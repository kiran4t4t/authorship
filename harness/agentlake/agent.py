"""The synthetic agent: one turn, decomposed into phases.

A turn takes a question from the corpus and produces the burst of platform
traffic a real agent would produce answering it. What it does NOT do is decide
whether the answer is correct -- accuracy is out of scope, and simulating it
would add a dependency on a live model without changing any platform-side metric.

Determinism is the point. Given a seed, a turn is exactly reproducible, which is
what lets a reviewer re-run a reported number.
"""

from __future__ import annotations

import dataclasses
import random

from . import sqlgen
from .engine import EngineAdapter, QueryStats
from .phases import Phase, PhaseModel


@dataclasses.dataclass
class TurnResult:
    """Outcome of one agent turn.

    Attributes:
        question: The question attempted.
        stats: Every query the turn issued, in order.
        delivered: Whether the turn produced an answer the user saw. False when
            the turn was abandoned or exhausted its correction budget.
    """

    question: sqlgen.Question
    stats: list[QueryStats]
    delivered: bool


class SyntheticAgent:
    """An agent that generates phased traffic against a lakehouse.

    Args:
        agent_id: Identifier within the fleet.
        engine: The lakehouse under test.
        model: Phase parameters governing this agent's behaviour.
        seed: Per-agent RNG seed.
    """

    def __init__(
        self,
        agent_id: int,
        engine: EngineAdapter,
        model: PhaseModel,
        seed: int = 0,
    ) -> None:
        self.agent_id = agent_id
        self._engine = engine
        self._model = model
        # Separate RNG streams per decision type. A single shared stream made
        # the mitigation ablation invalid: disabling discovery stopped
        # consuming draws, which shifted every downstream correction and
        # abandonment decision, and a mitigation that removes no scan work at
        # all appeared to make the workload 6% more expensive. Independent
        # streams mean changing one phase's parameters leaves the others'
        # decisions bit-identical.
        base = seed * 1_000_003 + agent_id
        self._rng_discovery = random.Random(base + 11)
        self._rng_sampling = random.Random(base + 22)
        self._rng_correction = random.Random(base + 33)
        self._rng_abandon = random.Random(base + 44)
        self._rng_mutate = random.Random(base + 55)
        self._known_tables: list[str] = []

    def _discover(self, question: sqlgen.Question, turn_id: int) -> None:
        """Catalog introspection. Costs requests, not scans.

        A cold agent rediscovers the schema every turn. This is the behaviour
        that produces metadata hot spots, so it is modelled explicitly rather
        than amortised away.

        Table selection is biased toward the tables the question actually needs,
        with occasional exploration elsewhere. An earlier version drew uniformly
        at random, which made catalog skew unmeasurable by construction: uniform
        discovery cannot produce a hot spot, so any skew we reported would have
        been an artifact of the model rather than a property of the workload.
        Question-driven discovery lets skew emerge -- or not -- on its own.
        """
        relevant = sorted(question.tables)
        for _ in range(self._model.n_discovery):
            tables = self._engine.list_tables()
            if not tables:
                continue
            self._known_tables = tables
            explore = self._rng_discovery.random() < self._model.p_explore
            pool = tables if (explore or not relevant) else relevant
            self._engine.describe(self._rng_discovery.choice(pool))

    def _sample(self, question: sqlgen.Question, turn_id: int) -> list[QueryStats]:
        """LIMITed reads to learn column semantics. Small, unselective scans."""
        out: list[QueryStats] = []
        targets = sorted(question.tables) or self._known_tables
        for _ in range(self._model.n_sampling):
            if not targets:
                break
            table = self._rng_sampling.choice(targets)
            sql = f"SELECT * FROM {table} LIMIT {self._model.sample_limit}"
            out.append(
                self._engine.run(
                    sql,
                    phase=Phase.SAMPLING,
                    agent_id=self.agent_id,
                    turn_id=turn_id,
                    delivered=False,
                )
            )
        return out

    def run_turn(self, question: sqlgen.Question, turn_id: int) -> TurnResult:
        """Execute one full turn against `question`.

        The turn proceeds: discovery, sampling, then for each candidate a
        possible error-and-correction loop, then a possible abandonment. Only
        the final surviving candidate is marked delivered.
        """
        self._discover(question, turn_id)
        stats = self._sample(question, turn_id)
        delivered_any = False

        for _ in range(self._model.n_candidate):
            attempts: list[QueryStats] = []
            failed_first = self._rng_correction.random() < self._model.p_correction

            if failed_first:
                broken = sqlgen.break_query(question.sql, self._rng_correction)
                attempts.append(
                    self._engine.run(
                        broken,
                        phase=Phase.CANDIDATE,
                        agent_id=self.agent_id,
                        turn_id=turn_id,
                        delivered=False,
                    )
                )
                for _ in range(self._model.max_corrections):
                    # A correction is not guaranteed to work. Modelling every
                    # retry as succeeding first time made the correction-budget
                    # mitigation inert by construction: the second attempt was
                    # never reached, so capping it changed nothing.
                    retry_fails = (
                        self._rng_correction.random() < self._model.p_correction_fails
                    )
                    sql = (
                        sqlgen.break_query(question.sql, self._rng_correction)
                        if retry_fails
                        else sqlgen.mutate(question.sql, self._rng_mutate)
                    )
                    stat = self._engine.run(
                        sql,
                        phase=Phase.CORRECTION,
                        agent_id=self.agent_id,
                        turn_id=turn_id,
                        delivered=False,
                    )
                    attempts.append(stat)
                    if stat.error is None:
                        break
            else:
                attempts.append(
                    self._engine.run(
                        question.sql,
                        phase=Phase.CANDIDATE,
                        agent_id=self.agent_id,
                        turn_id=turn_id,
                        delivered=False,
                    )
                )

            succeeded = [s for s in attempts if s.error is None]
            abandoned = self._rng_abandon.random() < self._model.p_abandon
            if succeeded and not abandoned:
                final = succeeded[-1]
                idx = attempts.index(final)
                attempts[idx] = dataclasses.replace(final, delivered=True)
                delivered_any = True
            elif abandoned:
                attempts = [
                    dataclasses.replace(s, abandoned=True, delivered=False)
                    for s in attempts
                ]
            stats.extend(attempts)

        return TurnResult(question=question, stats=stats, delivered=delivered_any)
