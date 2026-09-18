#!/usr/bin/env python3
"""CLI: sweep fleet size under real concurrent execution.

Usage:
    python run_concurrency.py --sizes 1,2,4,8,16,32 --turns 192 --threads 4
"""

from __future__ import annotations

import argparse
import json
import math


def _exponent(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 0.0


def main() -> None:
    from agentlake.concurrent import concurrency_sweep

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="1,2,4,8,16,32")
    ap.add_argument("--turns", type=int, default=192, help="total turns across fleet")
    ap.add_argument("--rows", type=int, default=400_000)
    ap.add_argument("--threads", type=int, default=4, help="shared DuckDB worker threads")
    ap.add_argument("--memory", default="512MB", help="shared DuckDB memory budget")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", metavar="PATH")
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    res = concurrency_sweep(
        sizes,
        total_turns=args.turns,
        n_sales=args.rows,
        seed=args.seed,
        threads=args.threads,
        memory_limit=args.memory,
    )

    print(f"shared budget: {args.threads} worker threads, {args.memory}\n")
    hdr = (
        f"{'fleet':>6}{'answered':>9}{'scan/q':>12}{'mean_ms':>9}"
        f"{'p95_ms':>9}{'makespan_s':>11}{'qps':>8}"
    )
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for r in res:
        print(
            f"{r.fleet_size:>6}{r.report.questions_answered:>9}"
            f"{r.report.scanned_per_question:>12,.0f}{r.mean_latency_ms:>9.2f}"
            f"{r.p95_latency_ms:>9.2f}{r.makespan_ms / 1000:>11.2f}{r.throughput_qps:>8.0f}"
        )
        rows.append(
            {
                "fleet_size": r.fleet_size,
                "questions_answered": r.report.questions_answered,
                "scanned_per_question": r.report.scanned_per_question,
                "mean_latency_ms": r.mean_latency_ms,
                "p95_latency_ms": r.p95_latency_ms,
                "makespan_ms": r.makespan_ms,
                "throughput_qps": r.throughput_qps,
            }
        )

    xs = [math.log(r.fleet_size) for r in res]
    ks = {
        "scan_per_question": _exponent(
            xs, [math.log(r.report.scanned_per_question) for r in res]
        ),
        "mean_latency": _exponent(xs, [math.log(r.mean_latency_ms) for r in res]),
        "p95_latency": _exponent(xs, [math.log(r.p95_latency_ms) for r in res]),
    }
    print("\nscaling exponents (metric ~ fleet**k):")
    for name, k in ks.items():
        print(f"  k[{name}] = {k:+.4f}")
    print(
        "\n  scan work additive (k~0) but latency superlinear (k>0) means the fleet\n"
        "  creates no extra work, it queues for a saturated resource pool."
    )

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"exponents": ks, "points": rows}, fh, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
