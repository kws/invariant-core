"""Tests for AsyncExecutor."""

import asyncio

import pytest
from invariant import AsyncExecutor, Node, SubGraphNode, SwitchNode, ref
from invariant.invocation import invoke_op


class SleepingScheduler:
    """Async test scheduler that exposes concurrent invocations."""

    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def invoke(self, request):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return invoke_op(request.op, request.op_name, request.manifest)
        finally:
            self.active -= 1


def test_async_execute_simple_graph(registry, store):
    """AsyncExecutor executes a simple graph."""
    registry.register("identity", lambda value: value)
    graph = {
        "a": Node(op_name="identity", params={"value": "hello"}, deps=[]),
        "b": Node(op_name="identity", params={"value": ref("a")}, deps=["a"]),
    }

    results = asyncio.run(AsyncExecutor(registry, store).execute(graph, ["b"]))

    assert results == {"b": "hello"}


def test_async_execute_with_cache_hit(registry, caching_store):
    """AsyncExecutor uses the store and skips scheduler invocation on hits."""
    scheduler = SleepingScheduler(delay=0)
    registry.register("inc", lambda value: value + 1)
    graph = {"a": Node(op_name="inc", params={"value": 5}, deps=[])}
    executor = AsyncExecutor(registry, caching_store, scheduler=scheduler)

    assert asyncio.run(executor.execute(graph, ["a"])) == {"a": 6}
    assert asyncio.run(executor.execute(graph, ["a"])) == {"a": 6}

    assert scheduler.calls == 1
    assert caching_store.stats.hits == 1


def test_async_execute_cache_false_cascades(registry, caching_store):
    """cache=False bypasses cache for transitive downstream nodes."""
    calls = {"source": 0, "sink": 0}

    def source() -> int:
        calls["source"] += 1
        return 2

    def sink(value: int) -> int:
        calls["sink"] += 1
        return value * 3

    registry.register("source", source)
    registry.register("sink", sink)
    graph = {
        "source": Node(op_name="source", params={}, deps=[], cache=False),
        "sink": Node(op_name="sink", params={"value": ref("source")}, deps=["source"]),
    }
    executor = AsyncExecutor(registry, caching_store)

    assert asyncio.run(executor.execute(graph, ["sink"])) == {"sink": 6}
    assert asyncio.run(executor.execute(graph, ["sink"])) == {"sink": 6}

    assert calls == {"source": 2, "sink": 2}
    assert caching_store.stats.puts == 0


def test_async_execute_missing_op(registry, store):
    """AsyncExecutor reports missing ops like the sync executor."""
    graph = {"a": Node(op_name="missing", params={}, deps=[])}

    with pytest.raises(ValueError, match="unregistered op"):
        asyncio.run(AsyncExecutor(registry, store).execute(graph, ["a"]))


def test_async_switch_prunes_inactive_branch(registry, store):
    """Inactive switch branches are not validated or executed."""
    registry.register("identity", lambda value: value)
    graph = {
        "left": Node(op_name="identity", params={"value": "left"}, deps=[]),
        "right": Node(
            op_name="missing_op",
            params={"value": ref("missing_context")},
            deps=["missing_context"],
        ),
        "out": SwitchNode(
            selector=ref("choice"),
            deps=["choice"],
            cases={"left": "left", "right": "right"},
        ),
    }

    assert asyncio.run(
        AsyncExecutor(registry, store).execute(
            graph,
            ["out"],
            context={"choice": "left"},
        )
    ) == {"out": "left"}


def test_async_subgraph_context_and_ephemeral_propagation(registry, caching_store):
    """Subgraphs receive parent context and preserve cache=False propagation."""
    calls = {"source": 0, "inner": 0}

    def source() -> int:
        calls["source"] += 1
        return 4

    def double(value: int) -> int:
        calls["inner"] += 1
        return value * 2

    registry.register("source", source)
    registry.register("double", double)
    inner = {
        "out": Node(op_name="double", params={"value": ref("x")}, deps=["x"]),
    }
    graph = {
        "source": Node(op_name="source", params={}, deps=[], cache=False),
        "sub": SubGraphNode(
            params={"x": ref("source")},
            deps=["source"],
            graph=inner,
            output="out",
        ),
    }
    executor = AsyncExecutor(registry, caching_store)

    assert asyncio.run(executor.execute(graph, ["sub"])) == {"sub": 8}
    assert asyncio.run(executor.execute(graph, ["sub"])) == {"sub": 8}

    assert calls == {"source": 2, "inner": 2}
    assert caching_store.stats.puts == 0


def test_async_independent_ready_nodes_can_run_concurrently(registry, store):
    """Independent nodes can be in-flight at the same time."""
    scheduler = SleepingScheduler()
    registry.register("identity", lambda value: value)
    graph = {
        "a": Node(op_name="identity", params={"value": "a"}, deps=[]),
        "b": Node(op_name="identity", params={"value": "b"}, deps=[]),
    }

    assert asyncio.run(
        AsyncExecutor(registry, store, scheduler=scheduler).execute(graph, ["a", "b"])
    ) == {"a": "a", "b": "b"}

    assert scheduler.max_active == 2


def test_async_singleflight_cache_miss_invokes_once(registry, caching_store):
    """Concurrent identical cache misses share one scheduler invocation."""
    scheduler = SleepingScheduler()
    registry.register("identity", lambda value: value)
    graph = {
        "a": Node(op_name="identity", params={"value": "same"}, deps=[]),
        "b": Node(op_name="identity", params={"value": "same"}, deps=[]),
    }

    assert asyncio.run(
        AsyncExecutor(registry, caching_store, scheduler=scheduler).execute(
            graph,
            ["a", "b"],
        )
    ) == {"a": "same", "b": "same"}

    assert scheduler.calls == 1
    assert caching_store.stats.puts == 1
