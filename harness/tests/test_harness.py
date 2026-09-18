"""Test suite for agentlake.

The load-bearing test is `TestMutationsPreserveMeaning`. The harness's central
claim -- that correction-phase queries are semantically equivalent to what they
replace, and therefore *should* be cache hits -- is only meaningful if the
mutations really do preserve meaning. If they do not, the cache findings are an
artifact of the mutator emitting different queries, not a property of agents.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb

from agentlake import dataset, sqlgen
from agentlake.cache import ExactTextCache, PlanCache
from agentlake.engine import DuckDBAdapter, normalise_text
from agentlake.fleet import run_fleet
from agentlake.metrics import gini
from agentlake.phases import HUMAN_BASELINE, Phase, PhaseModel
from agentlake.sweep import scaling_exponent, sweep

SMALL = 20_000


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    dataset.build(con, n_sales=SMALL)
    return con


class TestMutationsPreserveMeaning(unittest.TestCase):
    """Every mutation must return exactly the same rows as the canonical query."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.con = _con()

    def test_every_mutation_on_every_question(self) -> None:
        for question in sqlgen.CORPUS:
            # Compare as sorted multisets: several corpus queries ORDER BY a
            # measure with ties, and SQL guarantees no particular order among
            # tied rows. Comparing raw row order would fail on tie ordering
            # rather than on any real change of meaning.
            truth = sorted(self.con.execute(question.sql).fetchall())
            for mutation in sqlgen.MUTATIONS:
                variant = mutation(question.sql)
                with self.subTest(question=question.qid, mutation=mutation.__name__):
                    got = sorted(self.con.execute(variant).fetchall())
                    self.assertEqual(
                        got,
                        truth,
                        f"{mutation.__name__} changed the result of {question.qid}",
                    )

    def test_question_space_is_executable(self) -> None:
        for question in sqlgen.question_space():
            with self.subTest(question=question.qid):
                self.con.execute(question.sql).fetchall()

    def test_break_query_actually_breaks(self) -> None:
        import random

        rng = random.Random(0)
        broken_count = 0
        for question in sqlgen.CORPUS:
            for _ in range(6):
                sql = sqlgen.break_query(question.sql, rng)
                try:
                    self.con.execute(sql).fetchall()
                except duckdb.Error:
                    broken_count += 1
        self.assertGreater(broken_count, 0, "break_query never produced a failure")


class TestPlanFingerprint(unittest.TestCase):
    """Mutations should collide under plan keys and mostly differ under text keys."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.con = _con()
        cls.engine = DuckDBAdapter(cls.con)

    def _stat(self, sql: str):
        return self.engine.run(
            sql, phase=Phase.CANDIDATE, agent_id=0, turn_id=0, delivered=True
        )

    def test_plan_key_is_stable_across_mutations(self) -> None:
        for question in sqlgen.CORPUS:
            base = self._stat(question.sql)
            for mutation in sqlgen.MUTATIONS:
                variant = self._stat(mutation(question.sql))
                with self.subTest(question=question.qid, mutation=mutation.__name__):
                    self.assertEqual(
                        variant.plan_key,
                        base.plan_key,
                        f"{mutation.__name__} changed the plan key of {question.qid}",
                    )

    def test_text_key_differs_for_most_mutations(self) -> None:
        differing = 0
        total = 0
        for question in sqlgen.CORPUS:
            base = self._stat(question.sql)
            for mutation in sqlgen.MUTATIONS:
                total += 1
                if self._stat(mutation(question.sql)).text_key != base.text_key:
                    differing += 1
        self.assertGreater(
            differing / total, 0.5, "mutator barely changes text; cache test is vacuous"
        )

    def test_different_questions_have_different_plans(self) -> None:
        keys = {self._stat(q.sql).plan_key for q in sqlgen.CORPUS}
        self.assertEqual(
            len(keys), len(sqlgen.CORPUS), "distinct questions collided on plan key"
        )

    def test_filter_literals_do_not_share_a_plan_key(self) -> None:
        """The direction that matters: over-collision would serve wrong results.

        If `country = 'US'` and `country = 'UK'` shared a fingerprint, the plan
        cache would return one country's numbers for the other, and its measured
        hit rate would be fiction.
        """
        space = sqlgen.question_space()
        keys = {}
        for question in space:
            key = self._stat(question.sql).plan_key
            self.assertNotIn(
                key,
                keys,
                f"{question.qid} collided with {keys.get(key)} on plan key",
            )
            keys[key] = question.qid

    def test_normalise_text_collapses_whitespace_and_case(self) -> None:
        self.assertEqual(normalise_text("SELECT  1 \n FROM t"), "select 1 from t")

    def test_failed_query_is_recorded_not_raised(self) -> None:
        stat = self._stat("SELECT no_such_column FROM fact_sales")
        self.assertIsNotNone(stat.error)
        self.assertFalse(stat.delivered)


class TestCaches(unittest.TestCase):
    """Plan-keyed caching must dominate text-keyed caching on agent traffic."""

    def test_plan_cache_beats_text_cache_on_mutated_stream(self) -> None:
        con = _con()
        engine = DuckDBAdapter(con)
        text, plan = ExactTextCache(256), PlanCache(256)
        import random

        rng = random.Random(3)
        for question in sqlgen.CORPUS:
            for sql in [question.sql] + [
                sqlgen.mutate(question.sql, rng) for _ in range(5)
            ]:
                stat = engine.run(
                    sql, phase=Phase.CORRECTION, agent_id=0, turn_id=0, delivered=True
                )
                text.lookup(stat)
                plan.lookup(stat)
        self.assertGreater(plan.stats.hit_rate, text.stats.hit_rate)

    def test_errors_are_not_cached(self) -> None:
        con = _con()
        engine = DuckDBAdapter(con)
        cache = PlanCache(16)
        bad = engine.run(
            "SELECT bad FROM fact_sales",
            phase=Phase.CANDIDATE,
            agent_id=0,
            turn_id=0,
            delivered=False,
        )
        self.assertFalse(cache.lookup(bad))
        self.assertEqual(cache.stats.hits + cache.stats.misses, 0)

    def test_rejects_nonpositive_capacity(self) -> None:
        with self.assertRaises(ValueError):
            PlanCache(0)


class TestMetrics(unittest.TestCase):
    def test_gini_bounds(self) -> None:
        self.assertEqual(gini([]), 0.0)
        self.assertEqual(gini([0, 0, 0]), 0.0)
        self.assertAlmostEqual(gini([5, 5, 5, 5]), 0.0, places=6)
        self.assertGreater(gini([100, 1, 1, 1]), 0.5)

    def test_gini_is_monotonic_in_concentration(self) -> None:
        self.assertLess(gini([10, 8, 6, 4]), gini([50, 2, 1, 1]))


class TestPhaseModel(unittest.TestCase):
    def test_rejects_out_of_range_probabilities(self) -> None:
        for kwargs in ({"p_correction": 1.5}, {"p_abandon": -0.1}, {"p_explore": 2.0}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                PhaseModel(**kwargs)

    def test_rejects_negative_counts(self) -> None:
        with self.assertRaises(ValueError):
            PhaseModel(n_sampling=-1)

    def test_defaults_declare_themselves_uncalibrated(self) -> None:
        self.assertFalse(PhaseModel().is_calibrated)
        self.assertFalse(HUMAN_BASELINE.is_calibrated)

    def test_abandonment_is_not_a_phase(self) -> None:
        self.assertNotIn("abandoned", [p.value for p in Phase])


class TestFleet(unittest.TestCase):
    def test_run_is_deterministic_under_a_seed(self) -> None:
        con = _con()
        reports = []
        for _ in range(2):
            engine = DuckDBAdapter(con)
            reports.append(
                run_fleet(
                    engine,
                    fleet_size=3,
                    turns_per_agent=3,
                    model=PhaseModel(),
                    seed=42,
                )
            )
        a, b = reports
        self.assertEqual(a.queries_issued, b.queries_issued)
        self.assertEqual(a.rows_scanned, b.rows_scanned)
        self.assertEqual(a.questions_answered, b.questions_answered)
        self.assertEqual(a.by_phase, b.by_phase)

    def test_agent_fanout_exceeds_human_baseline(self) -> None:
        con = _con()
        agentic = run_fleet(
            DuckDBAdapter(con), fleet_size=3, turns_per_agent=4,
            model=PhaseModel(), seed=7,
        )
        human = run_fleet(
            DuckDBAdapter(con), fleet_size=3, turns_per_agent=4,
            model=HUMAN_BASELINE, seed=7,
        )
        self.assertGreater(agentic.queries_per_question, human.queries_per_question)
        self.assertAlmostEqual(human.queries_per_question, 1.0, places=6)
        self.assertEqual(human.waste_fraction, 0.0)

    def test_rejects_invalid_fleet_size(self) -> None:
        with self.assertRaises(ValueError):
            run_fleet(DuckDBAdapter(_con()), fleet_size=0, turns_per_agent=1,
                      model=PhaseModel())


class TestSweep(unittest.TestCase):
    def test_total_work_is_held_constant_across_fleet_sizes(self) -> None:
        points = sweep([1, 2, 4], total_turns=12, n_sales=SMALL, seed=5)
        answered = [p.report.questions_answered for p in points]
        spread = (max(answered) - min(answered)) / max(answered)
        self.assertLess(
            spread, 0.35, f"fleet sizes did comparable work? answered={answered}"
        )

    def test_scaling_exponent_degenerate_cases(self) -> None:
        self.assertEqual(scaling_exponent([]), 0.0)

    def test_rejects_empty_sizes(self) -> None:
        with self.assertRaises(ValueError):
            sweep([], n_sales=SMALL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
