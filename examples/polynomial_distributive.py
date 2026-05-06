"""Example: Polynomial distributive law verification pipeline.

This example demonstrates Invariant's core capabilities using polynomial arithmetic
to verify the algebraic identity: (p + q) * r == p*r + q*r

See examples/README.md for a detailed walkthrough and DAG diagram.
"""

import argparse
from pathlib import Path

from invariant import Executor, Node, OpRegistry, ref
from invariant.ops import poly
from invariant.store.chain import ChainStore
from invariant.store.disk import DiskStore
from invariant.store.memory import MemoryStore


def parse_coefficients(s: str) -> list[int]:
    """Parse comma-separated coefficients string into list of integers."""
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def main():
    parser = argparse.ArgumentParser(
        description="Verify polynomial distributive law: (p + q) * r == p*r + q*r"
    )
    parser.add_argument(
        "--p-coeffs",
        type=str,
        default="1,2,1",
        help=(
            "Coefficients for polynomial p "
            "(comma-separated, default: 1,2,1 for x^2+2x+1)"
        ),
    )
    parser.add_argument(
        "--q-coeffs",
        type=str,
        default="3,0,-1",
        help=(
            "Coefficients for polynomial q "
            "(comma-separated, default: 3,0,-1 for -x^2+3)"
        ),
    )
    parser.add_argument(
        "--r-coeffs",
        type=str,
        default="1,1",
        help="Coefficients for polynomial r (comma-separated, default: 1,1 for x+1)",
    )
    parser.add_argument(
        "--x",
        type=int,
        default=5,
        help="Evaluation point for polynomials (default: 5)",
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

    # Parse coefficients
    p_coeffs = parse_coefficients(args.p_coeffs)
    q_coeffs = parse_coefficients(args.q_coeffs)
    r_coeffs = parse_coefficients(args.r_coeffs)

    # Register polynomial operations
    registry = OpRegistry()
    registry.register_package("poly", poly)

    # Define the graph
    graph = {
        # Create polynomials from coefficient lists
        "p": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": p_coeffs},
            deps=[],
        ),
        "q": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": q_coeffs},
            deps=[],
        ),
        "r": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": r_coeffs},
            deps=[],
        ),
        # Left branch: (p + q) * r
        "p_plus_q": Node(
            op_name="poly:add",
            params={"a": ref("p"), "b": ref("q")},
            deps=["p", "q"],
        ),
        "lhs": Node(
            op_name="poly:multiply",
            params={"a": ref("p_plus_q"), "b": ref("r")},
            deps=["p_plus_q", "r"],
        ),
        # Right branch: p*r + q*r
        "pr": Node(
            op_name="poly:multiply",
            params={"a": ref("p"), "b": ref("r")},
            deps=["p", "r"],
        ),
        "qr": Node(
            op_name="poly:multiply",
            params={"a": ref("q"), "b": ref("r")},
            deps=["q", "r"],
        ),
        "rhs": Node(
            op_name="poly:add",
            params={"a": ref("pr"), "b": ref("qr")},
            deps=["pr", "qr"],
        ),
        # Evaluate both sides at x
        "eval_lhs": Node(
            op_name="poly:evaluate",
            params={"poly": ref("lhs"), "x": args.x},
            deps=["lhs"],
        ),
        "eval_rhs": Node(
            op_name="poly:evaluate",
            params={"poly": ref("rhs"), "x": args.x},
            deps=["rhs"],
        ),
        # Bonus: derivative chain
        "d1": Node(
            op_name="poly:derivative",
            params={"poly": ref("lhs")},
            deps=["lhs"],
        ),
        "d2": Node(
            op_name="poly:derivative",
            params={"poly": ref("d1")},
            deps=["d1"],
        ),
        "eval_d2": Node(
            op_name="poly:evaluate",
            params={"poly": ref("d2"), "x": args.x},
            deps=["d2"],
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
    results = executor.execute(
        graph,
        ["lhs", "rhs", "eval_lhs", "eval_rhs", "eval_d2"],
    )

    # Verify distributive law: (p + q) * r == p*r + q*r
    assert results["lhs"].coefficients == results["rhs"].coefficients
    print("✓ Distributive law verified: (p + q) * r == p*r + q*r")
    print(f"  LHS coefficients: {list(results['lhs'].coefficients)}")
    print(f"  RHS coefficients: {list(results['rhs'].coefficients)}")

    # Verify numeric equality at x
    assert results["eval_lhs"] == results["eval_rhs"]
    equality = f"{results['eval_lhs']} == {results['eval_rhs']}"
    print(f"✓ Numeric equality at x={args.x}: {equality}")

    # Verify derivative chain
    assert isinstance(results["eval_d2"], int)
    print(f"✓ Second derivative evaluated at x={args.x}: {results['eval_d2']}")

    print("\nPipeline features exercised:")
    print("  - Chain: p -> p_plus_q -> lhs -> eval_lhs")
    print("  - Branch (fan-out): r feeds lhs, pr, and qr")
    print("  - Merge (fan-in): rhs = poly:add(pr, qr)")
    print("  - Deep chains: lhs -> d1 -> d2 -> eval_d2")
    print("  - Re-entrant patterns: d1 and eval_lhs both depend on lhs")

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
