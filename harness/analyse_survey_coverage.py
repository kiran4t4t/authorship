#!/usr/bin/env python3
"""Recompute the term-frequency evidence in Section 2.3 of the paper.

Section 2.3 claims that the field's own comprehensive survey does not discuss
platform-side concerns. That is a quantitative claim and should be auditable
rather than taken on trust, so this script recomputes it from the survey PDF.

Usage:
    pip install pypdf
    git clone --depth 1 https://github.com/HKUSTDial/awesome-data-agents
    python analyse_survey_coverage.py awesome-data-agents/reports/Data_Agents_Survey.pdf

Counts are case-insensitive substring matches over the full extracted text. That
is deliberately generous to the survey: "scan" matches "scanning" and also
unrelated words containing it, so a zero is a strong result and a small non-zero
count should be inspected in context rather than treated as coverage.
"""

from __future__ import annotations

import argparse
import re
import sys

PLATFORM_TERMS = (
    "throughput",
    "queue",
    "contention",
    "concurrent execution",
    "result cache",
    "cache hit",
    "scan",
    "fan-out",
    "fanout",
    "retry",
    "abandon",
    "admission control",
    "workload characteriz",
    "cost per query",
    "lakehouse",
    "iceberg",
    "shared resource",
    "catalog",
)

AGENT_TERMS = ("llm", "tool", "planning", "memory", "benchmark", "accuracy")


def extract(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("pypdf is required: pip install pypdf")
    reader = PdfReader(path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf", help="path to the survey PDF")
    ap.add_argument("--context", metavar="TERM", help="print surrounding text for a term")
    args = ap.parse_args()

    text = extract(args.pdf)
    words = len(text.split())
    print(f"extracted {len(text):,} chars, ~{words:,} words\n")

    if args.context:
        for m in re.finditer(re.escape(args.context), text, re.I):
            window = re.sub(r"\s+", " ", text[max(0, m.start() - 250) : m.start() + 250])
            print(f"  ...{window}...\n")
        return

    print(f"{'platform-side term':<26}{'count':>6}")
    print("-" * 32)
    for term in PLATFORM_TERMS:
        print(f"{term:<26}{len(re.findall(re.escape(term), text, re.I)):>6}")
    print(f"\n{'agent-side term':<26}{'count':>6}")
    print("-" * 32)
    for term in AGENT_TERMS:
        print(f"{term:<26}{len(re.findall(re.escape(term), text, re.I)):>6}")
    print(
        "\nRe-run with --context TERM to inspect any non-zero count in situ before\n"
        "citing it; a substring match is not the same as a discussion of the topic."
    )


if __name__ == "__main__":
    main()
