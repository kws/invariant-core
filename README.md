# Invariant

A Python-based deterministic execution engine for directed acyclic graphs (DAGs). Invariant treats every operation as a pure function, providing aggressive caching, deduplication, and bit-for-bit reproducibility.

Invariant was motivated by the need for deterministic graphics pipelines: icons, badges, dynamic UI components, and data visualizations where aggressive caching and bit-for-bit reproducibility matter. Critically, layouts can be described without a final size—size is injected at execution time and everything else is derived via the expression language. The engine itself is domain-agnostic; domain implementations like [Invariant GFX](https://github.com/kws/invariant-gfx) provide graphics ops and plug in via the op registry.

## Features

- **Aggressive Caching**: Artifacts are reused across runs if inputs match
- **Deduplication**: Identical operations execute only once
- **Reproducibility**: Bit-for-bit identical outputs across runs
- **Ephemeral nodes**: Set `cache=False` to skip caching for frequently-changing outputs and their downstream dependents
- **Immutability**: Artifacts are frozen once created
- **Determinism**: Operations rely only on explicit inputs
- **Serializable graphs**: Versioned JSON wire format for storage, transmission, and interoperability
- **Demand execution**: Callers request one or more outputs; unreachable graph branches are skipped
- **Conditional composition**: `SwitchNode` selects graph-local branches without touching inactive branches
- **YAML authoring**: Optional human-editable graph documents, including resource-backed subgraph grafting

## Installation

```bash
# From PyPI
pip install invariant-core

# Optional YAML authoring and resource-backed subgraph grafting
pip install invariant-core[yaml,resources]

# From source
git clone https://github.com/kws/invariant-core
cd invariant-core
uv sync
```

## Quick Start

```python
from invariant import Executor, Node, OpRegistry, cel, ref
from invariant.ops import stdlib
from invariant.store.memory import MemoryStore

# Create registry and register operations
registry = OpRegistry()
registry.register_package("stdlib", stdlib)

# Pipeline: compute (3 * 4) + (5 * 6), then scale the total
#
#   ab ──┐
#        ├── total ── scaled
#   cd ──┘
#
# Literal values flow directly into params — no wrapper nodes needed.
# ref()  passes a computed artifact to a downstream op.
# cel()  evaluates a CEL (Common Expression Language) expression against
#        upstream artifacts, useful for extracting or transforming values
#        without a dedicated op.
graph = {
    "ab": Node(
        op_name="stdlib:multiply",
        params={"a": 3, "b": 4},                    # literal inputs
        deps=[]
    ),
    "cd": Node(
        op_name="stdlib:multiply",
        params={"a": 5, "b": 6},                    # literal inputs
        deps=[]
    ),
    "total": Node(
        op_name="stdlib:add",
        params={"a": ref("ab"), "b": ref("cd")},    # ref() passes artifacts directly
        deps=["ab", "cd"]
    ),
    "scaled": Node(
        op_name="stdlib:multiply",
        params={"a": ref("total"), "b": cel("ab + cd")},  # cel() computes from upstreams
        deps=["ab", "cd", "total"]
    ),
}

# Execute requested outputs
store = MemoryStore()
executor = Executor(registry=registry, store=store)
results = executor.execute(graph, ["ab", "cd", "total", "scaled"])

print(results["ab"])      # 12
print(results["cd"])      # 30
print(results["total"])   # 42
print(results["scaled"])  # 42 * (12 + 30) = 1764
```

## Architecture

Invariant separates graph definition from demand execution in two phases:

1. **Phase 1: Context Resolution** - Builds input manifests for active nodes
2. **Phase 2: Action Execution** - Executes operations or retrieves from cache

### Documentation

| Document | Description |
|:--|:--|
| [docs/architecture.md](docs/architecture.md) | System overview, design philosophy, and reference test pipeline |
| [docs/expressions.md](docs/expressions.md) | **Normative reference** for `ref()`, `cel()`, `${...}` parameter markers and the CEL expression language |
| [docs/executor.md](docs/executor.md) | **Normative reference** for demand execution, graph shaking, caching, and artifact storage |
| [docs/serialization.md](docs/serialization.md) | **Normative reference** for graph JSON/YAML documents, data URIs, Node, SubGraphNode, SwitchNode, ref, and cel |
| [examples/README.md](examples/README.md) | Runnable examples with walkthroughs, DAG diagrams, and run instructions |
| [AGENTS.md](AGENTS.md) | Quick-start guide for AI agents working with this codebase |

## Development

```bash
# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=src --cov-report=html

# Run linting
uv run ruff check src/ tests/

# Format code
uv run ruff format src/ tests/
```

## License

MIT License - see [LICENSE](LICENSE) for details.
