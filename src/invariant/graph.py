"""GraphResolver for parsing, validating, and sorting DAGs."""

from typing import TYPE_CHECKING

from invariant.node import Node, SubGraphNode, SwitchNode

if TYPE_CHECKING:
    from invariant.registry import OpRegistry

# Graph may contain regular nodes, subgraph nodes, or lazy switch nodes.
GraphVertex = Node | SubGraphNode | SwitchNode
Graph = dict[str, GraphVertex]


def _switch_targets(node: SwitchNode) -> list[str]:
    """Return switch branch targets in deterministic order."""
    targets = [node.cases[key] for key in sorted(node.cases)]
    if node.default is not None:
        targets.append(node.default)
    return targets


def _graph_deps(node: GraphVertex) -> list[str]:
    """Return declared dependency edges for a vertex."""
    return list(node.deps)


class GraphResolver:
    """Responsible for parsing graph definitions and ensuring valid DAGs.

    Handles:
    - Cycle detection
    - Validation (missing dependencies, missing ops)
    - Topological sorting
    """

    def __init__(self, registry: "OpRegistry | None" = None) -> None:
        """Initialize GraphResolver.

        Args:
            registry: Optional OpRegistry for validating that ops exist.
                     If None, op validation is skipped.
        """
        self.registry = registry

    def validate(self, graph: Graph, context_keys: set[str] | None = None) -> None:
        """Validate a graph definition.

        Checks:
        - All node dependencies exist in the graph or in context
        - All switch branch targets exist in the graph
        - All referenced ops are registered (if registry provided; Node only)
        - No cycles exist across declared dependencies

        Args:
            graph: Dictionary mapping node IDs to graph vertices.
            context_keys: Optional set of external dependency keys (from context).
                         Dependencies not in the graph are allowed if they are in
                         context.

        Raises:
            ValueError: If validation fails (missing deps, missing ops, cycles).
        """
        # Check all dependencies exist
        node_ids = set(graph.keys())
        context_keys = context_keys or set()
        for node_id, node in graph.items():
            for dep in node.deps:
                if dep not in node_ids and dep not in context_keys:
                    raise ValueError(
                        f"Node '{node_id}' has dependency '{dep}' that doesn't "
                        "exist in graph "
                        f"or context. Available: graph={sorted(node_ids)}, "
                        f"context={sorted(context_keys)}"
                    )
            if isinstance(node, SwitchNode):
                for target in _switch_targets(node):
                    if target not in node_ids:
                        raise ValueError(
                            f"SwitchNode '{node_id}' targets '{target}' which "
                            f"doesn't exist in graph. Available: {sorted(node_ids)}"
                        )

        # Check all ops are registered (if registry provided); only Node has op_name
        if self.registry:
            for node_id, node in graph.items():
                if isinstance(node, Node) and not self.registry.has(node.op_name):
                    raise ValueError(
                        f"Node '{node_id}' references unregistered op "
                        f"'{node.op_name}'"
                    )

        # Check for cycles (excluding context dependencies)
        if self._has_cycle(graph, context_keys=context_keys):
            raise ValueError("Graph contains cycles")

    def _has_cycle(self, graph: Graph, context_keys: set[str] | None = None) -> bool:
        """Detect cycles in the graph using DFS.

        Args:
            graph: Dictionary mapping node IDs to graph vertices.
            context_keys: Optional set of external dependency keys (from context).
                         These are excluded from cycle detection.

        Returns:
            True if cycle exists, False otherwise.
        """
        node_ids = set(graph.keys())
        WHITE = 0
        GRAY = 1
        BLACK = 2

        color: dict[str, int] = {node_id: WHITE for node_id in node_ids}

        def dfs(node_id: str) -> bool:
            """DFS helper that returns True if cycle found."""
            if node_id not in node_ids:
                return False
            if color[node_id] == GRAY:
                return True
            if color[node_id] == BLACK:
                return False

            color[node_id] = GRAY
            node = graph[node_id]
            for dep in _graph_deps(node):
                # Only check dependencies that are in the graph (not context)
                if dep in node_ids and dfs(dep):
                    return True

            color[node_id] = BLACK
            return False

        # Check all nodes (handles disconnected components)
        return any(color[node_id] == WHITE and dfs(node_id) for node_id in node_ids)

    def topological_sort(
        self, graph: Graph, context_keys: set[str] | None = None
    ) -> list[str]:
        """Topologically sort the graph's declared dependency edges using DFS.

        Args:
            graph: Dictionary mapping node IDs to graph vertices.
            context_keys: Optional set of external dependency keys (from context).
                         These are excluded from topological sorting.

        Returns:
            List of node IDs in topological order (dependencies before dependents).

        Raises:
            ValueError: If graph contains cycles.
        """
        node_ids = set(graph.keys())
        color: dict[str, int] = {node_id: 0 for node_id in node_ids}
        result: list[str] = []

        def visit(node_id: str) -> None:
            if node_id not in node_ids:
                return
            if color[node_id] == 1:
                raise ValueError("Graph contains cycles (topological sort impossible)")
            if color[node_id] == 2:
                return

            color[node_id] = 1
            for dep in _graph_deps(graph[node_id]):
                if dep in node_ids:
                    visit(dep)
            color[node_id] = 2
            result.append(node_id)

        for node_id in sorted(node_ids):
            visit(node_id)

        return result

    def resolve(self, graph: Graph, context_keys: set[str] | None = None) -> list[str]:
        """Validate and topologically sort a graph.

        Convenience method that validates then sorts.

        Args:
            graph: Dictionary mapping node IDs to graph vertices.
            context_keys: Optional set of external dependency keys (from context).

        Returns:
            List of node IDs in topological order.

        Raises:
            ValueError: If validation fails or cycles exist.
        """
        self.validate(graph, context_keys=context_keys)
        return self.topological_sort(graph, context_keys=context_keys)
