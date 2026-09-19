#!/usr/bin/env python3
"""Consistency checks for PAPER.md against the harness results.

Run before any submission. Checks section numbering, citation closure,
cross-reference validity, abstract length, leftover drafting markers, and every
headline figure against the JSON the harness actually produced.

Usage:
    python check_paper.py ../papers/multi-agent-lakehouse/PAPER.md
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

RESULTS = pathlib.Path(__file__).parent / "results"
ABSTRACT_MAX_WORDS = 250


def _load(name: str) -> dict:
    return json.loads((RESULTS / f"{name}.json").read_text())


def check(paper: pathlib.Path) -> list[str]:
    """Return a list of problems; empty means the paper is internally consistent."""
    t = paper.read_text()
    bad: list[str] = []

    heads = [l for l in t.splitlines() if l.startswith("## ")]
    nums = [int(m.group(1)) for h in heads if (m := re.match(r"## (\d+)\.", h))]
    if nums != list(range(1, len(nums) + 1)):
        bad.append(f"section numbering not sequential: {nums}")

    split = t.index("## 10. References")
    used: set[int] = set()
    for m in re.finditer(r"\[(\d+)\](?:-\[(\d+)\])?", t[:split]):
        lo, hi = int(m.group(1)), m.group(2)
        used.update(range(lo, int(hi) + 1) if hi else [lo])
    defined = {int(m.group(1)) for m in re.finditer(r"^(\d+)\.\s", t[split:], re.M)}
    if used - defined:
        bad.append(f"cited but undefined: {sorted(used - defined)}")
    if defined - used:
        bad.append(f"defined but uncited: {sorted(defined - used)}")

    valid = {str(n) for n in nums} | {
        m.group(1) for m in re.finditer(r"^### (\d+\.\d+)", t, re.M)
    }
    dangling = {m.group(1) for m in re.finditer(r"Section (\d+(?:\.\d+)?)", t)} - valid
    if dangling:
        bad.append(f"dangling cross-references: {sorted(dangling)}")

    abstract = t[t.index("## Abstract") : t.index("## 1. Introduction")]
    words = len(abstract.replace("## Abstract", "").split())
    if words > ABSTRACT_MAX_WORDS:
        bad.append(f"abstract is {words} words (max {ABSTRACT_MAX_WORDS})")

    if markers := re.findall(r"\[\[", t):
        bad.append(f"{len(markers)} unresolved drafting markers remain")

    # Headline figures must match what the harness produced.
    ag, hu = _load("agentic")["points"][0], _load("human")["points"][0]
    co, mi = _load("concurrency"), {m["key"]: m for m in _load("mitigations")["mitigations"]}
    expect = {
        "2.58x": round(ag["scanned_per_question"] / hu["scanned_per_question"], 2) == 2.58,
        "32% fewer rows per query": round(
            1 - ag["scanned_per_query"] / hu["scanned_per_query"], 2
        ) == 0.32,
        "969,865": f"{ag['scanned_per_question']:,.0f}" == "969,865",
        "fleet^0.76": round(co["exponents"]["mean_latency"], 2) == 0.76,
        "k = -0.02 (scan)": round(co["exponents"]["scan_per_question"], 2) == -0.02,
        "-35.9% profiles": round(mi["profile_cache"]["delta_vs_baseline"], 3) == -0.359,
        "+3.4% correction budget": round(
            mi["correction_budget"]["delta_vs_baseline"], 3
        ) == 0.034,
        "15% answerable": round(mi["semantic_layer"]["answerable_fraction"], 2) == 0.15,
    }
    for label, ok in expect.items():
        if not ok:
            bad.append(f"figure drifted from harness results: {label}")
        elif label.strip("+-%0123456789., x^flet=k()") and label not in t:
            pass  # presence is checked below for the numeric forms only

    for literal in ("2.58x", "969,865", "375,731", "35.9%", "3.4%", "fleet^0.76"):
        if literal not in t:
            bad.append(f"headline figure missing from paper text: {literal}")

    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paper", type=pathlib.Path)
    args = ap.parse_args()
    problems = check(args.paper)
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("paper is internally consistent and matches the harness results")


if __name__ == "__main__":
    main()
