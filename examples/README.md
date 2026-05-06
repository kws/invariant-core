# Examples

This directory contains runnable examples demonstrating Invariant's core capabilities.

## Serialized Graphs

The [`serialized/`](./serialized/) directory contains JSON graph envelopes and a
YAML authoring example that mirror the default arguments of the Python examples.
They can be executed with the `invariant` CLI or module entry point:

```bash
uv run invariant examples/serialized/commutative_canonicalization.json --pick sum_xy --pick sum_yx
uv run python -m invariant examples/serialized/polynomial_distributive.json --pick eval_lhs
uv run invariant examples/serialized/yaml_authoring_example.yaml
```

The serialized graphs use the same model described in
[`docs/serialization.md`](../docs/serialization.md): JSON is canonical, while
YAML is a supported load-only authoring format with explicit tags. Graph
documents may carry an optional default `output`; otherwise the CLI requires one
or more `--pick NODE_ID` options. YAML authoring can also graft reusable
subgraphs from JustMyResource with `!subgraph` when `invariant-core[yaml,resources]`
is installed; the loaded graph is still serialized canonically as a single
atomic JSON document.

Use `--context FILE` for external context values and repeat `--param KEY=VALUE`
to override or add context values from the CLI. Param values accept JSON scalars
and objects, Invariant JSON markers, or bare strings. Missing active external
graph dependencies are errors unless supplied explicitly, including explicit
`null`. Use `--output FILE` to write the result to a file. In auto mode, a
single selected `ICacheable` output is written as a binary artifact stream, while
multiple outputs and native outputs are written as JSON.

## Polynomial Distributive Law

**File:** [`polynomial_distributive.py`](./polynomial_distributive.py)
**Serialized graph:** [`serialized/polynomial_distributive.json`](./serialized/polynomial_distributive.json)

Demonstrates Invariant's core capabilities using polynomial arithmetic to verify the algebraic identity **(p + q) \* r == p\*r + q\*r**. This example exercises chains, branches, merges, and deduplication without requiring external dependencies.

**Run:**
```bash
uv run python examples/polynomial_distributive.py
```

The example creates three polynomials from coefficient lists, then computes both sides of the distributive law through different paths in the DAG:

**Basic node creation:** Nodes like `p`, `q`, and `r` use `poly:from_coefficients` to create polynomials from coefficient lists:
```python
"p": Node(
    op_name="poly:from_coefficients",
    params={"coefficients": [1, 2, 1]},  # x^2 + 2x + 1
    deps=[],
)
```

**Using `ref()` for artifact passthrough:** The `p_plus_q` node passes entire polynomial artifacts:
```python
"p_plus_q": Node(
    op_name="poly:add",
    params={"a": ref("p"), "b": ref("q")},
    deps=["p", "q"],
)
```

**Chain patterns:** Linear sequences like `p -> p_plus_q -> lhs -> eval_lhs` demonstrate how artifacts flow through chains.

**Branch/merge patterns:** The right branch shows fan-out and fan-in: `p` and `r` both feed into `pr`, while `q` and `r` feed into `qr`, then both `pr` and `qr` merge into `rhs`.

**Deep chains:** The derivative chain `lhs -> d1 -> d2 -> eval_d2` demonstrates 4-deep linear dependencies.

### DAG Structure

```mermaid
flowchart TD
    subgraph inputs [Inputs]
        pCoeffs["p_coeffs: from_coefficients [1, 2, 1]"]
        qCoeffs["q_coeffs: from_coefficients [3, 0, -1]"]
        rCoeffs["r_coeffs: from_coefficients [1, 1]"]
    end

    subgraph leftBranch [Left Branch: chain then merge]
        pqSum["p_plus_q: poly:add(p, q)"]
        lhsMul["lhs: poly:multiply(p_plus_q, r)"]
    end

    subgraph rightBranch [Right Branch: fan-out then merge]
        prMul["pr: poly:multiply(p, r)"]
        qrMul["qr: poly:multiply(q, r)"]
        rhsSum["rhs: poly:add(pr, qr)"]
    end

    subgraph evaluate [Evaluate both at x=5]
        evalLhs["eval_lhs: poly:evaluate(lhs, 5)"]
        evalRhs["eval_rhs: poly:evaluate(rhs, 5)"]
    end

    pCoeffs --> pqSum
    qCoeffs --> pqSum
    pqSum --> lhsMul
    rCoeffs --> lhsMul

    pCoeffs --> prMul
    rCoeffs --> prMul
    qCoeffs --> qrMul
    rCoeffs --> qrMul
    prMul --> rhsSum
    qrMul --> rhsSum

    lhsMul --> evalLhs
    rhsSum --> evalRhs
```

### Pipeline Features Exercised

| Pipeline Feature | Where It Appears | Notes |
|:--|:--|:--|
| **Chain** | `p -> p_plus_q -> lhs -> eval_lhs` | 3-deep linear chain |
| **Branch (fan-out)** | `r` feeds `lhs`, `pr`, and `qr`; `p` feeds `p_plus_q` and `pr` | Single artifact used by multiple downstream nodes |
| **Merge (fan-in)** | `rhs = poly:add(pr, qr)` | Two branches converge into one node |
| **Deduplication** | If `p == q`, then `pr` and `qr` produce identical manifests | Same digest triggers single execution |
| **Cache reuse** | Running the same graph twice skips all ops on the second run | All artifacts retrieved from cache |
| **Deep chains** | `lhs -> d1 -> d2 -> eval_d2` | 4-deep chain with derivative operations |
| **Re-entrant patterns** | `d1` and `eval_lhs` both depend on `lhs` | Same artifact reused across multiple paths |

---

## Commutative Canonicalization

**File:** [`commutative_canonicalization.py`](./commutative_canonicalization.py)
**Serialized graph:** [`serialized/commutative_canonicalization.json`](./serialized/commutative_canonicalization.json)

For commutative operations like addition or multiplication, the order of operands does not affect the result, but it *does* affect the manifest hash. Consider two nodes computing the same sum with arguments in different order:

* `add(x, y)` → manifest `{a: x_value, b: y_value}` → digest `abc123...`
* `add(y, x)` → manifest `{a: y_value, b: x_value}` → digest `def456...` (cache miss!)

The engine correctly treats these as distinct computations because it has no knowledge of commutativity. The manifest is an ordered dictionary, so different parameter orderings produce different digests, even when the mathematical result is identical.

**Solution:** Use `min()` and `max()` in `cel()` expressions to canonicalize operand order.

**Run:**
```bash
uv run python examples/commutative_canonicalization.py
```

**Example pattern:**

```python
from invariant import Node, OpRegistry, cel

graph = {
    "x": Node(
        op_name="stdlib:identity",
        params={"value": 7},
        deps=[],
    ),
    "y": Node(
        op_name="stdlib:identity",
        params={"value": 3},
        deps=[],
    ),

    # First node: explicitly uses x, y order
    "sum_xy": Node(
        op_name="stdlib:add",
        params={"a": cel("min(x, y)"), "b": cel("max(x, y)")},
        deps=["x", "y"],
    ),

    # Second node: uses y, x order in expressions — same result!
    "sum_yx": Node(
        op_name="stdlib:add",
        params={"a": cel("min(y, x)"), "b": cel("max(y, x)")},
        deps=["x", "y"],
    ),
}

# Both sum_xy and sum_yx resolve to manifest {a: 3, b: 7}
# Same digest -> single execution, cache hit for the second node
```

Both nodes resolve to the same manifest `{a: 3, b: 7}` because `min(x, y)` and `min(y, x)` both evaluate to `3`, and `max(x, y)` and `max(y, x)` both evaluate to `7`. The canonical ordering ensures cache hits regardless of how the dependencies are declared or referenced in expressions.

**Note:** `min()` and `max()` are custom CEL functions registered alongside `decimal()`, available in the expression evaluation scope. They work with any comparable types (integers, decimals, strings) and ensure deterministic canonicalization for commutative operations.
