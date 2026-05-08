"""Invocation schedulers for async execution."""

import asyncio
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from invariant.invocation import invoke_op
from invariant.registry import OpRegistry, import_implementation_ref
from invariant.store.codec import deserialize, serialize
from invariant.traits import OpTrait


@dataclass(frozen=True)
class InvocationRequest:
    """A scheduler-facing operation invocation request."""

    op_name: str
    op: Callable[..., Any]
    manifest: dict[str, Any]
    traits: frozenset[str]
    implementation_ref: str | None = None
    cache_key: tuple[str, str] | None = None


class InvocationScheduler(Protocol):
    """Protocol implemented by local and remote invocation schedulers."""

    async def invoke(self, request: InvocationRequest) -> Any:
        """Invoke an operation and return its artifact."""
        ...


class InlineScheduler:
    """Invoke operations directly on the event loop thread."""

    async def invoke(self, request: InvocationRequest) -> Any:
        """Invoke an operation inline."""
        return invoke_op(request.op, request.op_name, request.manifest)


class ThreadPoolScheduler:
    """Invoke operations in a thread pool."""

    def __init__(
        self,
        max_workers: int | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        if max_workers is not None and executor is not None:
            raise ValueError("max_workers cannot be set when executor is provided")
        self._executor = executor or ThreadPoolExecutor(max_workers=max_workers)
        self._owns_executor = executor is None

    async def invoke(self, request: InvocationRequest) -> Any:
        """Invoke an operation in the thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            invoke_op,
            request.op,
            request.op_name,
            request.manifest,
        )

    async def aclose(self) -> None:
        """Shut down the owned thread pool."""
        if self._owns_executor:
            self._executor.shutdown(wait=True)


class ProcessPoolScheduler:
    """Invoke worker-resolvable operations in a process pool."""

    def __init__(
        self,
        max_workers: int | None = None,
        executor: ProcessPoolExecutor | None = None,
    ) -> None:
        if max_workers is not None and executor is not None:
            raise ValueError("max_workers cannot be set when executor is provided")
        self._executor = executor or ProcessPoolExecutor(max_workers=max_workers)
        self._owns_executor = executor is None

    async def invoke(self, request: InvocationRequest) -> Any:
        """Invoke an operation through an Invariant codec process boundary."""
        if not request.implementation_ref:
            raise ValueError(
                f"Op '{request.op_name}' cannot run in a process because it has "
                "no worker-resolvable implementation_ref"
            )

        manifest_payload = serialize(request.manifest)
        loop = asyncio.get_running_loop()
        artifact_payload = await loop.run_in_executor(
            self._executor,
            _process_worker_invoke,
            request.op_name,
            request.implementation_ref,
            manifest_payload,
        )
        return deserialize(artifact_payload)

    async def aclose(self) -> None:
        """Shut down the owned process pool."""
        if self._owns_executor:
            self._executor.shutdown(wait=True)


class RoutingScheduler:
    """Route invocations to local schedulers according to traits."""

    def __init__(
        self,
        *,
        inline_scheduler: InvocationScheduler | None = None,
        thread_scheduler: InvocationScheduler | None = None,
        process_scheduler: InvocationScheduler | None = None,
    ) -> None:
        self.inline_scheduler = inline_scheduler or InlineScheduler()
        self.thread_scheduler = thread_scheduler
        self.process_scheduler = process_scheduler

    async def invoke(self, request: InvocationRequest) -> Any:
        """Route an invocation to the first configured matching scheduler."""
        if (
            OpTrait.PROCESS_SAFE.value in request.traits
            and self.process_scheduler is not None
        ):
            return await self.process_scheduler.invoke(request)

        if self.thread_scheduler is not None and (
            OpTrait.BLOCKING.value in request.traits
            or OpTrait.IO_BOUND.value in request.traits
        ):
            return await self.thread_scheduler.invoke(request)

        return await self.inline_scheduler.invoke(request)

    async def aclose(self) -> None:
        """Close child schedulers that expose ``aclose``."""
        for scheduler in (
            self.process_scheduler,
            self.thread_scheduler,
            self.inline_scheduler,
        ):
            close = getattr(scheduler, "aclose", None)
            if close is not None:
                await close()


def _process_worker_invoke(
    op_name: str,
    implementation_ref: str,
    manifest_payload: bytes,
) -> bytes:
    """Process worker entrypoint.

    The parent sends only simple strings and Invariant codec bytes. The worker
    resolves the exact callable locally, invokes it, and returns codec bytes.
    """
    registry = OpRegistry()
    registry.clear()
    registry.auto_discover()

    if registry.has(op_name):
        binding = registry.get_binding(op_name)
        if binding.implementation_ref != implementation_ref:
            raise ValueError(
                f"Worker discovered op '{op_name}' as "
                f"{binding.implementation_ref!r}, but request requires "
                f"{implementation_ref!r}"
            )
        op = binding.op
    else:
        op = import_implementation_ref(implementation_ref)
        registry.register(
            op_name,
            op,
            implementation_ref=implementation_ref,
        )

    manifest = deserialize(manifest_payload)
    artifact = invoke_op(op, op_name, manifest)
    return serialize(artifact)


__all__ = [
    "InlineScheduler",
    "InvocationRequest",
    "InvocationScheduler",
    "ProcessPoolScheduler",
    "RoutingScheduler",
    "ThreadPoolScheduler",
]
