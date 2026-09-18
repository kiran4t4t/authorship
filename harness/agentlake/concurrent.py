"""Concurrent execution: does contention appear when agents really run at once?

Section 3 finds that rows scanned per answered question is flat in fleet size.
That result was produced by a driver that interleaves agents deterministically
but executes their queries one at a time, so it measures the *work* a fleet
creates, not the *contention* it causes. Those are different questions and they
can have different answers.

This module runs the fleet through a thread pool against a shared DuckDB
database with a bounded resource budget. DuckDB releases the GIL during query
execution and supports one cursor per thread, so queries genuinely execute in
parallel and genuinely compete for the buffer pool, the memory limit and the
worker threads.

It remains an in-process engine: there is still no network shuffle, no
distributed catalog and no cross-node scheduling. What this adds is a real
shared resource pool under real simultaneous load, which is the single largest
gap in the serial result.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import duckdb

from . import dataset, sqlgen
from .agent import SyntheticAgent
from .cache import ExactTextCache, PlanCache
from .engine import (
    EngineAdapter,
    QueryStats,
    _fingerprint_plan,
    _tables_in_plan,
    normalise_text,
)
from .metrics import RunReport, summarise
from .phases import Phase, PhaseModel

_HASH_LEN = 16


@dataclasses.dataclass(frozen=True)
class ConcurrencyReport:
    """Latency and throughput of a concurrent run.

    Attributes:
        report: The usual platform-cost aggregation.
        fleet_size: Agents running simultaneously.
        makespan_ms: Wall-clock time for the whole run.
        mean_latency_ms: Mean per-query wall time.
        p95_latency_ms: 95th percentile per-query wall time.
        throughput_qps: Queries completed per second.
    """

    report: RunReport
    fleet_size: int
    makespan_ms: float
    mean_latency_ms: float
    p95_latency_ms: float
    throughput_qps: float


class ConcurrentDuckDBAdapter(EngineAdapter):
    """Thread-safe adapter over a shared DuckDB database.

    Each calling thread gets its own cursor, which DuckDB supports and which is
    what makes genuine parallel execution possible. Profiling is written to a
    per-call temporary file so that concurrent profiles do not overwrite one
    another.

    Args:
        con: Shared connection. All cursors derive from it.
        profile_dir: Directory for per-query profile files.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection, profile_dir: str) -> None:
        self._con = con
        self._dir = profile_dir
        self._local = threading.local()
        self._lock = threading.Lock()
        self._counter = 0
        self.catalog_requests: list[str] = []

    def _cursor(self) -> duckdb.DuckDBPyConnection:
        cur = getattr(self._local, "cur", None)
        if cur is None:
            cur = self._con.cursor()
            self._local.cur = cur
        return cur

    def _next_path(self) -> str:
        with self._lock:
            self._counter += 1
            n = self._counter
        return os.path.join(self._dir, f"p{n}.json")

    def run(
        self, sql: str, *, phase: Phase, agent_id: int, turn_id: int, delivered: bool
    ) -> QueryStats:
        """Execute `sql` on this thread's cursor, capturing an isolated profile."""
        cur = self._cursor()
        path = self._next_path()
        error: str | None = None
        rows: list = []
        prof: dict = {}
        started = time.perf_counter()
        try:
            cur.execute("PRAGMA enable_profiling='json'")
            cur.execute(f"PRAGMA profiling_output='{path}'")
            rows = cur.execute(sql).fetchall()
        except duckdb.Error as exc:
            error = f"{type(exc).__name__}: {exc}"
        wall_ms = (time.perf_counter() - started) * 1000.0
        try:
            cur.execute("PRAGMA disable_profiling")
            with open(path) as fh:
                prof = json.load(fh)
        except (duckdb.Error, OSError, json.JSONDecodeError):
            prof = {}
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        root = prof if "children" in prof else prof.get("root", {})
        import hashlib

        return QueryStats(
            sql=sql,
            phase=phase,
            agent_id=agent_id,
            turn_id=turn_id,
            rows_scanned=int(prof.get("cumulative_rows_scanned") or 0),
            rows_returned=len(rows),
            bytes_read=int(prof.get("total_bytes_read") or 0),
            wall_ms=wall_ms,
            cpu_ms=float(prof.get("cpu_time") or 0.0) * 1000.0,
            text_key=hashlib.sha256(normalise_text(sql).encode()).hexdigest()[:_HASH_LEN],
            plan_key=_fingerprint_plan(root) if root else "unplanned",
            tables=_tables_in_plan(root) if root else frozenset(),
            delivered=delivered and error is None,
            error=error,
        )

    def list_tables(self) -> list[str]:
        with self._lock:
            self.catalog_requests.append("list_tables")
        return [r[0] for r in self._cursor().execute("SHOW TABLES").fetchall()]

    def describe(self, table: str) -> list[tuple[str, str]]:
        with self._lock:
            self.catalog_requests.append(f"describe:{table}")
        rows = self._cursor().execute(f"DESCRIBE {table}").fetchall()
        return [(r[0], r[1]) for r in rows]


def run_fleet_concurrent(
    engine: ConcurrentDuckDBAdapter,
    *,
    fleet_size: int,
    turns_per_agent: int,
    model: PhaseModel,
    seed: int = 0,
    cache_capacity: int = 512,
    question_skew: float = 1.1,
    space: tuple[sqlgen.Question, ...] | None = None,
) -> ConcurrencyReport:
    """Run `fleet_size` agents simultaneously through a thread pool.

    Each agent runs on its own thread and issues its queries as fast as it can,
    so the engine sees `fleet_size` simultaneous requests competing for a fixed
    resource budget. Cache lookups happen after the run, over the collected
    stream, so cache simulation does not serialise execution.

    Raises:
        ValueError: If `fleet_size` or `turns_per_agent` is not positive.
    """
    if fleet_size <= 0:
        raise ValueError(f"fleet_size must be positive, got {fleet_size}")
    if turns_per_agent <= 0:
        raise ValueError(f"turns_per_agent must be positive, got {turns_per_agent}")

    space = space or sqlgen.question_space()
    agents = [SyntheticAgent(i, engine, model, seed=seed) for i in range(fleet_size)]

    def drive(agent: SyntheticAgent) -> tuple[list[QueryStats], int]:
        rng = random.Random(seed * 7919 + agent.agent_id)
        collected: list[QueryStats] = []
        answered = 0
        for turn_id in range(turns_per_agent):
            question = sqlgen.zipf_choice(space, rng, skew=question_skew)
            result = agent.run_turn(question, turn_id)
            collected.extend(result.stats)
            if result.delivered:
                answered += 1
        return collected, answered

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=fleet_size) as pool:
        outcomes = list(pool.map(drive, agents))
    makespan_ms = (time.perf_counter() - started) * 1000.0

    all_stats: list[QueryStats] = []
    answered_total = 0
    for stats, answered in outcomes:
        all_stats.extend(stats)
        answered_total += answered

    text_cache, plan_cache = ExactTextCache(cache_capacity), PlanCache(cache_capacity)
    for stat in all_stats:
        text_cache.lookup(stat)
        plan_cache.lookup(stat)

    report = summarise(
        all_stats,
        fleet_size=fleet_size,
        questions_answered=answered_total,
        catalog_requests=list(engine.catalog_requests),
        text_cache=text_cache.stats,
        plan_cache=plan_cache.stats,
    )

    latencies = sorted(s.wall_ms for s in all_stats)
    mean = sum(latencies) / len(latencies) if latencies else 0.0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
    return ConcurrencyReport(
        report=report,
        fleet_size=fleet_size,
        makespan_ms=makespan_ms,
        mean_latency_ms=mean,
        p95_latency_ms=p95,
        throughput_qps=len(all_stats) / (makespan_ms / 1000.0) if makespan_ms else 0.0,
    )


def concurrency_sweep(
    fleet_sizes: list[int],
    *,
    total_turns: int = 192,
    model: PhaseModel | None = None,
    n_sales: int = 400_000,
    seed: int = 1,
    threads: int = 4,
    memory_limit: str = "512MB",
) -> list[ConcurrencyReport]:
    """Sweep fleet size under real concurrent execution and a bounded budget.

    The resource budget is deliberately fixed and modest. A fleet competing for
    an unbounded pool would show no contention regardless of size, which would
    make the experiment vacuous.

    Args:
        fleet_sizes: Simultaneous-agent counts to test.
        total_turns: Total turns across the fleet, held constant across sizes.
        model: Phase parameters; defaults to `PhaseModel()`.
        n_sales: Fact table rows.
        seed: Run seed.
        threads: DuckDB worker threads -- the shared execution resource.
        memory_limit: DuckDB memory budget, shared across the fleet.

    Raises:
        ValueError: If `fleet_sizes` is empty.
    """
    if not fleet_sizes:
        raise ValueError("fleet_sizes must not be empty")
    model = model or PhaseModel()

    con = duckdb.connect()
    dataset.build(con, n_sales=n_sales, seed=seed)
    con.execute(f"SET threads={threads}")
    con.execute(f"SET memory_limit='{memory_limit}'")

    out: list[ConcurrencyReport] = []
    with tempfile.TemporaryDirectory(prefix="agentlake-conc-") as tmp:
        for size in fleet_sizes:
            engine = ConcurrentDuckDBAdapter(con, tmp)
            turns_per_agent = max(1, -(-total_turns // size))
            out.append(
                run_fleet_concurrent(
                    engine,
                    fleet_size=size,
                    turns_per_agent=turns_per_agent,
                    model=model,
                    seed=seed,
                )
            )
    return out
