"""Engine adapters: run a query and report what it cost the platform.

The harness measures the platform side, so an adapter's job is not to return
rows but to return `QueryStats`. `DuckDBAdapter` is the reference implementation
and runs anywhere; the `EngineAdapter` interface exists so that the same fleet
driver can be pointed at Trino, Spark or a vendor lakehouse without the
experiment code changing.

Plan fingerprinting is the load-bearing part. To test whether correction-phase
queries defeat exact-match result caching, we need a key that is stable across
semantically equivalent rewrites of the same query. We derive it from the
optimised operator tree rather than from query text.
"""

from __future__ import annotations

import abc
import dataclasses
import hashlib
import json
import os
import re
import tempfile
import time
from typing import Any

import duckdb

from .phases import Phase


@dataclasses.dataclass(frozen=True)
class QueryStats:
    """Platform-side cost of a single executed query.

    Attributes:
        sql: The exact SQL issued, as the agent wrote it.
        phase: Which phase of the agent turn produced it.
        agent_id: Which agent in the fleet issued it.
        turn_id: Which turn within that agent.
        rows_scanned: Rows read from base tables, summed over all scan operators.
            This is the scan-amplification numerator.
        rows_returned: Rows delivered to the caller.
        bytes_read: Bytes read by the engine, where the engine reports it.
        wall_ms: Wall-clock execution time in milliseconds.
        cpu_ms: CPU time in milliseconds, where the engine reports it.
        text_key: Hash of normalised query *text*. The key an exact-match result
            cache would use.
        plan_key: Hash of the optimised operator tree. The key a
            semantics-aware cache would use.
        tables: Base tables touched, for catalog skew analysis.
        delivered: Whether this query's result reached the user. False for
            abandoned work and for superseded corrections.
        abandoned: Whether this query belonged to a turn whose work was
            discarded. Orthogonal to `phase`: abandoned work retains the phase
            that produced it, so per-phase cost attribution survives.
        error: Error string if the query failed, else None.
    """

    sql: str
    phase: Phase
    agent_id: int
    turn_id: int
    rows_scanned: int
    rows_returned: int
    bytes_read: int
    wall_ms: float
    cpu_ms: float
    text_key: str
    plan_key: str
    tables: frozenset[str]
    delivered: bool
    abandoned: bool = False
    error: str | None = None

    @property
    def scan_amplification(self) -> float:
        """Rows scanned per row returned. Infinite-ish for empty results."""
        return self.rows_scanned / max(self.rows_returned, 1)


class EngineAdapter(abc.ABC):
    """A lakehouse engine the harness can drive and instrument."""

    @abc.abstractmethod
    def run(
        self, sql: str, *, phase: Phase, agent_id: int, turn_id: int, delivered: bool
    ) -> QueryStats:
        """Execute `sql` and return its platform-side cost."""

    @abc.abstractmethod
    def list_tables(self) -> list[str]:
        """Return table names, as a catalog listing would."""

    @abc.abstractmethod
    def describe(self, table: str) -> list[tuple[str, str]]:
        """Return (column_name, column_type) pairs for `table`."""


_TEXT_NORMALISE = re.compile(r"\s+")
_TABLE_IN_PLAN = re.compile(r"\b(fact_\w+|dim_\w+)\b")


def normalise_text(sql: str) -> str:
    """Collapse whitespace and case so that only real text differences remain.

    This is intentionally generous: a cache that normalised *less* would show an
    even lower hit rate, so measuring against this makes the exact-match cache
    look as good as it reasonably can. Findings about cache collapse are
    therefore conservative.
    """
    return _TEXT_NORMALISE.sub(" ", sql.strip().lower())


# An alias-qualified reference may quote the column: `d."year"` as well as
# `d.year`. The lookahead must allow the opening quote, or alias stripping
# silently fails on quoted identifiers -- which the test suite caught.
_ALIAS_PREFIX = re.compile(r"\b[A-Za-z_]\w*\.(?=[\"`\[]?[A-Za-z_])")
_DQUOTE = re.compile(r'"')
_INTERNAL = re.compile(r"__internal_\w+")
_CARDINALITY_KEYS = frozenset(
    {"Estimated Cardinality", "Dynamic Filters", "Estimated Cardinalities"}
)
_SEMANTIC_KEYS = (
    "Table",
    "Projections",
    "Filters",
    "Conditions",
    "Join Type",
    "Groups",
    "Aggregates",
    "Order By",
    "Expressions",
    "__expression__",
)


def _canon(value: object) -> str:
    """Canonicalise a plan attribute: drop aliases and engine-internal noise.

    Table aliases are stripped because an agent renaming `f` to `s0` has not
    changed the query. Engine-internal string compression functions are stripped
    because they are an implementation detail of the scan, not of the question.
    Literals are deliberately KEPT: `country = 'US'` and `country = 'UK'` are
    different questions and must not share a cache key.
    """
    if isinstance(value, (list, tuple)):
        text = ",".join(str(v) for v in value)
    else:
        text = str(value)
    text = _INTERNAL.sub("", text)
    text = _ALIAS_PREFIX.sub("", text)
    text = _DQUOTE.sub("", text)  # quoted and unquoted identifiers are the same column
    return _TEXT_NORMALISE.sub(" ", text).strip().lower()


def _fingerprint_plan(node: dict[str, Any]) -> str:
    """Reduce an operator tree to a canonical string.

    We keep operator names, tree shape, and the attributes that determine the
    result -- tables, projected columns, filter predicates including their
    literals, join conditions, grouping keys, aggregates and ordering. We drop
    aliases, whitespace, case, cardinality estimates and runtime counters.

    The intent is that two queries share a fingerprint exactly when they would
    return the same rows. `tests/test_harness.py` checks both directions: that
    semantics-preserving mutations collide, and that questions differing only in
    a filter literal do NOT. The second direction is the important one -- a
    fingerprint that over-collides would make the plan cache serve wrong results
    and would inflate its measured hit rate.
    """
    parts: list[str] = []

    def walk(n: dict[str, Any]) -> None:
        name = n.get("operator_name") or n.get("name") or ""
        extra = n.get("extra_info") or {}
        attrs: list[str] = []
        if isinstance(extra, dict):
            for key in _SEMANTIC_KEYS:
                if key in extra and key not in _CARDINALITY_KEYS:
                    attrs.append(f"{key}={_canon(extra[key])}")
        elif extra:
            attrs.append(_canon(extra))
        parts.append(f"({name}|{'|'.join(attrs)}")
        for child in n.get("children", []) or []:
            walk(child)
        parts.append(")")

    walk(node)
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:16]


def _tables_in_plan(node: dict[str, Any]) -> frozenset[str]:
    """Collect base tables referenced anywhere in an operator tree."""
    found: set[str] = set()

    def walk(n: dict[str, Any]) -> None:
        extra = n.get("extra_info") or {}
        blob = json.dumps(extra) if isinstance(extra, dict) else str(extra)
        found.update(_TABLE_IN_PLAN.findall(blob))
        for child in n.get("children", []) or []:
            walk(child)

    walk(node)
    return frozenset(found)


class DuckDBAdapter(EngineAdapter):
    """Reference adapter. Runs the lakehouse under test in-process.

    DuckDB is used because it runs everywhere with no cluster, which keeps the
    harness reproducible on a laptop and in CI. Its profiler reports
    `cumulative_rows_scanned` and `total_bytes_read`, which are the two metrics
    the scan-amplification analysis needs.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con
        self._profile_path = os.path.join(
            tempfile.mkdtemp(prefix="agentlake-"), "profile.json"
        )
        self.catalog_requests: list[str] = []
        """Every catalog operation issued, in order. Drives skew analysis."""

    def _profile(self) -> dict[str, Any]:
        try:
            with open(self._profile_path) as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}

    def run(
        self, sql: str, *, phase: Phase, agent_id: int, turn_id: int, delivered: bool
    ) -> QueryStats:
        """Execute `sql`, capturing a profile. Failures are recorded, not raised.

        An agent that writes invalid SQL is a normal event in this workload, not
        an exceptional one -- a failed query still costs the platform parse and
        plan time, and it is what triggers the correction phase we are measuring.
        """
        self._con.execute("PRAGMA enable_profiling='json'")
        self._con.execute(f"PRAGMA profiling_output='{self._profile_path}'")
        started = time.perf_counter()
        error: str | None = None
        rows: list[Any] = []
        try:
            rows = self._con.execute(sql).fetchall()
        except duckdb.Error as exc:
            error = f"{type(exc).__name__}: {exc}"
        wall_ms = (time.perf_counter() - started) * 1000.0
        try:
            self._con.execute("PRAGMA disable_profiling")
        except duckdb.Error:
            pass

        prof = self._profile()
        root = prof if "children" in prof else prof.get("root", {})
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
            text_key=hashlib.sha256(normalise_text(sql).encode()).hexdigest()[:16],
            plan_key=_fingerprint_plan(root) if root else "unplanned",
            tables=_tables_in_plan(root) if root else frozenset(),
            delivered=delivered and error is None,
            error=error,
        )

    def list_tables(self) -> list[str]:
        """List tables, recording the catalog request."""
        self.catalog_requests.append("list_tables")
        return [r[0] for r in self._con.execute("SHOW TABLES").fetchall()]

    def describe(self, table: str) -> list[tuple[str, str]]:
        """Describe `table`, recording the catalog request against it."""
        self.catalog_requests.append(f"describe:{table}")
        rows = self._con.execute(f"DESCRIBE {table}").fetchall()
        return [(r[0], r[1]) for r in rows]
