"""The five phases of an agent turn, and the parameters governing them.

A single user question handed to an analytical agent does not become a single
query. It becomes a burst with an internal structure. We model that structure
explicitly so that platform cost can be attributed per phase rather than
aggregated into one undifferentiated "agent query volume" number.

Every default in PhaseModel is an ASSUMPTION, not a measurement. They are
starting values chosen to be plausible, and they are the first thing that should
be recalibrated against real traces. Papers using this harness must report the
PhaseModel they ran with; `PhaseModel.provenance` exists to make that explicit.
"""

from __future__ import annotations

import dataclasses
import enum


class Phase(enum.Enum):
    """Phases of a single agent turn.

    The ordering is the order in which they typically occur, but a turn does not
    necessarily pass through all of them: a confident agent may go straight from
    DISCOVERY to CANDIDATE and stop.
    """

    DISCOVERY = "discovery"
    """Enumerating namespaces, tables and schemas. High request count, low bytes.
    Stresses the catalog rather than the engine."""

    SAMPLING = "sampling"
    """LIMITed reads to learn column semantics, cardinality and value
    distributions. Small scans, poor selectivity, often unpartitioned."""

    CANDIDATE = "candidate"
    """A full analytical query attempting to answer the question. The only phase
    resembling a conventional BI workload, and the only one the existing
    text-to-SQL benchmark literature measures."""

    CORRECTION = "correction"
    """Re-issue after an error, an empty result, or a failed self-check.
    Semantically near-identical to the query it replaces, but textually
    distinct -- which is the property that defeats exact-match result caching."""

    # NOTE: abandonment is deliberately NOT a phase. Building the harness made
    # clear that it is the *fate* of work, not a kind of work: a discovery query
    # and a candidate query can both be abandoned. Modelling it as a phase
    # destroyed per-phase cost attribution, because relabelling an abandoned
    # correction as "abandoned" lost the fact that it was a correction. It is
    # carried on QueryStats.abandoned instead, orthogonal to phase.


@dataclasses.dataclass(frozen=True)
class PhaseModel:
    """Parameters governing how a synthetic agent turn is generated.

    Attributes:
        n_discovery: Catalog introspection calls issued per turn.
        n_sampling: Sampling queries issued per turn.
        n_candidate: Full analytical queries issued per turn, before corrections.
        p_correction: Probability that any given candidate triggers a correction.
        max_corrections: Cap on correction attempts per candidate, modelling an
            agent that gives up rather than retrying forever.
        p_abandon: Probability that a completed candidate's work is discarded.
        sample_limit: Row limit applied to sampling queries.
        p_explore: Probability that a discovery call inspects a table unrelated
            to the current question, rather than one the question needs. Models
            an agent casting around. Setting this to 1.0 recovers uniform
            discovery and flattens catalog skew by construction.
        provenance: Free text recording where these numbers came from. Set this
            to a citation or a trace identifier when calibrating against real
            data; leave the default to signal that the values are uncalibrated.
    """

    n_discovery: int = 3
    n_sampling: int = 2
    n_candidate: int = 1
    p_correction: float = 0.35
    max_corrections: int = 2
    p_abandon: float = 0.15
    sample_limit: int = 100
    p_explore: float = 0.25
    provenance: str = "UNCALIBRATED defaults - not measured, see phases.py docstring"

    def __post_init__(self) -> None:
        if not 0.0 <= self.p_correction <= 1.0:
            raise ValueError(f"p_correction must be in [0,1], got {self.p_correction}")
        if not 0.0 <= self.p_abandon <= 1.0:
            raise ValueError(f"p_abandon must be in [0,1], got {self.p_abandon}")
        if not 0.0 <= self.p_explore <= 1.0:
            raise ValueError(f"p_explore must be in [0,1], got {self.p_explore}")
        if self.max_corrections < 0:
            raise ValueError(f"max_corrections must be >= 0, got {self.max_corrections}")
        for name in ("n_discovery", "n_sampling", "n_candidate", "sample_limit"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")

    @property
    def is_calibrated(self) -> bool:
        """Whether these parameters claim to derive from measured data."""
        return not self.provenance.startswith("UNCALIBRATED")


HUMAN_BASELINE = PhaseModel(
    n_discovery=0,
    n_sampling=0,
    n_candidate=1,
    p_correction=0.0,
    max_corrections=0,
    p_abandon=0.0,
    p_explore=0.0,
    provenance="UNCALIBRATED idealisation: one deliberate query, no rediscovery, "
    "no retry storm. The control condition, not a claim about real analysts.",
)
"""The control condition: what the platform was designed for.

Comparing a fleet under this model against the same fleet under an agentic model
isolates the cost of the consumer change from the cost of the concurrency.
"""
