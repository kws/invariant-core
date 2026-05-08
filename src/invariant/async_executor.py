"""Async executor: scheduler-driven alternative to the safe sync Executor."""

import asyncio
from collections.abc import Iterable
from typing import Any

from invariant.cacheable import is_cacheable
from invariant.executor import Executor
from invariant.graph import Graph
from invariant.hashing import hash_manifest
from invariant.node import Node, SubGraphNode, SwitchNode
from invariant.scheduler import InlineScheduler, InvocationRequest, InvocationScheduler


class AsyncExecutor(Executor):
    """Async runtime engine for executing DAGs.

    The async executor preserves the synchronous executor's graph and cache
    semantics, while delegating operation placement to an InvocationScheduler.
    """

    def __init__(
        self,
        registry,
        store,
        scheduler: InvocationScheduler | None = None,
    ) -> None:
        """Initialize AsyncExecutor."""
        super().__init__(registry, store)
        self.scheduler = scheduler or InlineScheduler()
        self._singleflight: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._singleflight_lock = asyncio.Lock()

    async def __aenter__(self) -> "AsyncExecutor":
        """Enter an async context manager."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close scheduler resources on context exit."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close scheduler resources when supported."""
        close = getattr(self.scheduler, "aclose", None)
        if close is not None:
            await close()

    async def execute(
        self,
        graph: Graph,
        outputs: Iterable[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute requested graph outputs and return their artifacts."""
        output_ids = self._normalize_outputs(outputs, graph)
        artifacts_by_node, _ = await self._execute_requested_outputs_async(
            graph,
            output_ids,
            context=context,
        )
        return {output: artifacts_by_node[output] for output in output_ids}

    async def _execute_requested_outputs_async(
        self,
        graph: Graph,
        outputs: tuple[str, ...],
        context: dict[str, Any] | None = None,
        uncacheable_context_keys: set[str] | None = None,
    ) -> tuple[dict[str, Any], set[str]]:
        """Demand-execute active paths for requested graph outputs."""
        context = context or {}
        uncacheable_nodes = set(uncacheable_context_keys or set())
        artifacts_by_node: dict[str, Any] = {}
        node_tasks: dict[str, asyncio.Task[Any]] = {}
        waiting_for: dict[str, set[str]] = {}

        def check_wait_cycle(waiter: str, target: str) -> None:
            stack = [target]
            seen: set[str] = set()
            while stack:
                current = stack.pop()
                if current == waiter:
                    raise ValueError(
                        "Graph contains cycles on active path: "
                        f"{waiter} -> {target} -> {waiter}"
                    )
                if current in seen:
                    continue
                seen.add(current)
                stack.extend(waiting_for.get(current, ()))

        async def await_existing_task(
            node_id: str,
            task: asyncio.Task[Any],
            waiter: str | None,
        ) -> Any:
            if task.done() or waiter is None:
                return await task
            check_wait_cycle(waiter, node_id)
            waiting_for.setdefault(waiter, set()).add(node_id)
            try:
                return await task
            finally:
                waiters = waiting_for.get(waiter)
                if waiters is not None:
                    waiters.discard(node_id)
                    if not waiters:
                        waiting_for.pop(waiter, None)

        async def resolve_artifact(
            node_id: str,
            path: tuple[str, ...] = (),
            waiter: str | None = None,
        ) -> Any:
            if node_id in artifacts_by_node:
                return artifacts_by_node[node_id]

            if node_id in path:
                cycle = " -> ".join([*path, node_id])
                raise ValueError(f"Graph contains cycles on active path: {cycle}")

            if node_id in graph:
                existing = node_tasks.get(node_id)
                if existing is not None:
                    return await await_existing_task(node_id, existing, waiter)

                task = asyncio.create_task(run_graph_node(node_id, (*path, node_id)))
                node_tasks[node_id] = task
                return await task

            if node_id in context:
                value = context[node_id]
                if not is_cacheable(value):
                    raise ValueError(
                        f"Context value for '{node_id}' is not cacheable, "
                        f"got {type(value)}"
                    )
                artifacts_by_node[node_id] = value
                return value

            raise ValueError(
                f"Node depends on '{node_id}' but it doesn't exist in graph "
                f"or context. Available: graph={sorted(graph)}, "
                f"context={sorted(context)}"
            )

        async def run_graph_node(node_id: str, path: tuple[str, ...]) -> Any:
            node = graph[node_id]
            await asyncio.gather(
                *(
                    resolve_artifact(dep_id, path, waiter=node_id)
                    for dep_id in node.deps
                )
            )

            depends_on_uncacheable = any(
                dep_id in uncacheable_nodes for dep_id in node.deps
            )

            if isinstance(node, SwitchNode):
                self._validate_switch_targets(node, node_id, graph)
                target_id = self._select_switch_target(
                    node,
                    node_id,
                    artifacts_by_node,
                )
                if target_id not in graph:
                    raise ValueError(
                        f"SwitchNode '{node_id}' targets '{target_id}' "
                        "which doesn't exist in graph"
                    )
                await resolve_artifact(target_id, path, waiter=node_id)
                artifacts_by_node[node_id] = artifacts_by_node[target_id]
                if depends_on_uncacheable or target_id in uncacheable_nodes:
                    uncacheable_nodes.add(node_id)
            elif isinstance(node, SubGraphNode):
                manifest = self._build_manifest(
                    node,
                    node_id,
                    graph,
                    artifacts_by_node,
                )
                inner_uncacheable_context_keys = (
                    set(manifest.keys()) if depends_on_uncacheable else set()
                )
                inner_results, inner_uncacheable_nodes = (
                    await self._execute_requested_outputs_async(
                        node.graph,
                        (node.output,),
                        context=manifest,
                        uncacheable_context_keys=inner_uncacheable_context_keys,
                    )
                )
                artifacts_by_node[node_id] = inner_results[node.output]
                if depends_on_uncacheable or node.output in inner_uncacheable_nodes:
                    uncacheable_nodes.add(node_id)
            else:
                manifest = self._build_manifest(
                    node,
                    node_id,
                    graph,
                    artifacts_by_node,
                )
                artifact = await self._execute_node_async(
                    node,
                    node_id,
                    manifest,
                    depends_on_uncacheable,
                    uncacheable_nodes,
                )
                artifacts_by_node[node_id] = artifact

            return artifacts_by_node[node_id]

        try:
            await asyncio.gather(*(resolve_artifact(output) for output in outputs))
        except Exception:
            for task in node_tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*node_tasks.values(), return_exceptions=True)
            raise

        return artifacts_by_node, uncacheable_nodes

    async def _execute_node_async(
        self,
        node: Node,
        node_id: str,
        manifest: dict[str, Any],
        depends_on_uncacheable: bool,
        uncacheable_nodes: set[str],
    ) -> Any:
        """Execute one Node using scheduler-driven cache semantics."""
        if not self.registry.has(node.op_name):
            raise ValueError(
                f"Node '{node_id}' references unregistered op '{node.op_name}'"
            )

        binding = self.registry.get_binding(node.op_name)
        should_cache = node.cache and not depends_on_uncacheable
        if not should_cache:
            artifact = await self.scheduler.invoke(
                InvocationRequest(
                    op_name=node.op_name,
                    op=binding.op,
                    manifest=manifest,
                    traits=binding.traits,
                    implementation_ref=binding.implementation_ref,
                )
            )
            uncacheable_nodes.add(node_id)
            return artifact

        digest = hash_manifest(manifest)
        key = (node.op_name, digest)
        owner = False

        async with self._singleflight_lock:
            task = self._singleflight.get(key)
            if task is None:
                if self.store.exists(node.op_name, digest):
                    return self.store.get(node.op_name, digest)
                task = asyncio.create_task(
                    self._invoke_and_store(binding, manifest, key)
                )
                self._singleflight[key] = task
                owner = True

        try:
            return await task
        finally:
            if owner:
                async with self._singleflight_lock:
                    if self._singleflight.get(key) is task:
                        self._singleflight.pop(key, None)

    async def _invoke_and_store(self, binding, manifest, key: tuple[str, str]) -> Any:
        """Invoke an op through the scheduler and store the resulting artifact."""
        op_name, digest = key
        artifact = await self.scheduler.invoke(
            InvocationRequest(
                op_name=op_name,
                op=binding.op,
                manifest=manifest,
                traits=binding.traits,
                implementation_ref=binding.implementation_ref,
                cache_key=key,
            )
        )
        self.store.put(op_name, digest, artifact)
        return artifact


__all__ = ["AsyncExecutor"]
