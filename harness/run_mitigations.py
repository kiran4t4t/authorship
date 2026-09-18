#!/usr/bin/env python3
"""CLI: run the mitigation ablation and print a table.

Usage:
    python run_mitigations.py --fleet 8 --turns 192 --rows 400000
"""

from __future__ import annotations

import argparse
import json

from agentlake.mitigations import CACHE_COUNTERFACTUAL, evaluate


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fleet", type=int, default=8)
    ap.add_argument("--turns", type=int, default=192, help="total turns across fleet")
    ap.add_argument("--rows", type=int, default=400_000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", metavar="PATH")
    args = ap.parse_args()

    results = evaluate(
        fleet_size=args.fleet,
        total_turns=args.turns,
        n_sales=args.rows,
        seed=args.seed,
    )
    base_report = results[0][1]
    base_cost = base_report.scanned_per_question

    hdr = (
        f"{'mitigation':<20}{'scan/q':>12}{'vs base':>9}{'fanout':>8}"
        f"{'waste':>8}{'catalog':>9}{'answerable':>11}"
    )
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for mitigation, report, answerable in results:
        delta = (report.scanned_per_question / base_cost - 1.0) if base_cost else 0.0
        print(
            f"{mitigation.key:<20}{report.scanned_per_question:>12,.0f}"
            f"{delta:>+8.1%} {report.queries_per_question:>7.2f}"
            f"{report.waste_fraction:>7.1%} {report.catalog_requests:>8,}"
            f"{answerable:>10.0%}"
        )
        rows.append(
            {
                "key": mitigation.key,
                "name": mitigation.name,
                "scanned_per_question": report.scanned_per_question,
                "delta_vs_baseline": delta,
                "queries_per_question": report.queries_per_question,
                "waste_fraction": report.waste_fraction,
                "catalog_requests": report.catalog_requests,
                "answerable_fraction": answerable,
                "questions_answered": report.questions_answered,
                "text_cache_hit_rate": report.text_cache.hit_rate,
                "plan_cache_hit_rate": report.plan_cache.hit_rate,
                "text_rows_saved": report.text_cache.rows_scanned_saved,
                "plan_rows_saved": report.plan_cache.rows_scanned_saved,
                "capability_cost": mitigation.capability_cost,
            }
        )

    print(f"\n--- cache counterfactual (baseline run) ---")
    print(
        f"exact-text cache would avoid {base_report.text_cache.rows_scanned_saved:,} rows; "
        f"plan-keyed cache {base_report.plan_cache.rows_scanned_saved:,} rows "
        f"(+{base_report.plan_cache.rows_scanned_saved - base_report.text_cache.rows_scanned_saved:,})"
    )
    print(CACHE_COUNTERFACTUAL)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"mitigations": rows}, fh, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
