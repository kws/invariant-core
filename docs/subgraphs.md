# Subgraphs: Reusable DAG Fragments

A **subgraph** is a DAG fragment that appears as a single vertex in a parent graph. Instead of merging many internal nodes into the parent, the parent sees one node with dependencies and one output artifact. This keeps graphs composable, allows reuse of complex pipelines, and preserves fine-grained caching because internal ops still run against the same ArtifactStore.

Subgraphs are a core Invariant concept and are not domain-specific. Child projects (e.g. Invariant GFX) use them for bundled recipes (effects, pipelines); see their documentation for domain-specific use.

## 1. The SubGraphNode Model

A **SubGraphNode** is a node-like construct that:

* **Has `deps` and `params`** — just like a regular `Node`. Upstream artifacts (and any other inputs) are declared as dependencies.
* **Carries an internal graph** and an **`output: str`** — the node ID of the internal node whose artifact is the subgraph's result. There is no `op_name`; execution runs the internal graph instead of invoking a single op.
* **Produces one artifact** — the output of the designated internal node.
* **Hides its internals** — the parent graph never sees or references internal node IDs. No naming convention or prefix is needed, because internal nodes are never merged into the parent namespace.

From the parent graph's perspective, a SubGraphNode is indistinguishable from a regular Node: it has an ID, it declares deps, and it produces one artifact that downstream nodes reference via `ref(node_id)`.

This `SubGraphNode.output` is the subgraph's required internal output selector.
It is separate from the optional top-level serialized graph document `output`
field, which is only default output metadata for document/CLI/component callers.

## 2. Execution Semantics

When demand execution reaches a SubGraphNode, it:

1. **Resolves** the SubGraphNode's params normally (ref/cel/expressions) using the parent's `artifacts_by_node`.
2. **Executes** the internal graph by calling `executor.execute(node.graph, [node.output], context=resolved_params)` — the same Executor instance, same registry, same ArtifactStore. Resolved params are passed as context so internal nodes can reference upstream artifacts (and any other inputs) by the same dependency names the subgraph builder used.
3. **Returns** the internal `output` node's artifact as this vertex's artifact, storing it in `artifacts_by_node[node_id]` for downstream nodes.

Internal nodes are never part of the parent graph's dict. They run in an isolated execution context; only the final output artifact is visible to the parent.

## 3. Shared Caching

All subgraph executions use the **same ArtifactStore** as the parent graph. Each internal op is cached by `(op_name, digest)` in that store. Therefore:

* If two different SubGraphNodes both run the same op on the same upstream artifact, the second execution gets a **cache hit** — the store already has the artifact for that digest.
* If the same subgraph recipe is used in multiple places with the same inputs and params, internal nodes are deduplicated across those subgraph runs.
* No SubGraphNode-level caching is required; fine-grained op caching is sufficient.

This guarantees that identical work is never repeated, whether it occurs inside one subgraph, across multiple subgraphs, or in a mix of subgraphs and regular nodes.

## 4. Usage Example

A subgraph is typically produced by a builder function that returns a `SubGraphNode`. The parent graph assigns it to a node ID and uses that ID like any other node.

```python
from invariant import Node, SubGraphNode, ref

def make_sum_subgraph(left_dep: str, right_dep: str) -> SubGraphNode:
    """Build a subgraph that adds two upstream values (e.g. from stdlib:identity or stdlib:add)."""
    inner = {
        "sum": Node(
            op_name="stdlib:add",
            params={"a": ref("left"), "b": ref("right")},
            deps=["left", "right"],
        ),
    }
    return SubGraphNode(
        params={"left": ref(left_dep), "right": ref(right_dep)},
        deps=[left_dep, right_dep],
        graph=inner,
        output="sum",
    )

# Parent graph: two source nodes, one subgraph, one consumer
graph = {
    "x": Node(op_name="stdlib:identity", params={"value": 5}, deps=[]),
    "y": Node(op_name="stdlib:identity", params={"value": 3}, deps=[]),
    "sum": make_sum_subgraph("x", "y"),
    "double": Node(
        op_name="stdlib:multiply",
        params={"a": ref("sum"), "b": 2},
        deps=["sum"],
    ),
}
```

Here `make_sum_subgraph("x", "y")` returns a `SubGraphNode` whose `deps` are `["x", "y"]`. The parent graph treats `"sum"` exactly like any other node — no prefix, no `graph.update()`, no exposed internal node IDs. Child projects can define builder functions that return SubGraphNode for domain-specific recipes (e.g. effect pipelines).

## 5. YAML Resource Grafting

Reusable subgraphs can also be authored as standalone canonical graph documents
and grafted into a YAML graph with the YAML-only `!subgraph` tag. This is an
authoring-time feature: the YAML loader resolves the resource through
JustMyResource, replaces the tag with an ordinary canonical `SubGraphNode`, and
then normal graph validation runs. Canonical JSON dumps and graph data URIs are
atomic; they contain the grafted graph, not the resource reference.

```yaml
graph:
  badge: !subgraph
    resource: components:badge
    deps: [canvas, title]
    params:
      canvas: !ref canvas
      text: !ref title
    output: final
```

See [serialization.md](serialization.md#94-resource-subgraph-grafting) for the
supported content types, optional dependency extras, output rules, and cycle
detection behavior.

## 6. Implementation (Invariant)

SubGraphNode is implemented in Invariant:

| Component | Behavior |
|:--|:--|
| **Node / SubGraphNode** | `SubGraphNode` is a frozen dataclass with `params`, `deps`, `graph`, and `output: str`. Ref-validation in params matches Node. The executor and resolver accept both types in a graph. |
| **Executor** | During demand resolution, branch on node type: for a `SubGraphNode`, build manifest, call `self.execute(node.graph, [node.output], context=manifest)`, and set `artifacts_by_node[node_id] = inner_results[node.output]`. No subgraph-level cache. |
| **GraphResolver** | `validate()` and `topological_sort()` accept `SubGraphNode` and `SwitchNode` alongside `Node`: validate declared deps, validate switch branch targets, skip op-registry checks for non-Node vertices, and defer internal graph execution to demand resolution. |

## 7. Related Documents

* [architecture.md](architecture.md) — Design philosophy, protocol specifications, reference pipeline
* [executor.md](executor.md) — Two-phase execution model, caching, SubGraphNode execution (§4.6)

Invariant GFX uses subgraphs for effect recipes (drop shadow, stroke, glow, etc.); see that project's documentation.
