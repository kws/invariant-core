"""Tests for invocation schedulers."""

import asyncio
from decimal import Decimal

import pytest
from invariant import (
    AsyncExecutor,
    InvocationRequest,
    Node,
    OpTrait,
    ProcessPoolScheduler,
    RoutingScheduler,
)

from . import process_ops


class NamedScheduler:
    """Test scheduler that records routed invocations."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def invoke(self, request):
        self.calls += 1
        return self.name


def _request(traits):
    return InvocationRequest(
        op_name="test",
        op=lambda: "inline",
        manifest={},
        traits=frozenset(traits),
    )


def test_routing_scheduler_prefers_process_for_process_safe_ops():
    """process-safe ops use the configured process scheduler first."""
    inline = NamedScheduler("inline")
    thread = NamedScheduler("thread")
    process = NamedScheduler("process")
    router = RoutingScheduler(
        inline_scheduler=inline,
        thread_scheduler=thread,
        process_scheduler=process,
    )

    result = asyncio.run(
        router.invoke(_request({OpTrait.PROCESS_SAFE.value, OpTrait.BLOCKING.value}))
    )

    assert result == "process"
    assert process.calls == 1
    assert thread.calls == 0
    assert inline.calls == 0


def test_routing_scheduler_uses_thread_for_blocking_ops():
    """blocking/io-bound ops use the configured thread scheduler."""
    inline = NamedScheduler("inline")
    thread = NamedScheduler("thread")
    router = RoutingScheduler(inline_scheduler=inline, thread_scheduler=thread)

    result = asyncio.run(router.invoke(_request({OpTrait.IO_BOUND.value})))

    assert result == "thread"
    assert thread.calls == 1
    assert inline.calls == 0


def test_routing_scheduler_defaults_to_inline():
    """Unmatched traits use inline scheduling."""
    inline = NamedScheduler("inline")
    router = RoutingScheduler(inline_scheduler=inline)

    result = asyncio.run(router.invoke(_request(set())))

    assert result == "inline"
    assert inline.calls == 1


def test_process_scheduler_executes_importable_op_through_ref(registry, store):
    """Process scheduler uses implementation_ref rather than parent callable."""

    def parent_callable(value: int) -> int:
        raise AssertionError("parent callable must not run in the worker")

    registry.register(
        "tests:add_one",
        parent_callable,
        traits={OpTrait.PROCESS_SAFE},
        implementation_ref="tests.process_ops:add_one",
    )
    graph = {"out": Node(op_name="tests:add_one", params={"value": 41}, deps=[])}

    async def run():
        async with AsyncExecutor(
            registry,
            store,
            scheduler=ProcessPoolScheduler(max_workers=1),
        ) as executor:
            return await executor.execute(graph, ["out"])

    assert asyncio.run(run()) == {"out": 42}


def test_process_scheduler_codec_roundtrips_manifest_and_result(registry, store):
    """Process scheduler supports codec-serialized Decimal manifests/results."""
    registry.register(
        "tests:add_decimal",
        process_ops.add_decimal,
        traits={OpTrait.PROCESS_SAFE},
    )
    graph = {
        "out": Node(
            op_name="tests:add_decimal",
            params={"value": Decimal("2.50")},
            deps=[],
        )
    }

    async def run():
        async with AsyncExecutor(
            registry,
            store,
            scheduler=ProcessPoolScheduler(max_workers=1),
        ) as executor:
            return await executor.execute(graph, ["out"])

    assert asyncio.run(run()) == {"out": Decimal("3.75")}


def test_process_scheduler_requires_implementation_ref(registry, store):
    """Process execution fails for ops without a worker-resolvable ref."""
    registry.register(
        "local",
        lambda value: value,
        traits={OpTrait.PROCESS_SAFE},
    )
    graph = {"out": Node(op_name="local", params={"value": 1}, deps=[])}

    async def run():
        async with AsyncExecutor(
            registry,
            store,
            scheduler=ProcessPoolScheduler(max_workers=1),
        ) as executor:
            return await executor.execute(graph, ["out"])

    with pytest.raises(ValueError, match="implementation_ref"):
        asyncio.run(run())


def test_process_scheduler_rejects_conflicting_worker_binding(registry, store):
    """A worker-discovered op_name cannot clobber the requested implementation."""
    registry.register(
        "stdlib:add",
        process_ops.add_one,
        traits={OpTrait.PROCESS_SAFE},
        implementation_ref="tests.process_ops:add_one",
    )
    graph = {"out": Node(op_name="stdlib:add", params={"value": 1}, deps=[])}

    async def run():
        async with AsyncExecutor(
            registry,
            store,
            scheduler=ProcessPoolScheduler(max_workers=1),
        ) as executor:
            return await executor.execute(graph, ["out"])

    with pytest.raises(ValueError, match="Worker discovered op 'stdlib:add'"):
        asyncio.run(run())
