"""Graph vertex classes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from invariant.params import ref


def _collect_refs(value: Any) -> list[ref]:
    """Recursively collect all ref() markers from a value."""
    refs: list[ref] = []
    if isinstance(value, ref):
        refs.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            refs.extend(_collect_refs(v))
    elif isinstance(value, (list, tuple)):
        for item in value:
            refs.extend(_collect_refs(item))
    return refs


@dataclass(frozen=True)
class Node:
    """A vertex in the DAG defining what operation to perform.

    Attributes:
        op_name: The name of the operation to execute (must be registered).
        params: Static parameters for this node (dict of parameter name -> value).
                May contain ref() and cel() markers, and ${...} string interpolation.
        deps: List of node IDs that this node depends on (upstream dependencies).
        cache: When True (default), the node's result is cached unless it depends on
               an ephemeral upstream node. When False, the op is always executed, the
               result is never stored, and cache bypass cascades to downstream nodes.
    """

    op_name: str
    params: dict[str, Any]
    deps: list[str]
    cache: bool = True

    def __post_init__(self) -> None:
        """Validate node configuration."""
        if not self.op_name:
            raise ValueError("op_name cannot be empty")
        if not isinstance(self.params, dict):
            raise ValueError("params must be a dictionary")
        if not isinstance(self.deps, list):
            raise ValueError("deps must be a list")

        # Validate that all ref() markers reference declared dependencies
        self._validate_refs()

    def _validate_refs(self) -> None:
        """Validate that all ref() markers in params reference declared dependencies."""
        deps_set = set(self.deps)
        refs = _collect_refs(self.params)

        for ref_marker in refs:
            if ref_marker.dep not in deps_set:
                raise ValueError(
                    f"ref('{ref_marker.dep}') in params references undeclared "
                    "dependency. "
                    f"Declared deps: {self.deps}. "
                    f"Add '{ref_marker.dep}' to deps list."
                )


@dataclass(frozen=True)
class SubGraphNode:
    """A vertex that expands to an internal DAG at execution time.

    Has deps and params like Node, but carries an internal graph and output node ID
    instead of an op_name. The executor runs the internal graph with resolved params
    as context and returns the designated output node's artifact.
    """

    params: dict[str, Any]
    deps: list[str]
    graph: dict[str, Node | SubGraphNode | SwitchNode]
    output: str

    def __post_init__(self) -> None:
        """Validate SubGraphNode configuration."""
        if not isinstance(self.params, dict):
            raise ValueError("params must be a dictionary")
        if not isinstance(self.deps, list):
            raise ValueError("deps must be a list")
        if not isinstance(self.graph, dict):
            raise ValueError("graph must be a dictionary")
        if self.output not in self.graph:
            raise ValueError(
                f"output '{self.output}' must be a key in graph. "
                f"Graph keys: {list(self.graph.keys())}."
            )
        self._validate_refs()

    def _validate_refs(self) -> None:
        """Validate that all ref() markers in params reference declared dependencies."""
        deps_set = set(self.deps)
        refs = _collect_refs(self.params)
        for ref_marker in refs:
            if ref_marker.dep not in deps_set:
                raise ValueError(
                    f"ref('{ref_marker.dep}') in params references undeclared "
                    "dependency. "
                    f"Declared deps: {self.deps}. "
                    f"Add '{ref_marker.dep}' to deps list."
                )


@dataclass(frozen=True)
class SwitchNode:
    """A lazy conditional vertex that selects one graph-local branch target.

    The selector is resolved from declared deps. Its normalized value selects a
    node ID from cases, or default when no case matches. Branch targets are not
    dependencies: they are graph-local execution targets resolved by the graph
    executor.
    """

    selector: Any
    deps: list[str]
    cases: dict[str, str]
    default: str | None = None

    def __post_init__(self) -> None:
        """Validate switch configuration."""
        if not isinstance(self.deps, list):
            raise ValueError("deps must be a list")
        if not isinstance(self.cases, dict):
            raise ValueError("cases must be a dictionary")
        if not self.cases:
            raise ValueError("cases must not be empty")
        for key, target in self.cases.items():
            if not isinstance(key, str):
                raise ValueError("cases keys must be strings")
            if not isinstance(target, str) or not target:
                raise ValueError("cases values must be non-empty strings")
        if self.default is not None and (
            not isinstance(self.default, str) or not self.default
        ):
            raise ValueError("default must be a non-empty string when present")
        self._validate_refs()

    def _validate_refs(self) -> None:
        """Validate that all ref() markers in selector reference declared deps."""
        deps_set = set(self.deps)
        refs = _collect_refs(self.selector)
        for ref_marker in refs:
            if ref_marker.dep not in deps_set:
                raise ValueError(
                    f"ref('{ref_marker.dep}') in selector references undeclared "
                    f"dependency. Declared deps: {self.deps}. "
                    f"Add '{ref_marker.dep}' to deps list."
                )
