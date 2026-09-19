"""Fleet-size sweep: does aggregate cost scale linearly in the number of agents?

This is the experiment that cannot be run from any single-agent study, and it is
the reason the harness exists. If cost per answered question is flat in fleet
size, agent workloads are merely expensive. If it rises, they are expensive in a
way that gets worse with adoption -- which is a different engineering problem and
a different procurement conversation.
"""

from __future__ import annotations

import dataclasses

import duckdb

from . import dataset
from .engine import DuckDBAdapter
from .fleet import run_fleet
from .metrics import RunReport
from .phases import PhaseModel


@dataclasses.dataclass(frozen=True)
class SweepPoint:
    """One fleet size, with its report and the per-question cost it implied."""

    fleet_size: int
    report: RunReport

    @property
    def scanned_per_question(self) -> float:
        return self.report.scanned_per_question


def sweep(
    fleet_sizes: list[int],
    *,
    total_turns: int = 96,
    model: PhaseModel | None = None,
    n_sales: int = 200_000,
    seed: int = 1,
    cache_capacity: int = 512,
    question_skew: float = 1.1,
) -> list[SweepPoint]:
    """Run the same workload at each fleet size and report cost per question.

    Each point gets a fresh engine and fresh caches so that sizes do not
    contaminate each other, but the same dataset and seed, so the only variable
    is fleet size.

    Args:
        fleet_sizes: Fleet sizes to test, e.g. [1, 2, 4, 8, 16].
        total_turns: Total turns across the whole fleet, held CONSTANT across
            fleet sizes. This is the crux of the experiment design: if turns
            *per agent* were held constant instead, a larger fleet would do
            proportionally more total work, its shared cache would warm further,
            and per-question cost would fall for reasons that have nothing to do
            with concurrency. Fixing total work makes fleet size the only
            variable. Sizes that do not divide it evenly are rounded up.
        model: Phase parameters; defaults to `PhaseModel()`.
        n_sales: Fact table size.
        seed: Run seed.
        cache_capacity: Shared cache capacity. Held constant across fleet sizes
            deliberately -- a fixed cache serving a growing fleet is the
            realistic condition, and cache pollution is part of what we measure.
        question_skew: Zipf exponent for question selection, held constant.

    Returns:
        One `SweepPoint` per fleet size, in the order given.

    Raises:
        ValueError: If `fleet_sizes` is empty.
    """
    if not fleet_sizes:
        raise ValueError("fleet_sizes must not be empty")
    model = model or PhaseModel()

    con = duckdb.connect()
    dataset.build(con, n_sales=n_sales, seed=seed)

    points: list[SweepPoint] = []
    for size in fleet_sizes:
        engine = DuckDBAdapter(con)
        turns_per_agent = max(1, -(-total_turns // size))  # ceil division
        report = run_fleet(
            engine,
            fleet_size=size,
            turns_per_agent=turns_per_agent,
            model=model,
            seed=seed,
            cache_capacity=cache_capacity,
            question_skew=question_skew,
        )
        points.append(SweepPoint(fleet_size=size, report=report))
    return points


def scaling_exponent(points: list[SweepPoint]) -> float:
    """Fit cost-per-question ~ fleet_size**k and return k.

    k near 0 means per-question cost is flat: the workload scales linearly in
    aggregate and agents are simply additive. k > 0 means per-question cost
    rises with fleet size -- superlinear aggregate cost.

    Returns 0.0 when there are fewer than two usable points.
    """
    import math

    usable = [
        p for p in points if p.fleet_size > 0 and p.scanned_per_question > 0
    ]
    if len(usable) < 2:
        return 0.0
    xs = [math.log(p.fleet_size) for p in usable]
    ys = [math.log(p.scanned_per_question) for p in usable]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
