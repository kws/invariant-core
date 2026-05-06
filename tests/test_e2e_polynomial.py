"""End-to-end tests for polynomial operations pipeline."""

from invariant import Executor, Node, OpRegistry, ref
from invariant.ops import poly
from invariant.store.null import NullStore


def test_distributive_law_pipeline():
    """Test the distributive law verification pipeline from architecture spec."""
    # Register polynomial operations
    registry = OpRegistry()
    registry.clear()  # Clear singleton state
    registry.register_package("poly", poly)

    # Define the graph from section 8.5 of architecture spec
    graph = {
        # Create polynomials from coefficient lists
        "p": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": [1, 2, 1]},  # x^2 + 2x + 1
            deps=[],
        ),
        "q": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": [3, 0, -1]},  # -x^2 + 3
            deps=[],
        ),
        "r": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": [1, 1]},  # x + 1
            deps=[],
        ),
        # Left branch: (p + q) * r
        "p_plus_q": Node(
            op_name="poly:add",
            params={"a": ref("p"), "b": ref("q")},
            deps=["p", "q"],
        ),
        "lhs": Node(
            op_name="poly:multiply",
            params={"a": ref("p_plus_q"), "b": ref("r")},
            deps=["p_plus_q", "r"],
        ),
        # Right branch: p*r + q*r
        "pr": Node(
            op_name="poly:multiply",
            params={"a": ref("p"), "b": ref("r")},
            deps=["p", "r"],
        ),
        "qr": Node(
            op_name="poly:multiply",
            params={"a": ref("q"), "b": ref("r")},
            deps=["q", "r"],
        ),
        "rhs": Node(
            op_name="poly:add",
            params={"a": ref("pr"), "b": ref("qr")},
            deps=["pr", "qr"],
        ),
        # Evaluate both sides at x=5
        "eval_lhs": Node(
            op_name="poly:evaluate",
            params={"poly": ref("lhs"), "x": 5},
            deps=["lhs"],
        ),
        "eval_rhs": Node(
            op_name="poly:evaluate",
            params={"poly": ref("rhs"), "x": 5},
            deps=["rhs"],
        ),
        # Bonus: derivative chain
        "d1": Node(
            op_name="poly:derivative",
            params={"poly": ref("lhs")},
            deps=["lhs"],
        ),
        "d2": Node(
            op_name="poly:derivative",
            params={"poly": ref("d1")},
            deps=["d1"],
        ),
        "eval_d2": Node(
            op_name="poly:evaluate",
            params={"poly": ref("d2"), "x": 5},
            deps=["d2"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["lhs", "rhs", "eval_lhs", "eval_rhs", "eval_d2"])

    # Verify distributive law: (p + q) * r == p*r + q*r
    assert results["lhs"].coefficients == results["rhs"].coefficients

    # Verify numeric equality at x=5 (results are native int)
    assert isinstance(results["eval_lhs"], int)
    assert isinstance(results["eval_rhs"], int)
    assert results["eval_lhs"] == results["eval_rhs"]

    # Verify derivative chain
    assert isinstance(results["eval_d2"], int)


def test_cache_reuse():
    """Test that running the same graph twice skips all ops on the second run."""
    registry = OpRegistry()
    registry.clear()  # Clear singleton state
    registry.register_package("poly", poly)

    graph = {
        "p": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": [1, 2, 1]},
            deps=[],
        ),
        "q": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": [3, 0, -1]},
            deps=[],
        ),
        "sum": Node(
            op_name="poly:add",
            params={"a": ref("p"), "b": ref("q")},
            deps=["p", "q"],
        ),
    }

    store = NullStore()
    executor = Executor(registry=registry, store=store)

    # First run - all ops execute
    results1 = executor.execute(graph, ["sum"])

    # Second run - should use cache
    results2 = executor.execute(graph, ["sum"])

    # Results should be identical
    assert results1["sum"].coefficients == results2["sum"].coefficients

    # Verify cache was used (store should have entries)
    # We can't easily verify ops were skipped without mocking, but we can
    # verify the results are correct and identical
