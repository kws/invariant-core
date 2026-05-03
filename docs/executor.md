# Executor Reference

This document is the normative reference for Invariant's execution model — the two-phase pipeline that transforms a DAG of Nodes into cached Artifacts. It covers graph validation, manifest construction, cache lookup, operation invocation, and artifact storage.

**Source of truth:** This document. If other documentation (AGENTS.md, README.md, architecture.md) conflicts with this reference, this document takes precedence.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Two-Phase Execution Model](#2-two-phase-execution-model)
3. [Phase 1: Context Resolution](#3-phase-1-context-resolution)
   - [Graph Validation](#31-graph-validation)
   - [Topological Sort](#32-topological-sort)
   - [Manifest Construction](#33-manifest-construction)
   - [Digest Computation](#34-digest-computation)
4. [Phase 2: Action Execution](#4-phase-2-action-execution)
   - [Cache Lookup and Deduplication](#41-cache-lookup-and-deduplication)
   - [Operation Invocation](#42-operation-invocation)
   - [Type Unwrapping](#43-type-unwrapping)
   - [Return Value Validation and Wrapping](#44-return-value-validation-and-wrapping)
   - [Artifact Persistence](#45-artifact-persistence)
   - [SubGraphNode Execution](#46-subgraphnode-execution)
5. [External Dependencies (Context)](#5-external-dependencies-context)
6. [Graph Resolver](#6-graph-resolver)
7. [Artifact Store](#7-artifact-store)
   - [Store Interface](#71-store-interface)
   - [MemoryStore](#72-memorystore)
   - [DiskStore](#73-diskstore)
   - [ChainStore](#74-chainstore)
8. [Op Registry](#8-op-registry)
9. [Cacheable Type Universe](#9-cacheable-type-universe)
10. [Examples](#10-examples)
11. [Implementation Flags](#11-implementation-flags)

---

## 1. Overview

The `Executor` is the runtime engine that takes a graph of `Node` objects and produces a dictionary of `ICacheable` artifacts. Its primary goals are:

- **Cache-first execution:** Skip operations when cached results exist
- **Deduplication:** Execute identical operations only once per run
- **Determinism:** Identical inputs always produce identical outputs
- **Explicit data flow:** Dependencies are only available through declared param markers

```python
from invariant import Executor, Node, OpRegistry, ref
from invariant.store.memory import MemoryStore

registry = OpRegistry()
store = MemoryStore(cache="unbounded")
executor = Executor(registry=registry, store=store)

graph = {
    "x": Node(op_name="stdlib:from_integer", params={"value": 5}, deps=[]),
    "y": Node(op_name="stdlib:from_integer", params={"value": 3}, deps=[]),
    "sum": Node(
        op_name="stdlib:add",
        params={"a": ref("x"), "b": ref("y")},
        deps=["x", "y"],
    ),
}

results = executor.execute(graph)
# results["sum"].value == 8
```

---

## 2. Two-Phase Execution Model

For each node (in topological order), the executor runs two phases:

```
┌─────────────────────────────────────────────────────────┐
│  Phase 1: Context Resolution                            │
│                                                         │
│  Node params + dependency artifacts                     │
│       ↓ resolve ref(), cel(), ${...}                    │
│  Manifest (resolved params only)                        │
│       ↓ hash_manifest()                                 │
│  Digest (SHA-256 cache key)                             │
├─────────────────────────────────────────────────────────┤
│  Phase 2: Action Execution                              │
│                                                         │
│  Check: store.exists(op_name, digest)?                  │
│       → Yes: cache hit, load from store                 │
│  Otherwise:                                             │
│       Invoke op(**kwargs from manifest)                 │
│       Validate return value                             │
│       Store artifact to store                           │
│                                                         │
│  Result: ICacheable artifact                            │
└─────────────────────────────────────────────────────────┘
```

**Key invariant:** The manifest is built entirely from resolved params. Dependencies are NOT injected into the manifest — they are only used to resolve `ref()`, `cel()`, and `${...}` markers within params. This is what makes the cache identity explicit and deterministic.

---

## 3. Phase 1: Context Resolution

### 3.1 Graph Validation

Before execution begins, the `GraphResolver` validates the graph:

1. **Dependency existence:** Every dependency declared in a node's `deps` must exist either as a node in the graph or as a key in the `context` dict.
2. **Op registration:** Every `op_name` must be registered in the `OpRegistry` (if a registry is provided to the resolver).
3. **Cycle detection:** The graph must be acyclic. Cycles are detected using DFS with three-color marking (WHITE/GRAY/BLACK).

Validation failures raise `ValueError` with descriptive messages.

**Example — missing dependency error:**

```python
graph = {
    "a": Node(op_name="test", params={}, deps=["nonexistent"]),
}
executor.execute(graph)
# ValueError: Node 'a' has dependency 'nonexistent' that doesn't exist
#             in graph or context.
```

### 3.2 Topological Sort

After validation, nodes are topologically sorted using **Kahn's algorithm** (BFS-based). This guarantees that when a node is processed, all its dependencies have already been executed and their artifacts are available.

Context dependencies (external inputs not in the graph) are excluded from the sort — they are injected before the sort loop begins.

### 3.3 Manifest Construction

For each node, the executor builds a **Manifest** — a fully resolved dictionary of parameter values:

1. Collect dependency artifacts from already-executed upstream nodes
2. Call `resolve_params(node.params, dependencies)` to resolve all markers:
   - `ref("dep")` → ICacheable artifact from dependency
   - `cel("expr")` → evaluated CEL expression result
   - `"${expr}"` → interpolated string (or native type for whole-string expressions)
   - Literals → passed through unchanged
   - Nested dicts/lists → recursively resolved
3. The result is the Manifest — a plain dict with all markers resolved

**The manifest contains resolved params only.** The dependency artifacts are used during resolution but are not themselves part of the manifest.

See [Expressions Reference](./expressions.md) for full details on marker resolution.

### 3.4 Digest Computation

The Manifest is hashed to produce a **Digest** — a 64-character hex SHA-256 string that serves as the cache key.

**Hashing rules** (`hash_manifest` in `hashing.py`):

1. Keys are sorted alphabetically for canonical ordering
2. Each key and value is hashed recursively:
   - `ICacheable` → `get_stable_hash()`
   - `str` → SHA-256 of UTF-8 bytes
   - `int` → SHA-256 of string representation
   - `Decimal` → SHA-256 of canonicalized string
   - `dict` → sorted keys, recursive hash of each key-value pair
   - `list`/`tuple` → sequential hash of each element
   - `None` → SHA-256 of `b"None"`
3. All individual hashes are combined into a single SHA-256 digest

**Supported types for hashing:** `ICacheable`, `str`, `int`, `Decimal`, `dict`, `list`, `tuple`, `None`.

**Unsupported types** raise `TypeError` — notably `float` and `bytes`.

---

## 4. Phase 2: Action Execution

### 4.1 Cache Lookup and Deduplication

The cache key is the tuple `(op_name, digest)`. This composite key ensures that different operations with the same input manifest cache separately — two different ops could receive identical inputs but produce different outputs.

The executor checks two levels, in order:

1. **Store cache** (`store.exists(op_name, digest)`): Checks the configured `ArtifactStore`. If found, loads the artifact via `store.get()`.

2. **Execution:** If the store check misses, the op is invoked and the result is stored via `store.put()`.

All cache lookups go through the configured store, which provides a single, observable cache layer. Stores track cache statistics (hits, misses, puts) via the `store.stats` attribute.

**Ephemeral nodes (cache bypass):** A Node may set `cache=False`. For such nodes, the executor skips cache lookup and never stores the result. Cache bypass also cascades to all downstream graph nodes: any node with a declared dependency on an ephemeral node is treated as ephemeral, and that status continues transitively. Use this for nodes whose outputs change frequently and would otherwise create one-off downstream cache entries (e.g., time-dependent values passed as context). The op is invoked every time; `store.exists()` and `store.put()` are not called for that node or its downstream descendants. Ephemeral nodes are not deduplicated within a run — two identical ephemeral nodes (same op, params, deps) both execute.

```python
# Ephemeral node: always executes, never caches; descendants do the same
"clock": Node(
    op_name="stdlib:identity",
    params={"value": ref("now")},
    deps=["now"],
    cache=False,
)
```

### 4.2 Operation Invocation

Ops are plain Python functions. The executor maps manifest keys to function parameters using `inspect.signature()`:

1. Inspect the op's function signature
2. For each declared parameter:
   - If the parameter name exists as a manifest key → use the manifest value
   - If the parameter has a default value → skip (use default)
   - If the parameter is `**kwargs` → handled separately
   - Otherwise → raise `ValueError` (missing required parameter)
3. If the function accepts `**kwargs`, remaining manifest keys not already mapped are passed through
4. Invoke `op(**kwargs)`

**Example:**

```python
# Op signature:
def add(a: int, b: int) -> int:
    return a + b

# Manifest: {"a": 3, "b": 7}
# Executor maps: a=3, b=7
# Invokes: add(a=3, b=7) → 10
```

### 4.3 Return Value Validation

After the op returns, the executor:

1. **Validates** the return value is cacheable using `is_cacheable(result)`. If not, raises `TypeError`.
2. **Stores** the value as-is (native types or ICacheable domain types). No wrapping is performed.

This means ops can return native Python types (`int`, `str`, `Decimal`, `dict`, `list`) or ICacheable domain types (like `Polynomial`), and they are stored directly.

**Example:**

```python
def add(a: int, b: int) -> int:
    return a + b  # Returns native int

# Executor stores: 8 (native int) directly
```

### 4.4 Artifact Persistence

After execution (or cache retrieval), the artifact is:

1. Stored in the `ArtifactStore` under `(op_name, digest)` if cache miss, `node.cache` is true, and no declared dependency is ephemeral
2. Stored in `artifacts_by_node[node_id]` for downstream dependency resolution

### 4.6 SubGraphNode Execution

A graph may contain **SubGraphNode** vertices in addition to **Node** vertices. A `SubGraphNode` has no `op_name`; instead it carries an internal graph (`dict[str, Node]`) and an `output` key. When the executor encounters a SubGraphNode (in topological order), it:

1. **Builds the manifest** from the SubGraphNode's params and deps (same as Phase 1 for a Node).
2. **Executes the internal graph** with the same Executor instance, same registry, same ArtifactStore. The resolved params are passed as context so internal nodes can reference them by dependency name.
3. **Assigns** the internal `output` node's artifact to this vertex: `artifacts_by_node[node_id] = inner_results[node.output]`.

There is **no SubGraphNode-level caching**; only the internal ops are cached by `(op_name, digest)`. The same store is used for the entire run, so identical work inside one or across multiple subgraphs is deduplicated.

Ephemeral status crosses SubGraphNode boundaries. If a SubGraphNode has an ephemeral dependency, internal nodes that depend on the manifest context bypass cache, and the SubGraphNode output is considered ephemeral for parent-graph descendants. If the internal output node is ephemeral, the parent-facing SubGraphNode output is also considered ephemeral.

For the full SubGraphNode model, execution semantics, and shared caching, see [Subgraphs](./subgraphs.md).

---

## 5. External Dependencies (Context)

The `executor.execute(graph, context={...})` method accepts an optional `context` dict of external dependencies — values not produced by any node in the graph.

**How context works:**

1. Before the topological sort loop, context values are injected into `artifacts_by_node`
2. Context values must be cacheable (`is_cacheable()` check) and are stored as-is (no wrapping)
3. Any node can declare a context key in its `deps` and reference it via `ref()`, `cel()`, or `${...}` — identically to graph-internal dependencies
4. From a node's perspective, there is no difference between an internal artifact and a context value

**Rules:**

- A dependency that **is** a key in the graph → internal dependency (resolved by executing that node)
- A dependency that **is not** in the graph but **is** in context → external dependency
- A dependency in neither → validation error

**Example:**

```python
context = {"root_width": 144}

graph = {
    "bg": Node(
        op_name="stdlib:identity",
        params={"value": cel("root_width")},
        deps=["root_width"],  # References context, not a graph node
    ),
}

results = executor.execute(graph, context=context)
```

---

## 6. Graph Resolver

The `GraphResolver` is responsible for validating and sorting the DAG.

**API:**

```python
resolver = GraphResolver(registry=registry)  # registry optional

# Full pipeline: validate + sort
sorted_node_ids = resolver.resolve(graph, context_keys={"root"})

# Or individually:
resolver.validate(graph, context_keys={"root"})
sorted_node_ids = resolver.topological_sort(graph, context_keys={"root"})
```

**Validation checks:**
1. All dependencies exist in graph or context
2. All ops are registered (if registry provided)
3. No cycles (DFS three-color algorithm)

**Topological sort:** Kahn's algorithm. Context dependencies are excluded from in-degree calculations. Returns a list of node IDs in execution order (dependencies before dependents).

---

## 7. Artifact Store

### 7.1 Store Interface

All stores implement `ArtifactStore` (abstract base class):

```python
class ArtifactStore(ABC):
    def __init__(self) -> None:
        self.stats = CacheStats()  # Cache statistics (hits, misses, puts)
    
    def exists(self, op_name: str, digest: str) -> bool: ...
    def get(self, op_name: str, digest: str) -> ICacheable: ...
    def put(self, op_name: str, digest: str, artifact: ICacheable) -> None: ...
    def reset_stats(self) -> None: ...  # Reset statistics to zero
```

The composite key `(op_name, digest)` ensures that different operations with identical input manifests cache separately.

**Cache statistics:** All stores track cache performance via `store.stats` (a `CacheStats` object with `hits`, `misses`, and `puts` attributes).

**Serialization format** (used by DiskStore):

```
[4 bytes: type_name_length][type_name_utf8][serialized_artifact_bytes]
```

Where:
- `type_name` is the fully qualified class path (e.g., `"invariant.types.Integer"`)
- `serialized_artifact_bytes` is the output of `artifact.to_stream()`
- Deserialization uses `importlib` to load the class and calls `cls.from_stream()`

### 7.2 MemoryStore

Fast, ephemeral store using an in-memory dict or cachetools cache. Suitable for testing.

```python
from invariant.store.memory import MemoryStore
store = MemoryStore()                         # LRU, max_size=1000 (default)
store = MemoryStore(cache="unbounded")        # Plain dict, no eviction
store = MemoryStore(cache="lru", max_size=500)  # LRU eviction, 500 items
store = MemoryStore(cache="lfu", max_size=500)  # LFU eviction
store = MemoryStore(cache=TTLCache(maxsize=500, ttl=300))  # Custom cache
```

- Artifacts are stored as raw Python objects (no serialization)
- Relies on the immutability contract: artifacts are frozen once created
- Lost when the store instance is garbage collected
- Supports `clear()` method for test cleanup (also resets statistics)
- **Default:** `cache="lru"` with `max_size=1000` (safe bounded). Use `cache="unbounded"` for explicit unbounded.
- **Custom cache:** Pass a `MutableMapping` (e.g. cachetools `TTLCache`). When using a cache instance, `max_size` must not be set.

### 7.3 NullStore

No-op store that never caches. `exists()` always returns `False`; `put()` is a no-op. Use for execution-correctness tests where caching would obscure behavior, or when you want every run to execute all ops.

```python
from invariant.store.null import NullStore
store = NullStore()
```

### 7.4 DiskStore

Persistent filesystem store under `.invariant/cache/`.

```python
from invariant.store.disk import DiskStore
store = DiskStore()                          # Default: .invariant/cache/
store = DiskStore(cache_dir="/tmp/cache")    # Custom directory
```

**Directory structure:** `{cache_dir}/{safe_op_name}/{digest[:2]}/{digest[2:]}`

Where `safe_op_name` replaces `:` and `/` with `_`.

- Writes are atomic (write to `.tmp` file, then rename)
- Digest must be exactly 64 hex characters

### 7.5 ChainStore

Composite two-tier cache chaining MemoryStore (L1) and DiskStore (L2).

```python
from invariant.store.chain import ChainStore
store = ChainStore()  # Creates default L1 (MemoryStore) and L2 (DiskStore)
```

**Behavior:**
- `exists()`: Check L1, then L2
- `get()`: Try L1 first; if L2 hit, **promote** to L1 for faster subsequent access
- `put()`: Write to **both** L1 and L2

---

## 8. Op Registry

The `OpRegistry` maps string identifiers to Python callables.

```python
registry = OpRegistry()  # Singleton

# Individual registration
registry.register("my_op", my_function)

# Package registration (prefix:name)
registry.register_package("poly", poly_module)
# Registers: poly:add, poly:multiply, etc.

# Auto-discovery from entry points
registry.auto_discover()  # Scans "invariant.ops" entry point group
```

**Package registration** accepts:
- A `dict[str, Callable]` mapping short names to callables
- A Python module with an `OPS` dict attribute
- An object with an `OPS` dict attribute

**Entry point auto-discovery** scans the `"invariant.ops"` entry point group. Each entry point name becomes the package prefix.

> **Note:** See [Implementation Flag F-04 (in expressions.md)](./expressions.md#f-04-opregistry-described-as-singleton-but-requires-clear-in-tests) — the singleton pattern requires `clear()` in tests.

---

## 9. Cacheable Type Universe

The **Cacheable Type Universe** defines what values can appear in manifests, be stored as artifacts, or be passed between nodes.

**Allowed types** (recursive for containers):

| Type | Notes |
|:--|:--|
| `int` | |
| `str` | |
| `bool` | |
| `None` | Cacheable but not yet wrappable to ICacheable |
| `Decimal` | Safe numerics — no float |
| `dict[str, CacheableValue]` | String keys only, values recursively cacheable |
| `list[CacheableValue]` | Elements recursively cacheable |
| `tuple[CacheableValue, ...]` | Elements recursively cacheable |
| Any `ICacheable` implementor | |

**Forbidden types:**

| Type | Reason |
|:--|:--|
| `float` | IEEE 754 non-determinism across architectures |
| `bytes` | Not yet supported |
| Arbitrary objects | Not serializable/hashable |

**Note:** Native types are stored directly without wrapping. No wrapper types exist. The store codec handles serialization of all cacheable types uniformly.

---

## 10. Examples

### 10.1 Simple Linear Graph

```python
from invariant import Executor, Node, OpRegistry, ref
from invariant.ops import stdlib
from invariant.store.memory import MemoryStore

registry = OpRegistry()
registry.register_package("stdlib", stdlib)

graph = {
    "x": Node(op_name="stdlib:identity", params={"value": 5}, deps=[]),
    "y": Node(op_name="stdlib:identity", params={"value": 3}, deps=[]),
    "sum": Node(
        op_name="stdlib:add",
        params={"a": ref("x"), "b": ref("y")},
        deps=["x", "y"],
    ),
}

store = MemoryStore(cache="unbounded")
executor = Executor(registry=registry, store=store)
results = executor.execute(graph)

assert results["sum"] == 8
```

### 10.2 Diamond Pattern with cel()

```python
from invariant import Executor, Node, OpRegistry, cel
from invariant.store.memory import MemoryStore

registry = OpRegistry()

def add_one(value: int = 0) -> int:
    return value + 1

registry.register("add_one", add_one)

graph = {
    "a": Node(op_name="add_one", params={"value": 0}, deps=[]),
    "b": Node(op_name="add_one", params={"value": cel("a.value")}, deps=["a"]),
    "c": Node(op_name="add_one", params={"value": cel("a.value")}, deps=["a"]),
    "d": Node(
        op_name="add_one",
        params={"value": cel("b.value + c.value")},
        deps=["b", "c"],
    ),
}

store = MemoryStore(cache="unbounded")
executor = Executor(registry=registry, store=store)
results = executor.execute(graph)

# a=1, b=2, c=2, d=5
assert results["a"].value == 1
assert results["b"].value == 2
assert results["d"].value == 5
```

### 10.3 Cache Reuse Across Runs

```python
store = MemoryStore(cache="unbounded")
executor = Executor(registry=registry, store=store)

# First run: all ops execute, artifacts stored
results1 = executor.execute(graph)

# Second run: all ops skipped, artifacts loaded from cache
results2 = executor.execute(graph)

# Results are identical
assert results1["sum"].value == results2["sum"].value
```

### 10.4 Deduplication Within a Run

```python
# Two nodes with identical op + params produce the same digest
graph = {
    "a": Node(op_name="stdlib:from_integer", params={"value": 42}, deps=[]),
    "b": Node(op_name="stdlib:from_integer", params={"value": 42}, deps=[]),
}

results = executor.execute(graph)
# "a" executes, "b" reuses "a"'s artifact via deduplication
assert results["a"].value == results["b"].value == 42
```

### 10.5 External Context

```python
context = {"width": 144, "height": 144}

graph = {
    "bg": Node(
        op_name="stdlib:identity",
        params={"value": cel("width")},
        deps=["width"],
    ),
}

results = executor.execute(graph, context=context)
assert results["bg"] == 144
```

### 10.6 Complete Polynomial Pipeline

See architecture.md §8.5 for the full distributive-law verification pipeline demonstrating chains, branches, merges, and deduplication.

---

## 11. Implementation Flags

The following items are **disagreements or ambiguities** between documentation and the current implementation. Flags shared with the expressions reference are cross-referenced.

---

### F-07: Limited Type Unwrapping [RESOLVED]

**Original issue:** Documentation said (architecture.md §4): "Engine performs best-effort type unwrapping (e.g., `Integer` → `int`) when the op expects native types." Documentation also referenced wrapper types `Integer`, `String`, `DecimalValue` that don't exist.

**Implementation:** No wrapper types exist in the codebase. Native types (`int`, `str`, `Decimal`) are stored and passed directly to ops without any wrapping or unwrapping. The executor's `_invoke_op()` method passes manifest values directly to ops without type conversion.

**Resolution:** All references to wrapper types and unwrapping have been removed from documentation. Architecture.md has been updated to state that native types are passed directly. Examples have been updated to use native types. The wrapping table in executor.md has been removed.

**Source:** `executor.py` `_invoke_op()` method (lines 138-192) — no unwrapping logic exists.

---

### F-08: `bool` Wrapping Loses Type Information [RESOLVED]

**Original issue:** Documentation referenced `to_cacheable()` function that wraps `bool` values as `Integer(1)`, but this function doesn't exist.

**Implementation:** No `to_cacheable()` function exists. Native `bool` values are stored directly without any wrapping. The `is_cacheable()` function validates that `bool` is cacheable, but no wrapping occurs.

**Resolution:** References to `to_cacheable()` have been removed from documentation. The wrapping table in executor.md has been removed. Documentation now accurately reflects that native `bool` values are stored directly.

**Source:** `cacheable.py` — only `is_cacheable()` exists, no `to_cacheable()` function.

---

### F-09: Container Wrapping Not Implemented [RESOLVED]

**Original issue:** Documentation referenced `to_cacheable()` function that raises `NotImplementedError` for container types, but this function doesn't exist.

**Implementation:** No `to_cacheable()` function exists. Native container types (`dict`, `list`, `tuple`) are stored directly without any wrapping. The `is_cacheable()` function validates that containers with cacheable values are cacheable, but no wrapping occurs.

**Resolution:** References to `to_cacheable()` have been removed from documentation. The wrapping table in executor.md has been removed. Documentation now accurately reflects that native container types are stored directly.

**Source:** `cacheable.py` — only `is_cacheable()` exists, no `to_cacheable()` function.

---

### F-10: `None` Wrapping Not Implemented [RESOLVED]

**Original issue:** Documentation said `is_cacheable(None)` returns `True` and `hash_value(None)` returns a valid hash. Implementation was thought to have `to_cacheable(None)` raising `NotImplementedError`, implying ops could not return `None` and context could not contain `None`.

**Implementation:** No `to_cacheable()` function exists (consistent with F-07, F-08, F-09). `is_cacheable(None)` returns `True`; `hash_value(None)` returns a valid hash. Native types including `None` are stored directly without wrapping. Ops may return `None` and context may contain `None` — both are validated by `is_cacheable()` and pass.

**Resolution:** F-10 was based on a non-existent `to_cacheable()` function. The implementation supports `None` throughout. Marked resolved.

**Source:** `cacheable.py` — only `is_cacheable()` exists; `None` is explicitly allowed (lines 52–54).

---

**Cross-references:** See also [expressions.md Implementation Flags](./expressions.md#9-implementation-flags) for F-01 through F-06.
