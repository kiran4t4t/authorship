#!/usr/bin/env python3
"""CLI: run the fleet-size sweep and print a table.

Usage:
    python run_sweep.py --sizes 1,2,4,8,16 --turns 6 --rows 200000
"""

from __future__ import annotations

import argparse
import json

from agentlake.phases import HUMAN_BASELINE, PhaseModel
from agentlake.sweep import scaling_exponent, sweep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="1,2,4,8,16", help="comma-separated fleet sizes")
    ap.add_argument("--turns", type=int, default=96, help="TOTAL turns across the fleet (held constant across sizes)")
    ap.add_argument("--rows", type=int, default=200_000, help="fact table rows")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cache", type=int, default=512, help="shared cache capacity")
    ap.add_argument("--skew", type=float, default=1.1, help="Zipf exponent for question draw")
    ap.add_argument("--baseline", action="store_true", help="run the human control instead")
    ap.add_argument("--json", metavar="PATH", help="also write raw results as JSON")
    args = ap.parse_args()

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    model = HUMAN_BASELINE if args.baseline else PhaseModel()

    print(f"model provenance: {model.provenance}")
    print(f"calibrated: {model.is_calibrated}\n")

    points = sweep(
        sizes,
        total_turns=args.turns,
        model=model,
        n_sales=args.rows,
        seed=args.seed,
        cache_capacity=args.cache,
        question_skew=args.skew,
    )

    hdr = (
        f"{'fleet':>6} {'answered':>9} {'issued':>7} {'fanout':>7} "
        f"{'scan/q':>12} {'scan/query':>11} {'waste':>7} {'aband':>7} "
        f"{'text$':>7} {'plan$':>7} {'skew':>6}"
    )
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for p in points:
        r = p.report
        print(
            f"{p.fleet_size:>6} {r.questions_answered:>9} {r.queries_issued:>7} "
            f"{r.queries_per_question:>7.2f} {r.scanned_per_question:>12,.0f} "
            f"{r.scanned_per_query:>11,.0f} {r.waste_fraction:>6.1%} "
            f"{r.abandoned_fraction:>6.1%} {r.text_cache.hit_rate:>6.1%} "
            f"{r.plan_cache.hit_rate:>6.1%} {r.catalog_skew:>6.3f}"
        )
        rows.append(
            {
                "fleet_size": p.fleet_size,
                "questions_answered": r.questions_answered,
                "queries_issued": r.queries_issued,
                "queries_per_question": r.queries_per_question,
                "scanned_per_question": r.scanned_per_question,
                "scanned_per_query": r.scanned_per_query,
                "waste_fraction": r.waste_fraction,
                "abandoned_fraction": r.abandoned_fraction,
                "text_cache_hit_rate": r.text_cache.hit_rate,
                "plan_cache_hit_rate": r.plan_cache.hit_rate,
                "catalog_requests": r.catalog_requests,
                "catalog_skew": r.catalog_skew,
                "by_phase": r.by_phase,
                "scanned_by_phase": r.scanned_by_phase,
            }
        )

    k = scaling_exponent(points)
    print(f"\nscaling exponent k (cost/question ~ fleet**k): {k:+.4f}")
    print(
        "  k ~ 0 => per-question cost flat in fleet size (aggregate scales linearly)\n"
        "  k > 0 => per-question cost rises with fleet size (superlinear aggregate)"
    )

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(
                {"scaling_exponent": k, "provenance": model.provenance, "points": rows},
                fh,
                indent=2,
            )
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
