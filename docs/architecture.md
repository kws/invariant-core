# **Architecture: Invariant**

## **1\. Overview**

**Invariant** is a Python-based deterministic execution engine for directed acyclic graphs (DAGs). It is designed to orchestrate complex computational workflows—such as media transcoding, rendering, or scientific data processing—where the cost of re-computation is high.

### **1.1 Core Value Proposition**

Unlike task schedulers (e.g., Apache Airflow, Luigi) which focus on *when* to run tasks, Invariant focuses on *what* is being produced. It treats every operation as a pure function: Op(Input) \= Output.

By enforcing **hermeticity** (no hidden inputs) and **immutability** (read-only outputs), Invariant provides:

1. **Aggressive Caching:** Artifacts are reused across runs and even across different pipelines if their inputs match.  
2. **Deduplication:** Identical operations requested by different parts of the graph are executed only once.  
3. **Reproducibility:** A workflow run today produces bit-for-bit the same output as one run next year (assuming the underlying Op implementation is deterministic).

### **1.2 Influences & Similar Systems**

* **Google Bazel (Blaze):** Invariant adopts Bazel's concept of the "Action Graph" and "Artifact Cache." Just as Bazel caches build artifacts (.o files) based on the hash of source files and compiler flags, Invariant caches data artifacts based on the hash of input data and operation parameters.  
* **TensorFlow:** Invariant mirrors the "Computation Graph" model where nodes represent operations and edges represent the flow of immutable data tensors, separating the graph definition phase from the execution phase.  
* **Functional Programming:** The engine enforces a side-effect-free model where "mutation" is modeled as creating a new version of data.

## **2\. Core Philosophy & Constraints**

### **2.1 The "Immutability Contract"**

* **Principle:** Once an **Artifact** is generated, it is frozen.  
* **Constraint:** Downstream nodes cannot modify an upstream artifact in place. They must consume it and produce a *new* artifact.

### **2.2 The "Determinism Contract"**

* **Principle:** An **Op** must rely *only* on the data provided in its **Input Manifest**.  
* **Constraint:** Access to global state, system clocks (time.now()), or random number generators (random.random()) inside an Op is forbidden unless those values are passed in as explicit inputs from the graph's root.

### **2.3 The "Strict Numeric" Policy**

* **Problem:** IEEE 754 floating-point numbers are non-deterministic across architectures and serialization formats.  
* **Constraint:** Native float types are **forbidden** in the cacheable data protocol.  
* **Solution:** Use decimal.Decimal (canonicalized to string) or integer ratios for fractional data.

## **3\. Data Protocol & Component Naming**

We strictly normalize naming to ensure clarity across the system.

| Term | Definition | Analogous Concept |
| :---- | :---- | :---- |
| **Node** | A vertex in the DAG defining *what* to do. Contains op name, params, and references to upstream deps. | Build Target |
| **Op** | The underlying Python function implementing the logic. | Kernel / Function |
| **Manifest** | The fully resolved, static dictionary of inputs for a specific Node execution. | Call Frame / Props |
| **Artifact** | The immutable output produced by an Op. | Build Artifact / Tensor |
| **Digest** | The SHA-256 hash of a Manifest. Serves as the **Identity** of a potential Artifact. | Cache Key |

### **3.1 The ICacheable Protocol**

All data passed between Nodes must adhere to this protocol to ensure valid Manifest construction.

from typing import Protocol, BinaryIO

class ICacheable(Protocol):  
    def get\_stable\_hash(self) \-\> str:  
        """  
        Returns a deterministic SHA-256 hash of the object's structural state.  
        This represents the 'Identity' of the data.  
        """  
        ...

    def to\_stream(self, stream: BinaryIO) \-\> None:  
        """  
        Serializes the object to a binary stream for persistent storage.  
        """  
        ...

    @classmethod  
    def from\_stream(cls, stream: BinaryIO) \-\> 'ICacheable':  
        """  
        Hydrates the object from a binary stream.  
        """  
        ...

## **4\. Execution Architecture**

> **Normative references:** The execution model is fully specified in [executor.md](./executor.md). The expression and parameter marker system is fully specified in [expressions.md](./expressions.md). Those documents are the source of truth; this section provides a high-level overview. Where this document and the normative references disagree, see the Implementation Flags sections in the normative references.

The execution flow is split into two distinct phases to maximize cache hits.

### **Phase 1: Context Resolution (Graph \-\> Manifest)**

The engine traverses the user-defined DAG. For each Node, it resolves param markers (`ref()`, `cel()`, `${...}`) to create an **Input Manifest**.

* **Inputs:**  
  1. Node Parameters (may contain `ref()`, `cel()`, or `${...}` markers).  
  2. Upstream Artifacts (results from deps, available for marker resolution).  
* **Process:**  
  * The engine resolves all param markers using dependency artifacts.  
  * `ref("dep")` → resolves to the ICacheable artifact.  
  * `cel("expr")` → evaluates CEL expression and returns computed value.  
  * `"${expr}"` → evaluates CEL expression and interpolates into string.  
  * The engine recursively calculates the get\_stable\_hash() for every resolved value.  
  * It assembles a canonical dictionary (sorted keys) of the resolved params.  
* **Output:** The **Manifest** (resolved params only). The hash of this Manifest becomes the **Digest** (Cache Key).

**Key Design:** Dependencies are NOT injected into the manifest directly. They are only used to resolve param markers. The manifest is built entirely from resolved params, making the data flow explicit.

### **Phase 2: Action Execution (Manifest \-\> Artifact)**

* **Step 1: Cache Lookup**  
  * Engine checks ArtifactStore.exists(Op, Digest).  <- this is essential, artifact is the output of an operation for a particular input manifest. Antother operation could in theory get the same input manifest and would return a different result.
  * *If True:* Returns the stored Artifact. **Op is strictly skipped.**  
* **Step 2: Execution**  
  * *If False:* Engine inspects the op function signature and maps manifest keys to function parameters by name (`**kwargs` dispatch).  
  * Engine passes manifest values directly to ops. Native types are stored and used directly without wrapping.  
  * Engine invokes the op with resolved arguments and validates the return value is cacheable.  
* **Step 3: Persistence**  
  * The resulting Artifact is serialized and saved to ArtifactStore under Operation and Digest.

## **5\. System Components**

### **5.1 OpRegistry**

A singleton registry mapping string identifiers to executable Python callables.

* *Role:* Decouples the "string" name in the graph definition from the actual Python code.
* *Package Registration:* Supports grouping related operations into packages via `register_package(prefix, ops)`, which registers all ops from a package under a common prefix (e.g., `"poly:add"`, `"poly:multiply"`).
* *Auto-Discovery:* The `auto_discover()` method automatically discovers and registers op packages from Python entry points (group `"invariant.ops"`), enabling third-party packages to provide operations without explicit registration.

### **5.2 Graph Resolver**

Responsible for parsing the definition and ensuring a valid DAG.

* *Role:* Cycle detection, validation, and Topological Sorting.

### **5.3 Executor**

The runtime engine.

* *Role:* Iterates the sorted nodes, manages the "Phase 1 \-\> Phase 2" loop, handles failures, and reports progress.

### **5.4 Artifact Store**

The storage abstraction.

* *Implementations:*  
  * MemoryStore: fast, ephemeral (testing). Default LRU with max_size=1000; use cache="unbounded" for no eviction.
  * NullStore: no-op store (exists always False, put no-op). Use for execution-correctness tests.
  * DiskStore: local filesystem (.invariant/cache/).
  * ChainStore: composite two-tier cache chaining MemoryStore (L1) and DiskStore (L2), with automatic promotion from L2 to L1 on cache hits.
  * CloudStore: planned for S3/GCS buckets for shared team caches (not yet implemented).

## **6\. Examples**

The `examples/` directory contains runnable examples demonstrating Invariant's core capabilities. See [`examples/README.md`](../examples/README.md) for full walkthroughs, DAG diagrams, and run instructions.

| Example | File | Demonstrates |
|:--|:--|:--|
| Polynomial Distributive Law | [`polynomial_distributive.py`](../examples/polynomial_distributive.py) | Chains, branches, merges, deduplication, deep chains |
| Commutative Canonicalization | [`commutative_canonicalization.py`](../examples/commutative_canonicalization.py) | Using `min()`/`max()` in `cel()` to canonicalize operand order for cache hits |

### **6.1 External Dependencies (Context)**

No special node type is needed to distinguish external inputs from graph-internal dependencies. The rule is simple:

* Any dependency that **is** a key in the graph is an **internal** dependency, resolved by executing that node first.
* Any dependency that **is not** a key in the graph is an **external** dependency, and **must** be provided in `context`.
* If a dependency is neither in the graph nor in `context`, execution fails with an error.

The `Executor` resolves context values lazily when an active dependency requests them, making them available to any node that declares them as a dependency. From the node's perspective, there is no difference between consuming an internal artifact and consuming an external context value — both are accessed the same way via `ref()`, `cel()`, or `${...}` expressions.

### **6.2 Parameter Markers and Expression Language**

> **Normative reference:** See [expressions.md](./expressions.md) for the complete specification of parameter markers and the CEL expression language, including all built-in functions, type conversion rules, error cases, and implementation flags.

Invariant provides three explicit mechanisms for parameter values, each with a clear purpose:

| Marker | Purpose | Resolves to |
|:--|:--|:--|
| `ref("dep")` | Artifact passthrough | The ICacheable object from dependency |
| `cel("expr")` | CEL expression evaluation | Computed value (int, str, Decimal, etc.) |
| `"text ${expr} text"` | String interpolation | Interpolated string |
| literal (`5`, `"#000"`) | Static value | Itself |

**Key Design Principle:** The manifest is built entirely from resolved params. Dependencies are NOT injected into the manifest directly — they are only available for `ref()`/`cel()` resolution within params. This makes the data flow explicit and eliminates ambiguity.

All param markers are resolved during **Phase 1 (Context Resolution)**. The expressions themselves are never cached — only their resolved results matter for cache identity. See [expressions.md](./expressions.md) for the full specification.
