"""Drive a fleet of agents and collect a RunReport.

Concurrency here is logical, not wall-clock: agents interleave their turns over
a shared engine and shared caches, which is what the contention and cache
analyses need. We deliberately do not use threads, because DuckDB in-process
would then measure Python's GIL rather than the platform, and because a
deterministic interleaving is reproducible where a threaded one is not.

An adapter targeting a real distributed engine should override this with true
concurrent submission; the `RunReport` contract is unchanged.
"""

from __future__ import annotations

import random

from . import sqlgen
from .agent import SyntheticAgent
from .cache import ExactTextCache, PlanCache
from .engine import DuckDBAdapter, EngineAdapter
from .metrics import RunReport, summarise
from .phases import PhaseModel


def run_fleet(
    engine: EngineAdapter,
    *,
    fleet_size: int,
    turns_per_agent: int,
    model: PhaseModel,
    seed: int = 0,
    cache_capacity: int = 512,
    question_skew: float = 1.1,
    space: tuple[sqlgen.Question, ...] | None = None,
) -> RunReport:
    """Run `fleet_size` agents for `turns_per_agent` turns each.

    Args:
        engine: Lakehouse under test.
        fleet_size: Number of concurrent agents. The sweep axis.
        turns_per_agent: Questions each agent works through.
        model: Phase parameters. Use `HUMAN_BASELINE` for the control condition.
        seed: Run seed; agents derive per-agent seeds from it.
        cache_capacity: Entries per simulated cache. Shared across the fleet,
            which is what makes cache pollution visible.
        question_skew: Zipf exponent for question selection. Higher values
            concentrate traffic on a few popular questions. Holding this fixed
            across fleet sizes is what keeps a sweep interpretable.
        space: Question space to draw from. Defaults to the parameterised
            expansion of CORPUS, which is large enough that cache hit rate does
            not rise trivially with fleet size.

    Returns:
        Aggregated `RunReport`.

    Raises:
        ValueError: If `fleet_size` or `turns_per_agent` is not positive.
    """
    if fleet_size <= 0:
        raise ValueError(f"fleet_size must be positive, got {fleet_size}")
    if turns_per_agent <= 0:
        raise ValueError(f"turns_per_agent must be positive, got {turns_per_agent}")

    rng = random.Random(seed)
    space = space or sqlgen.question_space()
    agents = [SyntheticAgent(i, engine, model, seed=seed) for i in range(fleet_size)]
    text_cache = ExactTextCache(cache_capacity)
    plan_cache = PlanCache(cache_capacity)

    all_stats = []
    answered = 0

    # Round-robin interleave so that agents share the caches concurrently rather
    # than each running to completion in isolation.
    for turn_id in range(turns_per_agent):
        for agent in agents:
            question = sqlgen.zipf_choice(space, rng, skew=question_skew)
            result = agent.run_turn(question, turn_id)
            for stat in result.stats:
                text_cache.lookup(stat)
                plan_cache.lookup(stat)
            all_stats.extend(result.stats)
            if result.delivered:
                answered += 1

    catalog = getattr(engine, "catalog_requests", [])
    return summarise(
        all_stats,
        fleet_size=fleet_size,
        questions_answered=answered,
        catalog_requests=list(catalog),
        text_cache=text_cache.stats,
        plan_cache=plan_cache.stats,
    )
