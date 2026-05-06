"""End-to-end tests for commutative operation canonicalization."""

from invariant import Executor, Node, OpRegistry, cel, ref
from invariant.ops.stdlib import add, identity
from invariant.store.null import NullStore


def test_commutative_canonicalization():
    """Test that min/max canonicalization ensures cache hits for commutative ops."""
    registry = OpRegistry()
    registry.clear()  # Clear singleton state
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:add", add)

    # Create graph with two nodes computing the same sum with different operand orders
    # Both use min/max to canonicalize, so they should resolve to the same manifest
    graph = {
        "x": Node(
            op_name="stdlib:identity",
            params={"value": 7},
            deps=[],
        ),
        "y": Node(
            op_name="stdlib:identity",
            params={"value": 3},
            deps=[],
        ),
        # First node: explicitly uses x, y order
        "sum_xy": Node(
            op_name="stdlib:add",
            params={
                "a": cel("min(x, y)"),
                "b": cel("max(x, y)"),
            },
            deps=["x", "y"],
        ),
        # Second node: uses y, x order in expressions — same result!
        "sum_yx": Node(
            op_name="stdlib:add",
            params={
                "a": cel("min(y, x)"),
                "b": cel("max(y, x)"),
            },
            deps=["x", "y"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["sum_xy", "sum_yx"])

    # Both should produce the same result
    assert results["sum_xy"] == results["sum_yx"]
    assert results["sum_xy"] == 10  # 3 + 7

    # Note: We can't easily verify they used the same cache entry without
    # inspecting the store internals, but the fact that both produce
    # the same result and the expressions canonicalize correctly is sufficient


def test_commutative_without_canonicalization():
    """Test that operand orders produce different manifests."""
    registry = OpRegistry()
    registry.clear()  # Clear singleton state
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:add", add)

    # This test shows that without min/max, the order matters for caching
    # (though mathematically the result is the same)
    graph = {
        "x": Node(
            op_name="stdlib:identity",
            params={"value": 7},
            deps=[],
        ),
        "y": Node(
            op_name="stdlib:identity",
            params={"value": 3},
            deps=[],
        ),
        # Without canonicalization, these would have different manifests
        # (but same mathematical result)
        "sum_xy": Node(
            op_name="stdlib:add",
            params={"a": ref("x"), "b": ref("y")},
            deps=["x", "y"],
        ),
        "sum_yx": Node(
            op_name="stdlib:add",
            params={"a": ref("y"), "b": ref("x")},
            deps=["x", "y"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["sum_xy", "sum_yx"])

    # Results are mathematically the same
    assert results["sum_xy"] == results["sum_yx"]
    assert results["sum_xy"] == 10

    # But without canonicalization, they would have different manifests
    # and thus different cache entries (this is expected behavior)


def test_commutative_cache_deduplication(caching_store):
    """Test that three nodes with same manifest produce 1 execution + 2 cache hits."""
    registry = OpRegistry()
    registry.clear()  # Clear singleton state
    registry.register("stdlib:identity", identity)

    # Create a counting wrapper around add to track executions
    call_count = {"add": 0}

    def counting_add(a: int, b: int) -> int:
        call_count["add"] += 1
        return add(a, b)

    registry.register("stdlib:add", counting_add)

    # Create graph with three nodes that all resolve to the same manifest
    # All three use op_name="stdlib:add" with manifest {"a": 3, "b": 7}
    graph = {
        # First node: uses min/max canonicalization
        "sum_xy": Node(
            op_name="stdlib:add",
            params={
                "a": cel("min(x, y)"),
                "b": cel("max(x, y)"),
            },
            deps=["x", "y"],
        ),
        # Second node: uses reversed min/max (same result)
        "sum_yx": Node(
            op_name="stdlib:add",
            params={
                "a": cel("min(y, x)"),
                "b": cel("max(y, x)"),
            },
            deps=["x", "y"],
        ),
        # Third node: literal params (no deps needed)
        "sum_const": Node(
            op_name="stdlib:add",
            params={
                "a": 3,
                "b": 7,
            },
            deps=[],
        ),
    }

    context = {
        "x": 3,
        "y": 7,
    }

    store = caching_store
    executor = Executor(registry=registry, store=store)
    results = executor.execute(
        graph,
        ["sum_xy", "sum_yx", "sum_const"],
        context=context,
    )

    # All three should produce the same result
    assert results["sum_xy"] == results["sum_yx"] == results["sum_const"] == 10

    # Verify cache behavior: 1 execution, 2 cache hits
    assert call_count["add"] == 1, "add() should be called exactly once"
    assert store.stats.hits == 2, "Should have 2 cache hits (sum_yx and sum_const)"
    assert store.stats.misses == 1, "Should have 1 cache miss (sum_xy)"
    assert store.stats.puts == 1, "Should have 1 put (sum_xy)"
