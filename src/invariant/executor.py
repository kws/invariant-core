"""Executor: The runtime engine for executing DAGs."""

import inspect
from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from invariant.cacheable import is_cacheable
from invariant.expressions import resolve_params
from invariant.graph import Graph
from invariant.hashing import hash_manifest
from invariant.node import Node, SubGraphNode, SwitchNode

if TYPE_CHECKING:
    from invariant.registry import OpRegistry
    from invariant.store.base import ArtifactStore


class Executor:
    """Runtime engine for executing DAGs.

    Manages the two-phase execution:
    - Phase 1: Context Resolution (Graph -> Manifest)
    - Phase 2: Action Execution (Manifest -> Artifact)
    """

    def __init__(
        self,
        registry: "OpRegistry",
        store: "ArtifactStore",
    ) -> None:
        """Initialize Executor.

        Args:
            registry: OpRegistry for looking up operations.
            store: ArtifactStore for caching artifacts.
        """
        self.registry = registry
        self.store = store

    def execute(
        self,
        graph: Graph,
        outputs: Iterable[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute requested graph outputs and return their artifacts.

        Execution is demand-driven from the requested output roots. Unreachable
        vertices and inactive SwitchNode branches are not dependency-resolved,
        registry-validated, cache-checked, or executed.

        Args:
            graph: Dictionary mapping node IDs to graph vertices.
            outputs: Iterable of graph node IDs to produce.
            context: Optional dictionary of external dependencies (values not in graph).
                    These are injected as artifacts available to any node that declares
                    them in deps.

        Returns:
            Dictionary mapping requested output IDs to their resulting artifacts.

        Raises:
            ValueError: If requested outputs or active paths are invalid.
        """
        output_ids = self._normalize_outputs(outputs, graph)
        artifacts_by_node, _ = self._execute_requested_outputs(
            graph,
            output_ids,
            context=context,
        )
        return {output: artifacts_by_node[output] for output in output_ids}

    def _execute_requested_outputs(
        self,
        graph: Graph,
        outputs: tuple[str, ...],
        context: dict[str, Any] | None = None,
        uncacheable_context_keys: set[str] | None = None,
    ) -> tuple[dict[str, Any], set[str]]:
        """Demand-execute the active paths for requested graph outputs."""
        context = context or {}
        uncacheable_nodes = set(uncacheable_context_keys or set())
        artifacts_by_node: dict[str, Any] = {}
        visiting: list[str] = []
        visiting_set: set[str] = set()

        def resolve_artifact(node_id: str) -> Any:
            if node_id in artifacts_by_node:
                return artifacts_by_node[node_id]

            if node_id in graph:
                if node_id in visiting_set:
                    cycle = " -> ".join([*visiting, node_id])
                    raise ValueError(f"Graph contains cycles on active path: {cycle}")

                visiting.append(node_id)
                visiting_set.add(node_id)
                try:
                    node = graph[node_id]
                    for dep_id in node.deps:
                        resolve_artifact(dep_id)

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
                        resolve_artifact(target_id)
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
                            self._execute_requested_outputs(
                                node.graph,
                                (node.output,),
                                context=manifest,
                                uncacheable_context_keys=inner_uncacheable_context_keys,
                            )
                        )
                        artifacts_by_node[node_id] = inner_results[node.output]
                        if (
                            depends_on_uncacheable
                            or node.output in inner_uncacheable_nodes
                        ):
                            uncacheable_nodes.add(node_id)
                    else:
                        manifest = self._build_manifest(
                            node,
                            node_id,
                            graph,
                            artifacts_by_node,
                        )
                        artifact = self._execute_node(
                            node,
                            node_id,
                            manifest,
                            depends_on_uncacheable,
                            uncacheable_nodes,
                        )
                        artifacts_by_node[node_id] = artifact
                finally:
                    visiting.pop()
                    visiting_set.remove(node_id)

                return artifacts_by_node[node_id]

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

        for output in outputs:
            resolve_artifact(output)
        return artifacts_by_node, uncacheable_nodes

    def _normalize_outputs(
        self,
        outputs: Iterable[str],
        graph: Graph,
    ) -> tuple[str, ...]:
        """Validate and freeze requested output IDs."""
        if isinstance(outputs, (str, bytes)):
            raise ValueError(
                "outputs must be an iterable of node IDs, not str or bytes"
            )

        try:
            output_ids = tuple(outputs)
        except TypeError as exc:
            raise ValueError("outputs must be an iterable of node IDs") from exc

        if not output_ids:
            raise ValueError("outputs must not be empty")

        seen: set[str] = set()
        for output in output_ids:
            if not isinstance(output, str) or not output:
                raise ValueError("outputs must contain non-empty strings")
            if output in seen:
                raise ValueError(f"outputs contains duplicate node ID '{output}'")
            seen.add(output)
            if output not in graph:
                raise ValueError(
                    f"Output node '{output}' is not in graph. "
                    f"Available: {sorted(graph)}"
                )

        return output_ids

    def _validate_switch_targets(
        self,
        node: SwitchNode,
        node_id: str,
        graph: Graph,
    ) -> None:
        """Validate branch targets for an active SwitchNode."""
        targets = list(node.cases.values())
        if node.default is not None:
            targets.append(node.default)
        for target in targets:
            if target not in graph:
                raise ValueError(
                    f"SwitchNode '{node_id}' targets '{target}' which doesn't "
                    "exist in graph"
                )

    def _build_manifest(
        self,
        node: Node | SubGraphNode,
        node_id: str,
        graph: Graph,
        artifacts_by_node: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the input manifest for a node (Phase 1).

        The manifest is built entirely from resolved params. Dependencies are NOT
        injected into the manifest directly - they are only available for ref()/cel()
        resolution within params.

        Args:
            node: The node to build manifest for.
            node_id: The ID of the node.
            graph: The full graph (for reference).
            artifacts_by_node: Already computed artifacts for upstream nodes.

        Returns:
            The manifest dictionary mapping parameter names to resolved values.
        """
        # Collect dependency artifacts for ref()/cel() resolution
        dependencies: dict[str, Any] = {}
        for dep_id in node.deps:
            if dep_id not in artifacts_by_node:
                raise ValueError(
                    f"Node '{node_id}' depends on '{dep_id}' but artifact not found. "
                    "This should not happen after active dependency resolution."
                )
            dependencies[dep_id] = artifacts_by_node[dep_id]

        # Manifest = resolved params only. No dependency injection.
        # ref() and cel() markers in params are resolved using dependencies.
        return resolve_params(node.params, dependencies)

    def _resolve_selector(
        self,
        node: SwitchNode,
        node_id: str,
        artifacts_by_node: dict[str, Any],
    ) -> Any:
        """Resolve a SwitchNode selector from its declared deps."""
        dependencies: dict[str, Any] = {}
        for dep_id in node.deps:
            if dep_id not in artifacts_by_node:
                raise ValueError(
                    f"SwitchNode '{node_id}' depends on '{dep_id}' but artifact "
                    "not found"
                )
            dependencies[dep_id] = artifacts_by_node[dep_id]
        return resolve_params({"selector": node.selector}, dependencies)["selector"]

    def _select_switch_target(
        self,
        node: SwitchNode,
        node_id: str,
        artifacts_by_node: dict[str, Any],
    ) -> str:
        """Resolve a SwitchNode selector and return the chosen target node ID."""
        selector_value = self._resolve_selector(node, node_id, artifacts_by_node)
        case_key = self._normalize_switch_key(selector_value, node_id)
        if case_key in node.cases:
            return node.cases[case_key]
        if node.default is not None:
            return node.default
        available = ", ".join(sorted(node.cases))
        raise ValueError(
            f"SwitchNode '{node_id}' selector resolved to {case_key!r}, "
            f"which has no case. Available cases: {available}"
        )

    def _normalize_switch_key(self, value: Any, node_id: str) -> str:
        """Normalize selector results to switch case keys."""
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, Decimal):
            return str(value)
        raise ValueError(
            f"SwitchNode '{node_id}' selector returned unsupported "
            f"{type(value).__name__}; expected str, bool, null, int, or Decimal"
        )

    def _execute_node(
        self,
        node: Node,
        node_id: str,
        manifest: dict[str, Any],
        depends_on_uncacheable: bool,
        uncacheable_nodes: set[str],
    ) -> Any:
        """Execute one Node using the existing cache semantics."""
        if not self.registry.has(node.op_name):
            raise ValueError(
                f"Node '{node_id}' references unregistered op '{node.op_name}'"
            )

        should_cache = node.cache and not depends_on_uncacheable
        if not should_cache:
            op = self.registry.get(node.op_name)
            artifact = self._invoke_op(op, node.op_name, manifest)
            uncacheable_nodes.add(node_id)
            return artifact

        digest = hash_manifest(manifest)
        if self.store.exists(node.op_name, digest):
            return self.store.get(node.op_name, digest)

        op = self.registry.get(node.op_name)
        artifact = self._invoke_op(op, node.op_name, manifest)
        self.store.put(node.op_name, digest, artifact)
        return artifact

    def _invoke_op(self, op: Any, op_name: str, manifest: dict[str, Any]) -> Any:
        """Invoke an operation with kwargs dispatch and return validation.

        Args:
            op: The callable operation to invoke.
            op_name: The name of the operation (for error messages).
            manifest: The manifest dictionary mapping parameter names to values.

        Returns:
            The operation result (native type or ICacheable domain type).

        Raises:
            ValueError: If required parameters are missing.
            TypeError: If return value is not cacheable.
        """
        # Inspect function signature to map manifest keys to function parameters
        sig = inspect.signature(op)
        kwargs: dict[str, Any] = {}

        # Map manifest keys to function parameters by name
        for name, param in sig.parameters.items():
            if name in manifest:
                value = manifest[name]
                kwargs[name] = value
            elif param.default is not inspect.Parameter.empty:
                # Parameter has a default value, skip it
                pass
            elif param.kind == inspect.Parameter.VAR_KEYWORD:
                # Function accepts **kwargs, will handle below
                pass
            else:
                # Required parameter missing
                raise ValueError(f"Op '{op_name}': missing required parameter '{name}'")

        # If function has **kwargs, pass remaining manifest keys
        has_var_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if has_var_kwargs:
            for key, val in manifest.items():
                if key not in kwargs:
                    kwargs[key] = val

        # Invoke the operation
        result = op(**kwargs)

        # Validate return value is cacheable
        if not is_cacheable(result):
            raise TypeError(
                f"Op '{op_name}' returned {type(result).__name__}, "
                f"which is not a cacheable type"
            )

        # Return as-is (no wrapping needed)
        return result
