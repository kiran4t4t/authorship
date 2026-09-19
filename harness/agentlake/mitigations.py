"""Mitigation ablation: which interventions recover how much, and at what cost.

Section 3 establishes that agent workloads cost ~2.6x more per answered question
than the human control while appearing cheaper per issued query. This module
asks what can be done about it, by running the same workload under configurations
that each disable or constrain one source of cost.

Every mitigation here is expressed as a change to the *workload*, not to the
engine, because that is what the harness can vary honestly. Engine-side changes
(a different cache keying strategy) are evaluated by the cache simulation in
`cache.py` instead; see `CACHE_COUNTERFACTUAL` below for why those two are
reported differently.
"""

from __future__ import annotations

import dataclasses

import duckdb

from . import dataset, sqlgen
from .engine import DuckDBAdapter
from .fleet import run_fleet
from .metrics import RunReport
from .phases import PhaseModel

CACHE_COUNTERFACTUAL = """\
The cache mitigations are counterfactual estimates, not executed savings. The
harness simulates both caches as observers over the real query stream; it does
not skip execution on a hit. `rows_scanned_saved` is therefore what a cache with
that keying strategy *would* have avoided, given the queries that actually ran.
The workload mitigations below are different in kind: those queries genuinely do
not run."""


@dataclasses.dataclass(frozen=True)
class Mitigation:
    """One intervention to evaluate.

    Attributes:
        key: Short identifier used in the results table.
        name: Human-readable name.
        rationale: Why this would be expected to help, in one line.
        model: Phase model expressing the intervention.
        curated: Whether agents are restricted to a curated question space,
            modelling a semantic layer that exposes governed measures rather
            than raw tables. Restricting capability is part of the cost.
        capability_cost: Free-text note on what the intervention gives up.
    """

    key: str
    name: str
    rationale: str
    model: PhaseModel
    curated: bool = False
    capability_cost: str = "none"


_BASE = PhaseModel()

MITIGATIONS: tuple[Mitigation, ...] = (
    Mitigation(
        "baseline",
        "No mitigation",
        "Reference configuration from Section 3.",
        _BASE,
    ),
    Mitigation(
        "pinned_schema",
        "Pinned schema context",
        "Ship schema and column semantics with the agent so it stops "
        "rediscovering them on every cold start.",
        dataclasses.replace(
            _BASE,
            n_discovery=0,
            provenance="UNCALIBRATED: baseline with discovery eliminated",
        ),
        capability_cost="Context must be refreshed when the schema changes; a "
        "stale pin is worse than no pin.",
    ),
    Mitigation(
        "profile_cache",
        "Cached column profiles",
        "Serve cardinality and value distributions from a maintained profile "
        "instead of letting each agent sample the table itself.",
        dataclasses.replace(
            _BASE,
            n_sampling=0,
            provenance="UNCALIBRATED: baseline with sampling eliminated",
        ),
        capability_cost="Profiles go stale; agents lose the ability to inspect "
        "actual values, which matters for free-text and high-cardinality columns.",
    ),
    Mitigation(
        "correction_budget",
        "Tight correction budget",
        "Cap retries at one, so a failing agent escalates rather than "
        "re-querying.",
        dataclasses.replace(
            _BASE,
            max_corrections=1,
            provenance="UNCALIBRATED: baseline with correction budget of 1",
        ),
        capability_cost="Questions that would have been answered on the second "
        "retry now fail.",
    ),
    Mitigation(
        "semantic_layer",
        "Semantic layer boundary",
        "Expose governed measures and dimensions rather than raw tables: no "
        "schema discovery, no sampling, and a curated question space.",
        dataclasses.replace(
            _BASE,
            n_discovery=0,
            n_sampling=0,
            provenance="UNCALIBRATED: baseline with discovery and sampling "
            "eliminated, over a curated question space",
        ),
        curated=True,
        capability_cost="Only modelled questions are answerable at all. This is "
        "the intervention with a real capability price, and it must be reported "
        "alongside the saving.",
    ),
    Mitigation(
        "combined",
        "Semantic layer + correction budget",
        "The two interventions that attack different phases, applied together.",
        dataclasses.replace(
            _BASE,
            n_discovery=0,
            n_sampling=0,
            max_corrections=1,
            provenance="UNCALIBRATED: semantic layer plus correction budget of 1",
        ),
        curated=True,
        capability_cost="Both of the above.",
    ),
)


def curated_space() -> tuple[sqlgen.Question, ...]:
    """The question space a semantic layer would expose.

    We model it as the unparameterised base corpus: the governed measures an
    organisation has actually modelled, without the long tail of ad hoc filter
    combinations. The ratio of this to the full space is the capability cost,
    and `evaluate` reports it.
    """
    return sqlgen.CORPUS


def evaluate(
    *,
    fleet_size: int = 8,
    total_turns: int = 192,
    n_sales: int = 400_000,
    seed: int = 1,
    cache_capacity: int = 512,
    question_skew: float = 1.1,
) -> list[tuple[Mitigation, RunReport, float]]:
    """Run every mitigation against the same dataset, fleet and seed.

    Returns:
        One (mitigation, report, answerable_fraction) triple per mitigation.
        `answerable_fraction` is the share of the full question space the
        mitigation leaves answerable -- 1.0 unless it curates the space.

    Raises:
        ValueError: If `fleet_size` or `total_turns` is not positive.
    """
    if fleet_size <= 0 or total_turns <= 0:
        raise ValueError("fleet_size and total_turns must be positive")

    con = duckdb.connect()
    dataset.build(con, n_sales=n_sales, seed=seed)
    full = sqlgen.question_space()
    turns_per_agent = max(1, -(-total_turns // fleet_size))

    out: list[tuple[Mitigation, RunReport, float]] = []
    for mitigation in MITIGATIONS:
        space = curated_space() if mitigation.curated else full
        engine = DuckDBAdapter(con)
        report = run_fleet(
            engine,
            fleet_size=fleet_size,
            turns_per_agent=turns_per_agent,
            model=mitigation.model,
            seed=seed,
            cache_capacity=cache_capacity,
            question_skew=question_skew,
            space=space,
        )
        out.append((mitigation, report, len(space) / len(full)))
    return out
