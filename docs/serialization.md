# Graph Serialization Specification

This document is the normative reference for Invariant's graph serialization format. It defines how to encode graphs (including `Node`, `SubGraphNode`, and `SwitchNode` vertices) and their parameters (including `ref()`, `cel()`, `Decimal`, tuples, and ICacheable domain types) as JSON for storage and transmission over the wire. JSON is the canonical deterministic wire format; YAML is also supported as a human-editable authoring format over the same model.

**Source of truth:** This document. Implementations must conform to this specification.

**Separation of concerns:** This format is distinct from artifact/value serialization (`store/codec.py`). The store codec handles **cacheable values only** (artifacts, manifests). Graph serialization handles **graph structure** — a different layer with different constraints: human-readable, debuggable, and interoperable.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Top-Level Envelope](#2-top-level-envelope)
3. [Parameter Value Encoding](#3-parameter-value-encoding)
   - [Marker Types](#31-marker-types-ref-cel)
   - [Decimal and Tuple](#32-decimal-and-tuple)
   - [Literal Escape](#33-literal-escape)
   - [Collision Policy](#34-collision-policy)
   - [ICacheable (Domain Types)](#35-icacheable-domain-types)
4. [Vertex Encoding](#4-vertex-encoding)
   - [Node](#41-node)
   - [SubGraphNode](#42-subgraphnode)
   - [SwitchNode](#43-switchnode)
5. [Validation Requirements](#5-validation-requirements)
6. [Determinism](#6-determinism)
7. [Type Mapping Reference](#7-type-mapping-reference)
8. [Graph Data URIs](#8-graph-data-uris)
9. [YAML Authoring Format](#9-yaml-authoring-format)
10. [Complete Example](#10-complete-example)
11. [Backwards Compatibility](#11-backwards-compatibility)
12. [Related Documents](#12-related-documents)

---

## 1. Overview

Invariant graphs (`dict[str, Node | SubGraphNode | SwitchNode]`) are Python objects that cannot be serialized directly to JSON because:

- **Parameter markers** (`ref`, `cel`) are dataclass instances, not JSON-native types.
- **Vertices** (`Node`, `SubGraphNode`, `SwitchNode`) are dataclasses with nested structure.
- **Decimal** has no JSON representation.
- **Tuples** serialize as lists and lose type fidelity on round-trip.
- **ICacheable** domain types (e.g. `Polynomial`, or types from child projects like `invariant-gfx`) have custom binary serialization but no JSON representation.

This specification defines a JSON wire format that:

1. Encodes all graph structure and parameter values unambiguously.
2. Supports round-trip serialization and deserialization.
3. Preserves type fidelity (ref, cel, Decimal, tuple, ICacheable).
4. Enables deterministic output for diffs and hashing.
5. Includes versioning for future evolution.

---

## 2. Top-Level Envelope

Every serialized graph is wrapped in a top-level envelope:

```json
{
  "format": "invariant-graph",
  "version": 1,
  "output": "final",
  "graph": { ... }
}
```

The `output` field is optional. When present, it names the document's default
output vertex for callers that need a single default artifact, such as the CLI or
rendering systems that execute a graph document as a component.

| Field | Type | Required | Description |
|:--|:--|:--|:--|
| `format` | string | Yes | Must be `"invariant-graph"`. Identifies the document type. |
| `version` | integer | Yes | Schema version. Current version is `1`. Enables future migration. |
| `output` | string | No | Default output vertex. Must be non-empty and name a graph vertex when present. |
| `graph` | object | Yes | The graph: mapping of node IDs to vertex objects. Keys are node IDs (strings). |

Loaders must reject documents where `format` is not `"invariant-graph"` or `version` is unsupported.

---

## 3. Parameter Value Encoding

Parameters (`params` in Node and SubGraphNode) can contain literals, markers, and nested structures. The encoding is recursive.

### 3.1 Marker Types (ref, cel)

Parameter markers are encoded as single-key JSON objects:

| Python Type | JSON Representation |
|:--|:--|
| `ref("dep_name")` | `{"$ref": "dep_name"}` |
| `cel("expression")` | `{"$cel": "expression"}` |

**Examples:**

```json
{"a": {"$ref": "p"}, "b": {"$ref": "q"}}
```

```json
{"width": {"$cel": "decimal(background.width) * decimal('0.75')"}}
```

**Nesting:** Markers may appear anywhere params can appear: top-level, nested in dicts, or as elements of lists.

### 3.2 Decimal and Tuple

JSON has no native `Decimal` or `tuple`. These are encoded with tags:

| Python Type | JSON Representation |
|:--|:--|
| `Decimal("12.34")` | `{"$decimal": "12.34"}` |
| `(a, b, c)` | `{"$tuple": [ ... ]}` (elements recursively encoded) |

**Decimal:** The value is the canonical string representation (e.g. `"12.34"`, `"-0.5"`). Deserializers must construct `decimal.Decimal` from this string.

**Tuple:** The value is a JSON array. Each element is recursively encoded. Deserializers must reconstruct a Python tuple.

**Examples:**

```json
{"price": {"$decimal": "19.99"}}
```

```json
{"coords": {"$tuple": [1, 2]}}
```

```json
{"pair": {"$tuple": [{"$ref": "x"}, {"$cel": "y + 1"}]}}
```

### 3.3 Literal Escape

If a parameter value is literally a dict that would be interpreted as a marker (e.g. `{"$ref": "x"}` meaning "a dict with key $ref"), use the escape:

```json
{"$literal": <value>}
```

The inner `<value>` is decoded as a plain value with **no further marker interpretation**. This allows a literal `{"$ref": "x"}` to be serialized as:

```json
{"$literal": {"$ref": "x"}}
```

Deserializers must recognize `$literal` and return the inner value (with recursive decoding of nested structures, but without treating inner dict keys as markers).

### 3.4 Collision Policy

The following keys are **reserved** in the param value encoding:

- `$ref`
- `$cel`
- `$decimal`
- `$tuple`
- `$literal`
- `$icacheable`

**Rule:** A JSON object that is a **single-key** dict with one of these keys is treated as a marker (or escape). A multi-key dict is never treated as a marker; its values are recursively encoded.

**Reserved key usage:** Document that `$ref`, `$cel`, `$decimal`, `$tuple`, `$icacheable` are reserved in params. If params may contain arbitrary user-supplied JSON that could collide, use `$literal` to wrap the conflicting value.

### 3.5 ICacheable (Domain Types)

Literal ICacheable values in params (e.g. `Polynomial([1,2,3])` or domain types from child projects like `invariant-gfx`) are encoded as:

```json
{"$icacheable": {"type": "invariant.types.Polynomial", "payload_b64": "<base64>"}}
```

Or, when the type implements the optional `IJsonRepresentable` protocol (see below), a human-readable form:

```json
{"$icacheable": {"type": "invariant.types.Polynomial", "value": {"coefficients": [1, 2, 3]}}}
```

| Field | Type | Required | Description |
|:--|:--|:--|:--|
| `type` | string | Yes | Fully qualified class name: `module.ClassName`. Enables dynamic import via `importlib.import_module`. |
| `payload_b64` | string | No* | Base64-encoded binary output of `to_stream()`. Used when type has no JSON representation. |
| `value` | object | No* | Human-readable JSON. Used when type implements `IJsonRepresentable`. |

\* Exactly one of `payload_b64` or `value` must be present.

**Serialization:** If the type implements `IJsonRepresentable`, use `to_json_value()` and emit `value`. Otherwise, call `to_stream()`, base64-encode the bytes, and emit `payload_b64`.

**Deserialization:** Import the class via `type` (e.g. `invariant.types.Polynomial` → `Polynomial`). If `value` is present and the class has `from_json_value`, call it. Otherwise, base64-decode `payload_b64`, wrap in `BytesIO`, and call `cls.from_stream(stream)`.

**Type identity:** The `type` field is the FQN so the loader can import the class from any installed package. A graph with `invariant_gfx.effects.GlowParams` requires the `invariant-gfx` package to be installed at deserialization time. The graph format carries type identity; the runtime must provide the implementation.

**Optional protocol — IJsonRepresentable:** Types may optionally implement a separate protocol for human-readable JSON:

```python
class IJsonRepresentable(Protocol):
    """Optional: ICacheable types can implement this for human-readable JSON in graph serialization."""
    def to_json_value(self) -> dict: ...

    @classmethod
    def from_json_value(cls, obj: dict) -> Self: ...
```

Types that implement this get `value`-based encoding; others use `payload_b64`. Each type owns its JSON representation; no central registry is required. Child projects add the protocol to their types and get readable JSON automatically.

---

## 4. Vertex Encoding

A graph is `dict[str, Node | SubGraphNode | SwitchNode]`. Each vertex is encoded as a JSON object with an explicit `kind` discriminator.

### 4.1 Node

**Python definition** (`invariant.node.Node`):

```python
@dataclass(frozen=True)
class Node:
    op_name: str
    params: dict[str, Any]
    deps: list[str]
    cache: bool = True  # When False: ephemeral node; cache bypass cascades downstream
```

**JSON representation:**

```json
{
  "kind": "node",
  "op_name": "poly:add",
  "params": {"a": {"$ref": "p"}, "b": {"$ref": "q"}},
  "deps": ["p", "q"]
}
```

For ephemeral nodes, include `"cache": false`. When `cache` is true or omitted, it is typically not emitted (compact encoding).

| Field | Type | Required | Description |
|:--|:--|:--|:--|
| `kind` | string | Yes | Must be `"node"`. |
| `op_name` | string | Yes | Non-empty. The registered op identifier. |
| `params` | object | Yes | Parameter dict. Keys and values use param encoding. |
| `deps` | array of strings | Yes | List of dependency node IDs. |
| `cache` | boolean | No | When `false`, the node is ephemeral: it is never cached, and cache bypass cascades to downstream nodes. Default `true` when omitted. See [executor.md](executor.md) §4.1. |

### 4.2 SubGraphNode

**Python definition** (`invariant.node.SubGraphNode`):

```python
@dataclass(frozen=True)
class SubGraphNode:
    params: dict[str, Any]
    deps: list[str]
    graph: dict[str, Node | SubGraphNode | SwitchNode]
    output: str
```

**JSON representation:**

```json
{
  "kind": "subgraph",
  "params": {"left": {"$ref": "x"}, "right": {"$ref": "y"}},
  "deps": ["x", "y"],
  "graph": {
    "sum": {
      "kind": "node",
      "op_name": "stdlib:add",
      "params": {"a": {"$ref": "left"}, "b": {"$ref": "right"}},
      "deps": ["left", "right"]
    }
  },
  "output": "sum"
}
```

| Field | Type | Required | Description |
|:--|:--|:--|:--|
| `kind` | string | Yes | Must be `"subgraph"`. |
| `params` | object | Yes | Parameter dict. Keys and values use param encoding. |
| `deps` | array of strings | Yes | List of dependency node IDs. |
| `graph` | object | Yes | Internal graph. Keys are node IDs; values are vertex objects (Node, SubGraphNode, or SwitchNode). Recursive. |
| `output` | string | Yes | Node ID within `graph` whose artifact is the subgraph result. Must be a key in `graph`. |

**Nested vertices:** A `graph` value may contain vertices with `"kind": "subgraph"` or `"kind": "switch"`. Deserialization is recursive.

### 4.3 SwitchNode

**Python definition** (`invariant.node.SwitchNode`):

```python
@dataclass(frozen=True)
class SwitchNode:
    selector: Any
    deps: list[str]
    cases: dict[str, str]
    default: str | None = None
```

**JSON representation:**

```json
{
  "kind": "switch",
  "selector": {"$cel": "art == null ? 'plain' : (art.width > 160 ? 'wide' : 'compact')"},
  "deps": ["art"],
  "cases": {
    "plain": "plain_status",
    "wide": "wide_status",
    "compact": "compact_status"
  },
  "default": "plain_status"
}
```

| Field | Type | Required | Description |
|:--|:--|:--|:--|
| `kind` | string | Yes | Must be `"switch"`. |
| `selector` | any encoded parameter value | Yes | Selector value resolved from `deps`. May use the same marker encoding as params. |
| `deps` | array of strings | Yes | Required dependencies for selector resolution only. |
| `cases` | object string → string | Yes | Mapping from normalized selector values to graph-local target node IDs. Must not be empty. |
| `default` | string | No | Graph-local target node ID used when no case matches. |

Branch targets are graph-local node IDs, not dependencies, context keys, inline graphs, or operation names. They are resolved lazily by the executor only when selected or explicitly requested as outputs. Selector result normalization is defined in [executor.md](executor.md) §4.7.

---

## 5. Validation Requirements

Loaders must perform explicit validation **before** constructing graph vertex instances. Do not rely solely on `__post_init__`; JSON can be malformed in ways that produce unclear errors (missing keys, wrong types, extra fields).

### 5.1 Top-Level Envelope

- `format` must be present and equal to `"invariant-graph"`.
- `version` must be present and supported (currently `1`).
- `graph` must be present and be an object.
- If present, `output` must be a non-empty string and a key in `graph`.

### 5.2 Node Validation

Before constructing `Node(...)`:

| Check | Error condition |
|:--|:--|
| `kind` | Must be `"node"`. |
| `op_name` | Must be present, a string, and non-empty (after strip). |
| `params` | Must be present and an object. |
| `deps` | Must be present and an array. Every element must be a string. |
| `cache` | If present, must be a boolean. |

### 5.3 SubGraphNode Validation

Before constructing `SubGraphNode(...)`:

| Check | Error condition |
|:--|:--|
| `kind` | Must be `"subgraph"`. |
| `params` | Must be present and an object. |
| `deps` | Must be present and an array. Every element must be a string. |
| `graph` | Must be present and an object. |
| `output` | Must be present, a string, and a key in `graph`. |

Recursively validate each vertex in `graph` before constructing the SubGraphNode.

### 5.4 SwitchNode Validation

Before constructing `SwitchNode(...)`:

| Check | Error condition |
|:--|:--|
| `kind` | Must be `"switch"`. |
| `selector` | Must be present. |
| `deps` | Must be present and an array. Every element must be a string. |
| `cases` | Must be present, an object, non-empty, and every key/value must be a string. Values must be graph-local node IDs. |
| `default` | If present, must be a non-empty string and a graph-local node ID. |

### 5.5 ICacheable Validation

When decoding `$icacheable` objects in params:

| Check | Error condition |
|:--|:--|
| `type` | Must be present, a non-empty string, and resolvable (module importable, class exists). |
| Payload | Exactly one of `payload_b64` or `value` must be present. |
| `payload_b64` | If present, must be valid base64; `from_stream()` must succeed. |
| `value` | If present, class must have `from_json_value`; call must succeed. |

The deserializing environment must have the package defining the type installed (e.g. `invariant-gfx` for `invariant_gfx.effects.GlowParams`).

### 5.6 Post-Construction Validation

After constructing graph vertices, `__post_init__` runs and validates:

- All `ref(dep)` markers in params reference a dependency in `deps`.
- For SubGraphNode: `output` is a key in `graph`.
- For SwitchNode: all `ref(dep)` markers in `selector` reference a dependency in `deps`.

These invariants are enforced by the dataclass; the loader's job is to ensure the JSON structure is valid so that construction succeeds and these checks pass.

---

## 6. Determinism

For reproducible output (diffs, hashing, version control), serialization must be deterministic.

| Rule | Implementation |
|:--|:--|
| Graph keys | Serialize graph entries in **sorted key order** (node IDs). |
| Param keys | Serialize `params` entries in **sorted key order**. |
| `deps` order | Serialize `deps` as a **sorted list**. |
| JSON output | Use `json.dumps(..., sort_keys=True)` when producing the final string. |

This ensures that two semantically identical graphs produce identical JSON bytes.

---

## 7. Type Mapping Reference

### 7.1 Parameter Values

| Python | JSON |
|:--|:--|
| `None` | `null` |
| `bool` | `true` / `false` |
| `int` | number |
| `str` | string (including `${...}` interpolation — no special encoding) |
| `Decimal` | `{"$decimal": "..."}` |
| `ref(dep)` | `{"$ref": "dep"}` |
| `cel(expr)` | `{"$cel": "expr"}` |
| `tuple(...)` | `{"$tuple": [...]}` |
| `list[...]` | `[...]` |
| `dict` | `{...}` (keys/values recursively encoded; single-key `$ref`/`$cel`/etc. treated as markers) |
| `ICacheable` | `{"$icacheable": {"type": "module.ClassName", "payload_b64": "..."}}` or `{"value": {...}}` if IJsonRepresentable |
| Literal escape | `{"$literal": <value>}` |

### 7.2 Vertices

| Python | JSON `kind` |
|:--|:--|
| `Node` | `"node"` |
| `SubGraphNode` | `"subgraph"` |
| `SwitchNode` | `"switch"` |

---

## 8. Graph Data URIs

Graph documents may be embedded in data URIs for transport through systems that
only carry a single string field:

```text
data:application/vnd.invariant.graph+json;base64,<canonical-json>?text=Kitchen&width=144&missing=null
```

The encoded payload is the canonical JSON graph document, including optional
document `output`. The query string is execution context. This lets producers
reuse a static encoded graph and append per-render context without rebuilding the
graph payload.

Query values are decoded as JSON when possible and otherwise as strings. For
example, `width=144` becomes integer `144`, `missing=null` becomes `None`, and
`text=Kitchen` remains the string `"Kitchen"`. JSON marker values are decoded
through the same parameter value encoding, so a percent-encoded
`{"$tuple":[1,2]}` becomes a tuple.

The query key `output` is not reserved or special. Output selection comes from
the graph document's `output` field or from the caller. Duplicate query keys,
empty query keys, fragments, invalid base64, and invalid graph documents are
errors for matching Invariant graph data URIs.

Use `graph_data_uri_cache_key(uri)` to strip query context and get the static
data URI suitable for caching decoded graph documents.

---

## 9. YAML Authoring Format

YAML is a supported load-only authoring format. It compiles directly into the
same graph document model described above, then uses the existing graph loading
and validation path. Canonical dumping remains JSON through `dump_graph*`
functions.

YAML support is optional. Install it with:

```bash
pip install invariant-core[yaml]
```

If PyYAML is not installed, `load_graph_yaml()` and `load_graph_document_yaml()`
raise `RuntimeError` with the same install guidance.

YAML resource subgraph grafting is a separate optional capability. Install both
extras when authoring graphs that use `!subgraph` resource references:

```bash
pip install invariant-core[yaml,resources]
```

### 9.1 Explicit Tags

YAML documents use the normal graph keys (`kind`, `op_name`, `deps`, `params`,
`graph`, `output`) and these explicit tags for values that are not plain YAML:

| YAML | Equivalent JSON Marker |
|:--|:--|
| `!ref value` | `{"$ref": "value"}` |
| `!cel value` | `{"$cel": "value"}` |
| `!decimal value` | `{"$decimal": "value"}` |
| `!tuple [ ... ]` | `{"$tuple": [ ... ]}` |
| `!literal value` | `{"$literal": value}` |
| `!icacheable {type: ..., value: ...}` | `{"$icacheable": {"type": ..., "value": ...}}` |
| `!icacheable {type: ..., payload_b64: ...}` | `{"$icacheable": {"type": ..., "payload_b64": ...}}` |

The YAML layer is operation-agnostic. It does not provide `op` aliases, infer
dependencies, infer `ref()` values from strings, or expand color, size, unit, or
operation-specific shortcuts. Any operation-specific authoring sugar belongs in a
separate compiler before this format.

### 9.2 Graph Document

```yaml
format: invariant-graph
version: 1
output: payload
graph:
  amount:
    kind: node
    op_name: stdlib:identity
    deps: []
    params:
      value: !decimal "2.50"
  payload:
    kind: node
    op_name: stdlib:make_dict
    deps: [amount]
    params:
      amount: !ref amount
      pair: !tuple [1, !decimal "2.50"]
      literal_marker: !literal
        $ref: not-a-marker
```

The document `output` field is optional. If present, it must name a graph
vertex. There is no second outer document shape for pairing a graph with an
output.

### 9.3 SwitchNode

SwitchNode uses the same generic fields in YAML. Case keys such as `"null"`,
`"true"`, `"false"`, and numeric-looking strings should be quoted so YAML does
not coerce them.

```yaml
format: invariant-graph
version: 1
graph:
  plain_status:
    kind: node
    op_name: stdlib:identity
    deps: []
    params:
      value: plain
  wide_status:
    kind: node
    op_name: stdlib:identity
    deps: []
    params:
      value: wide
  status:
    kind: switch
    selector: !cel "art == null ? 'plain' : (art.width > 160 ? 'wide' : 'plain')"
    deps: [art]
    cases:
      "plain": plain_status
      "wide": wide_status
    default: plain_status
```

### 9.4 Resource Subgraph Grafting

YAML supports one vertex-level authoring tag for reusable graph components:
`!subgraph`. The tag resolves a graph document through JustMyResource while the
YAML document is loaded, then grafts that graph into an ordinary canonical
`SubGraphNode` before validation. Canonical JSON dumps and graph data URIs never
contain the resource name, file path, include marker, or any runtime lookup
instruction.

```yaml
format: invariant-graph
version: 1
output: badge
graph:
  title:
    kind: node
    op_name: stdlib:identity
    deps: []
    params:
      value: Kitchen
  badge: !subgraph
    resource: deckr-components:badge
    deps: [title]
    params:
      text: !ref title
    output: final
```

The tag fields are:

| Field | Required | Meaning |
|:--|:--|:--|
| `resource` | Yes | JustMyResource resource name. |
| `deps` | Yes | Parent graph dependencies required by `params`. |
| `params` | Yes | Parent-to-subgraph manifest mapping, using the same explicit YAML tags as normal `params`. |
| `output` | No | Internal output node ID. If omitted, the referenced graph document must have a default `output`. |

Resource documents must be canonical Invariant graph documents. JSON resources
are accepted with content type `application/vnd.invariant.graph+json` or
`application/json`. YAML resources are accepted with content type
`application/vnd.invariant.graph+yaml`, `application/yaml`, `text/yaml`, or
`application/x-yaml`; a resource name ending in `.yaml` or `.yml` is also
treated as YAML. JSON resources require one of the JSON content types above.
Unknown content types without a recognized YAML suffix are rejected.

Nested `!subgraph` resources are allowed. Include cycles are detected by
resource name stack and raise `ValueError`. If JustMyResource is not installed
and a `!subgraph` tag is used, loading raises `RuntimeError` with guidance to
install `invariant-core[resources]`.

The CLI auto-detects `.yaml` and `.yml` graph input files. Stdin remains JSON
unless `--input-format yaml` is supplied. Context files and `--param` values are
JSON-only.

---

## 10. Complete Example

A graph with two source nodes, a subgraph, and a consumer:

```json
{
  "format": "invariant-graph",
  "version": 1,
  "output": "double",
  "graph": {
    "double": {
      "kind": "node",
      "op_name": "stdlib:multiply",
      "params": {"a": {"$ref": "sum"}, "b": 2},
      "deps": ["sum"]
    },
    "sum": {
      "kind": "subgraph",
      "params": {"left": {"$ref": "x"}, "right": {"$ref": "y"}},
      "deps": ["x", "y"],
      "graph": {
        "sum": {
          "kind": "node",
          "op_name": "stdlib:add",
          "params": {"a": {"$ref": "left"}, "b": {"$ref": "right"}},
          "deps": ["left", "right"]
        }
      },
      "output": "sum"
    },
    "x": {
      "kind": "node",
      "op_name": "stdlib:identity",
      "params": {"value": 5},
      "deps": []
    },
    "y": {
      "kind": "node",
      "op_name": "stdlib:identity",
      "params": {"value": 3},
      "deps": []
    }
  }
}
```

Note: Keys are shown in sorted order (deterministic serialization). The `graph` object would be serialized with keys `["double", "sum", "x", "y"]`.

---

## 11. Backwards Compatibility

### 11.1 Future Versions

When introducing a new schema version:

1. Bump `version` in the envelope.
2. Document migration path from previous version.
3. Loaders may support multiple versions; document which are supported.

### 11.2 Optional Node Fields

The `cache` field on Node is optional. Documents without it decode as `cache=true`. When writing, implementations typically omit `cache` when true to keep payloads compact.

### 11.3 Optional: Legacy Kind Inference

Implementations may optionally accept vertices that omit `kind` for backwards compatibility with pre-specification payloads:

- If `op_name` is present and `graph` is absent → treat as `"node"`.
- If `graph` and `output` are present → treat as `"subgraph"`.

When **writing**, always include `kind`. When **reading**, prefer `kind` if present; fall back to inference only for legacy documents.

---

## 12. Related Documents

| Document | Description |
|:--|:--|
| [expressions.md](expressions.md) | Parameter markers (`ref`, `cel`, `${...}`) and CEL expression language |
| [executor.md](executor.md) | Two-phase execution model, manifest resolution |
| [subgraphs.md](subgraphs.md) | SubGraphNode model and execution semantics |
| [architecture.md](architecture.md) | Design philosophy, cacheable type universe |

**Artifact serialization:** The store codec (`store/codec.py`) serializes **artifacts** (op outputs) and **cacheable values** only. It does not handle graphs, ref, or cel. See `invariant.cacheable` and `invariant.protocol.ICacheable` for the cacheable type universe. The `$icacheable` encoding in this document reuses the same type-name + binary-payload pattern (with base64 for JSON); the optional `IJsonRepresentable` protocol extends types for human-readable JSON in graph params.
