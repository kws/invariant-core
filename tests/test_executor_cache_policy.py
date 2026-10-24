from invariant import Executor, Node, ref
from invariant.store.null import NullStore


def test_cache_bypass_preserves_node_memoisation_and_never_hashes(monkeypatch):
    import invariant.executor as runtime

    calls = []

    class Registry:
        def has(self, name):
            return True

        def get(self, name):
            def op(**kwargs):
                calls.append(name)
                return sum(kwargs.values()) or 7

            return op

    def forbidden(*args):
        raise AssertionError("Cache bypass must not hash or access storage")

    store = NullStore()
    monkeypatch.setattr(runtime, "hash_manifest", forbidden)
    monkeypatch.setattr(store, "exists", forbidden)
    monkeypatch.setattr(store, "put", forbidden)
    graph = {
        "a": Node("value", {}, []),
        "b": Node("sum", {"x": ref("a"), "y": ref("a")}, ["a"]),
        "c": Node("sum", {"x": ref("a"), "y": ref("b")}, ["a", "b"]),
    }
    assert Executor(Registry(), store, cache=False).execute(graph, ["b", "c"]) == {
        "b": 14,
        "c": 21,
    }
    assert calls == ["value", "sum", "sum"]
