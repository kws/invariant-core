"""Tests for NullStore."""

import pytest
from invariant.hashing import hash_value
from invariant.store.null import NullStore


class TestNullStore:
    """Tests for NullStore."""

    def test_exists_always_false(self):
        """NullStore.exists() always returns False."""
        store = NullStore()
        assert not store.exists("op", "a" * 64)
        assert not store.exists("op", hash_value("x"))

    def test_get_raises(self):
        """NullStore.get() raises KeyError (never called in practice)."""
        store = NullStore()
        with pytest.raises(KeyError):
            store.get("op", "a" * 64)

    def test_put_no_op(self):
        """NullStore.put() is a no-op; does not raise."""
        store = NullStore()
        store.put("op", hash_value("x"), "x")
        assert not store.exists("op", hash_value("x"))

    def test_executor_works_with_null_store(self):
        """Executor can run a graph with NullStore (no caching)."""
        from invariant import Executor, Node, OpRegistry
        from invariant.ops.stdlib import identity

        registry = OpRegistry()
        registry.clear()
        registry.register("stdlib:identity", identity)

        graph = {
            "a": Node(op_name="stdlib:identity", params={"value": 42}, deps=[]),
        }

        store = NullStore()
        executor = Executor(registry=registry, store=store)
        results = executor.execute(graph, ["a"])

        assert results["a"] == 42
