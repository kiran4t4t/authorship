"""Aggregation over a run's query stream.

Every number the paper reports should come from here rather than from ad hoc
analysis, so that the harness and the paper cannot drift apart.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

from .engine import QueryStats
from .phases import Phase


def gini(counts: list[int]) -> float:
    """Gini coefficient of a count distribution.

    Used for catalog request skew: 0.0 means requests are spread evenly across
    tables, 1.0 means they all land on one. Skew matters because a metadata hot
    spot is a different operational problem from metadata volume.

    Returns 0.0 for an empty or all-zero distribution.
    """
    values = sorted(c for c in counts if c >= 0)
    n = len(values)
    total = sum(values)
    if n == 0 or total == 0:
        return 0.0
    weighted = sum((i + 1) * v for i, v in enumerate(values))
    return (2 * weighted) / (n * total) - (n + 1) / n


@dataclasses.dataclass(frozen=True)
class RunReport:
    """Aggregate result of one harness run.

    Attributes:
        fleet_size: Number of concurrent agents.
        questions_answered: Turns that delivered a result to a user. The
            denominator that matters -- the unit of value is an answered
            question, not an issued query.
        queries_issued: Total queries hitting the engine, all phases.
        rows_scanned: Total rows read from base tables.
        wasted_rows_scanned: Rows scanned on work that was never delivered
            (abandoned turns and superseded corrections).
        abandoned_rows_scanned: Rows scanned on turns explicitly abandoned. A
            subset of `wasted_rows_scanned`, reported separately because
            abandonment is invisible to every accuracy metric.
        wall_ms: Summed query execution time.
        errors: Queries that failed.
        by_phase: Query count per phase.
        scanned_by_phase: Rows scanned per phase.
        catalog_requests: Total catalog operations.
        catalog_skew: Gini coefficient over per-table catalog requests.
        text_cache: Stats for the exact-match cache.
        plan_cache: Stats for the plan-keyed cache.
    """

    fleet_size: int
    questions_answered: int
    queries_issued: int
    rows_scanned: int
    wasted_rows_scanned: int
    abandoned_rows_scanned: int
    wall_ms: float
    errors: int
    by_phase: dict[str, int]
    scanned_by_phase: dict[str, int]
    catalog_requests: int
    catalog_skew: float
    text_cache: "object"
    plan_cache: "object"

    @property
    def scanned_per_question(self) -> float:
        """Rows scanned per *answered question*. The headline cost metric."""
        return self.rows_scanned / max(self.questions_answered, 1)

    @property
    def scanned_per_query(self) -> float:
        """Rows scanned per *issued query*. What conventional monitoring shows."""
        return self.rows_scanned / max(self.queries_issued, 1)

    @property
    def queries_per_question(self) -> float:
        """Query fan-out: how many queries one user question becomes."""
        return self.queries_issued / max(self.questions_answered, 1)

    @property
    def waste_fraction(self) -> float:
        """Share of scanned rows that produced nothing a user saw."""
        return self.wasted_rows_scanned / max(self.rows_scanned, 1)

    @property
    def abandoned_fraction(self) -> float:
        """Share of scanned rows spent on turns that were discarded outright."""
        return self.abandoned_rows_scanned / max(self.rows_scanned, 1)


def summarise(
    stats: list[QueryStats],
    *,
    fleet_size: int,
    questions_answered: int,
    catalog_requests: list[str],
    text_cache: object,
    plan_cache: object,
) -> RunReport:
    """Reduce a run's raw query stream to a `RunReport`."""
    by_phase: Counter[str] = Counter()
    scanned_by_phase: Counter[str] = Counter()
    wasted = 0
    abandoned = 0
    for s in stats:
        by_phase[s.phase.value] += 1
        scanned_by_phase[s.phase.value] += s.rows_scanned
        if not s.delivered:
            wasted += s.rows_scanned
        if s.abandoned:
            abandoned += s.rows_scanned

    per_table: Counter[str] = Counter()
    for req in catalog_requests:
        if req.startswith("describe:"):
            per_table[req.split(":", 1)[1]] += 1

    return RunReport(
        fleet_size=fleet_size,
        questions_answered=questions_answered,
        queries_issued=len(stats),
        rows_scanned=sum(s.rows_scanned for s in stats),
        wasted_rows_scanned=wasted,
        abandoned_rows_scanned=abandoned,
        wall_ms=sum(s.wall_ms for s in stats),
        errors=sum(1 for s in stats if s.error is not None),
        by_phase=dict(by_phase),
        scanned_by_phase=dict(scanned_by_phase),
        catalog_requests=len(catalog_requests),
        catalog_skew=gini(list(per_table.values())),
        text_cache=text_cache,
        plan_cache=plan_cache,
    )
