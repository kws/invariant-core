"""Example: Commutative operation canonicalization.

This example demonstrates how to use min() and max() CEL functions to
canonicalize operand order for commutative operations, ensuring cache hits
regardless of how dependencies are declared or referenced.

See examples/README.md for a detailed walkthrough.
"""

import argparse
from pathlib import Path

from invariant import Executor, Node, OpRegistry, cel
from invariant.ops.stdlib import add, identity
from invariant.store.chain import ChainStore
from invariant.store.disk import DiskStore
from invariant.store.memory import MemoryStore


def main():
    parser = argparse.ArgumentParser(
        description="Demonstrate commutative operation canonicalization"
    )
    parser.add_argument(
        "--x",
        type=int,
        default=7,
        help="First value for addition (default: 7)",
    )
    parser.add_argument(
        "--y",
        type=int,
        default=3,
        help="Second value for addition (default: 3)",
    )
    parser.add_argument(
        "--store",
        type=str,
        choices=["memory", "disk", "chain"],
        default="memory",
        help=(
            "Store type: memory (ephemeral), disk (persistent), "
            "or chain (memory+disk) (default: memory)"
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="Cache directory for disk/chain stores (default: .invariant/cache/)",
    )
    args = parser.parse_args()

    # Register operations
    registry = OpRegistry()
    registry.register("stdlib:identity", identity)
    registry.register("stdlib:add", add)

    # Define the graph
    graph = {
        "x": Node(
            op_name="stdlib:identity",
            params={"value": args.x},
            deps=[],
        ),
        "y": Node(
            op_name="stdlib:identity",
            params={"value": args.y},
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

    # Create store based on selected type
    if args.store == "memory":
        store = MemoryStore()
        cache_dir_str = None
    elif args.store == "disk":
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        store = DiskStore(cache_dir=cache_dir)
        cache_dir_str = str(store.cache_dir)
    else:  # chain
        cache_dir = Path(args.cache_dir) if args.cache_dir else None
        l2 = DiskStore(cache_dir=cache_dir)
        store = ChainStore(l2=l2)
        cache_dir_str = str(l2.cache_dir)

    executor = Executor(registry=registry, store=store)
    results = executor.execute(graph, ["sum_xy", "sum_yx"])

    # Both nodes resolve to the same manifest (min, max order)
    # Same digest -> single execution, cache hit for the second node
    min_val = min(args.x, args.y)
    max_val = max(args.x, args.y)
    print(
        f"✓ Both sum_xy and sum_yx resolve to manifest {{a: {min_val}, b: {max_val}}}"
    )
    print(f"  sum_xy result: {results['sum_xy']}")
    print(f"  sum_yx result: {results['sum_yx']}")
    print(f"  Results are equal: {results['sum_xy'] == results['sum_yx']}")

    print("\nCanonicalization pattern:")
    print("  - Use min() and max() in CEL expressions to ensure deterministic ordering")
    print(
        "  - Ensures cache hits for commutative operations regardless of operand order"
    )
    print("  - Both nodes produce the same manifest hash, triggering deduplication")

    # Print cache statistics
    stats = store.stats
    total_checks = stats.hits + stats.misses
    hit_rate = (stats.hits / total_checks * 100) if total_checks > 0 else 0.0

    print("\n" + "=" * 60)
    print("Cache Statistics:")
    print(f"  Store type: {args.store}")
    if cache_dir_str:
        print(f"  Cache directory: {cache_dir_str}")
    print(f"  Hits: {stats.hits}")
    print(f"  Misses: {stats.misses}")
    print(f"  Puts: {stats.puts}")
    print(f"  Hit rate: {hit_rate:.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
