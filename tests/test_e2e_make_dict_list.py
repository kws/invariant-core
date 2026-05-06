"""End-to-end tests for make_dict and make_list operations with ref/cel."""

from invariant import Executor, Node, OpRegistry, cel, ref
from invariant.ops.stdlib import identity, make_dict, make_list
from invariant.store.null import NullStore


def test_make_dict_with_ref_and_cel():
    """Test make_dict constructs a dict from ref() and cel() params."""
    registry = OpRegistry()
    registry.clear()  # Clear singleton state
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:make_dict", make_dict)

    context = {
        "bg": {"width": 144, "height": 72},
        "fg_color": "#ff0000",
    }

    graph = {
        "config": Node(
            op_name="stdlib:make_dict",
            params={
                "width": cel("bg.width"),
                "height": cel("bg.height"),
                "color": cel("fg_color"),
            },
            deps=["bg", "fg_color"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["config"], context=context)

    # Verify the constructed dict
    assert isinstance(results["config"], dict)
    assert results["config"]["width"] == 144
    assert results["config"]["height"] == 72
    assert results["config"]["color"] == "#ff0000"


def test_make_dict_with_mixed_literal_ref_cel():
    """Test make_dict with mixed literal, ref, and cel params."""
    registry = OpRegistry()
    registry.clear()
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:make_dict", make_dict)

    context = {
        "base_value": 100,
    }

    graph = {
        "value": Node(
            op_name="stdlib:identity",
            params={"value": 50},
            deps=[],
        ),
        "config": Node(
            op_name="stdlib:make_dict",
            params={
                "literal": "static",
                "from_ref": ref("value"),
                "from_cel": cel("base_value * 2"),
            },
            deps=["value", "base_value"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["config"], context=context)

    assert results["config"]["literal"] == "static"
    assert results["config"]["from_ref"] == 50
    assert results["config"]["from_cel"] == 200  # 100 * 2


def test_make_list_with_ref_and_cel():
    """Test make_list constructs a list from ref() and cel() items."""
    registry = OpRegistry()
    registry.clear()
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:make_list", make_list)

    context = {
        "c": 5,
    }

    graph = {
        "a": Node(
            op_name="stdlib:identity",
            params={"value": 1},
            deps=[],
        ),
        "b": Node(
            op_name="stdlib:identity",
            params={"value": 2},
            deps=[],
        ),
        "combined": Node(
            op_name="stdlib:make_list",
            params={"items": [ref("a"), ref("b"), cel("c + 1")]},
            deps=["a", "b", "c"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["combined"], context=context)

    # Verify the constructed list
    assert isinstance(results["combined"], list)
    assert results["combined"] == [1, 2, 6]  # [a, b, c+1] = [1, 2, 5+1]


def test_make_dict_caching(caching_store):
    """Test that make_dict caching works correctly."""
    registry = OpRegistry()
    registry.clear()
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:make_dict", make_dict)

    context = {
        "width": 144,
        "height": 72,
    }

    # Create two nodes with identical params (should cache)
    graph = {
        "config1": Node(
            op_name="stdlib:make_dict",
            params={
                "width": cel("width"),
                "height": cel("height"),
            },
            deps=["width", "height"],
        ),
        "config2": Node(
            op_name="stdlib:make_dict",
            params={
                "width": cel("width"),
                "height": cel("height"),
            },
            deps=["width", "height"],
        ),
    }

    store = caching_store
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["config1", "config2"], context=context)

    # Both should produce the same result
    assert results["config1"] == results["config2"]
    assert results["config1"] == {"width": 144, "height": 72}

    # Verify caching: 1 miss, 1 hit, 1 put
    assert store.stats.misses == 1, "Should have 1 cache miss (config1)"
    assert store.stats.hits == 1, "Should have 1 cache hit (config2)"
    assert store.stats.puts == 1, "Should have 1 put (config1)"


def test_make_list_caching(caching_store):
    """Test that make_list caching works correctly."""
    registry = OpRegistry()
    registry.clear()
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:make_list", make_list)

    context = {
        "x": 10,
    }

    # Create two nodes with identical params (should cache)
    graph = {
        "list1": Node(
            op_name="stdlib:make_list",
            params={"items": [cel("x"), cel("x * 2")]},
            deps=["x"],
        ),
        "list2": Node(
            op_name="stdlib:make_list",
            params={"items": [cel("x"), cel("x * 2")]},
            deps=["x"],
        ),
    }

    store = caching_store
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["list1", "list2"], context=context)

    # Both should produce the same result
    assert results["list1"] == results["list2"]
    assert results["list1"] == [10, 20]

    # Verify caching: 1 miss, 1 hit, 1 put
    assert store.stats.misses == 1, "Should have 1 cache miss (list1)"
    assert store.stats.hits == 1, "Should have 1 cache hit (list2)"
    assert store.stats.puts == 1, "Should have 1 put (list1)"


def test_make_dict_composition():
    """Test that downstream nodes can consume make_dict artifacts via ref/cel."""
    registry = OpRegistry()
    registry.clear()
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:make_dict", make_dict)

    graph = {
        "config": Node(
            op_name="stdlib:make_dict",
            params={
                "width": 144,
                "height": 72,
                "color": "red",
            },
            deps=[],
        ),
        "width": Node(
            op_name="stdlib:identity",
            params={"value": cel("config.width")},
            deps=["config"],
        ),
        "color": Node(
            op_name="stdlib:identity",
            params={"value": cel("config.color")},
            deps=["config"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["width", "color"])

    # Verify composition works
    assert results["width"] == 144
    assert results["color"] == "red"


def test_make_list_composition():
    """Test that downstream nodes can consume make_list artifacts via ref/cel."""
    registry = OpRegistry()
    registry.clear()
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:make_list", make_list)

    graph = {
        "items": Node(
            op_name="stdlib:make_list",
            params={"items": [1, 2, 3, 4, 5]},
            deps=[],
        ),
        "first": Node(
            op_name="stdlib:identity",
            params={"value": cel("items[0]")},
            deps=["items"],
        ),
        "last": Node(
            op_name="stdlib:identity",
            params={"value": cel("items[4]")},
            deps=["items"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["first", "last"])

    # Verify composition works
    assert results["first"] == 1
    assert results["last"] == 5
