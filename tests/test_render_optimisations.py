import asyncio
import gc
import weakref

import pytest
from invariant import AsyncExecutor, Executor, InlineScheduler, Node, ref
from invariant.expressions import _compiled_expression, _evaluate_cel
from invariant.invocation import invoke_op


def test_native_async_operations_overlap_but_respect_limit(registry, store):
    async def run():
        active = peak = 0

        async def load(value):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return value

        registry.register("load", load)
        graph = {str(i): Node("load", {"value": i}, []) for i in range(7)}
        results = await AsyncExecutor(
            registry, store, InlineScheduler(2), cache=False
        ).execute(graph, graph)
        assert results == {str(i): i for i in range(7)}
        assert peak == 2

    asyncio.run(run())


def test_cancelled_execution_does_not_cancel_another_waiter(registry, store):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return 42

        registry.register("load", load)
        executor = AsyncExecutor(registry, store)
        graph = {"a": Node("load", {}, [])}
        first = asyncio.create_task(executor.execute(graph, ["a"]))
        await entered.wait()
        second = asyncio.create_task(executor.execute(graph, ["a"]))
        for _ in range(20):
            if next(iter(executor._waiters.values())) == 2:
                break
            await asyncio.sleep(0)
        assert next(iter(executor._waiters.values())) == 2
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second == {"a": 42}
        assert calls == 1 and not executor._singleflight

    asyncio.run(run())


@pytest.mark.parametrize("cache", [False, True])
def test_cancel_drains_async_operations(registry, store, cache):
    async def run():
        entered, exited = asyncio.Event(), asyncio.Event()

        async def load():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

        registry.register("load", load)
        executor = AsyncExecutor(registry, store, cache=cache)
        task = asyncio.create_task(executor.execute({"a": Node("load", {}, [])}, ["a"]))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
        assert not executor._singleflight

    asyncio.run(run())


def test_concurrent_active_cycle_fails_instead_of_deadlocking(registry, store):
    async def run():
        registry.register("id", lambda value: value)
        graph = {
            "a": Node("id", {"value": ref("b")}, ["b"]),
            "b": Node("id", {"value": ref("a")}, ["a"]),
        }
        with pytest.raises(ValueError, match="cycles"):
            await asyncio.wait_for(
                AsyncExecutor(registry, store).execute(graph, ["a", "b"]), 0.5
            )

    asyncio.run(run())


def test_async_cache_bypass_never_hashes_or_stores(registry, store, monkeypatch):
    def forbidden(*args):
        raise AssertionError("unexpected cache access")

    monkeypatch.setattr("invariant.async_executor.hash_manifest", forbidden)
    monkeypatch.setattr(store, "put", forbidden)
    registry.register("id", lambda value: value)
    executor = AsyncExecutor(registry, store, cache=False)
    assert asyncio.run(
        executor.execute({"a": Node("id", {"value": 3}, [])}, ["a"])
    ) == {"a": 3}


def test_sync_executor_rejects_async_operation_without_leaking_coroutine(
    registry, store
):
    async def load():
        return 1

    registry.register("load", load)
    with pytest.raises(TypeError, match="AsyncExecutor"):
        Executor(registry, store).execute({"a": Node("load", {}, [])}, ["a"])


def test_compilation_reuses_syntax_but_not_activation():
    _compiled_expression.cache_clear()
    assert _evaluate_cel("x + 1", {"x": 2}) == 3
    assert _evaluate_cel("x + 1", {"x": 9}) == 10
    assert _compiled_expression.cache_info().hits == 1


def test_signature_cache_does_not_retain_closures():
    def make():
        captured = bytearray(1_000_000)

        def function():
            return len(captured)

        return function

    function = make()
    weak = weakref.ref(function)
    assert invoke_op(function, "size", {}) == 1_000_000
    del function
    gc.collect()
    assert weak() is None
