"""Question corpus and semantics-preserving query mutation.

Two jobs:

1. A corpus of analytical questions with a canonical SQL answer each, standing
   in for what a real agent would generate. Using a fixed corpus rather than a
   live LLM is what makes the harness deterministic, free to run, and
   reproducible by a reviewer with no API key.

2. A mutator that rewrites a query into a semantically equivalent but textually
   different form. This is the scientific core. Agents in the correction phase
   re-issue near-identical queries, and the question is whether a result cache
   catches them. Mutation lets us construct that condition deliberately instead
   of hoping it occurs.

The mutations below are all meaning-preserving on this schema. That is a claim
the test suite checks by executing both forms and comparing results.
"""

from __future__ import annotations

import dataclasses
import random
import re


@dataclasses.dataclass(frozen=True)
class Question:
    """An analytical question and its canonical SQL.

    Attributes:
        qid: Stable identifier, used to group an agent's attempts at one question.
        text: The question in natural language, as a user would pose it.
        sql: Canonical SQL answering it.
        tables: Base tables the canonical form touches.
    """

    qid: str
    text: str
    sql: str
    tables: frozenset[str]


CORPUS: tuple[Question, ...] = (
    Question(
        "q_rev_by_category",
        "What is total net revenue by product category?",
        """SELECT p.category, SUM(f.net_amount) AS revenue
           FROM fact_sales f JOIN dim_product p ON f.product_id = p.product_id
           GROUP BY p.category ORDER BY revenue DESC""",
        frozenset({"fact_sales", "dim_product"}),
    ),
    Question(
        "q_rev_by_region",
        "Which sales region brings in the most revenue?",
        """SELECT s.region, SUM(f.net_amount) AS revenue
           FROM fact_sales f JOIN dim_store s ON f.store_id = s.store_id
           GROUP BY s.region ORDER BY revenue DESC""",
        frozenset({"fact_sales", "dim_store"}),
    ),
    Question(
        "q_top_customers",
        "Who are the top spending enterprise customers?",
        """SELECT c.customer_name, SUM(f.net_amount) AS spend
           FROM fact_sales f JOIN dim_customer c ON f.customer_id = c.customer_id
           WHERE c.segment = 'enterprise'
           GROUP BY c.customer_name ORDER BY spend DESC LIMIT 20""",
        frozenset({"fact_sales", "dim_customer"}),
    ),
    Question(
        "q_quarterly_trend",
        "How did revenue trend by quarter?",
        """SELECT d.year, d.quarter, SUM(f.net_amount) AS revenue
           FROM fact_sales f JOIN dim_date d ON f.date_id = d.date_id
           GROUP BY d.year, d.quarter ORDER BY d.year, d.quarter""",
        frozenset({"fact_sales", "dim_date"}),
    ),
    Question(
        "q_country_category",
        "Revenue by customer country and product category?",
        """SELECT c.country, p.category, SUM(f.net_amount) AS revenue
           FROM fact_sales f
           JOIN dim_customer c ON f.customer_id = c.customer_id
           JOIN dim_product p ON f.product_id = p.product_id
           GROUP BY c.country, p.category ORDER BY revenue DESC""",
        frozenset({"fact_sales", "dim_customer", "dim_product"}),
    ),
    Question(
        "q_units_by_store",
        "How many units did each store sell on large orders?",
        """SELECT s.store_name, SUM(f.quantity) AS units
           FROM fact_sales f JOIN dim_store s ON f.store_id = s.store_id
           WHERE f.quantity > 5
           GROUP BY s.store_name ORDER BY units DESC""",
        frozenset({"fact_sales", "dim_store"}),
    ),
)


def _to_comma_join(sql: str) -> str:
    """Rewrite explicit JOIN ... ON into comma-join with WHERE predicates.

    Only applies to single-join queries; multi-join rewriting by string
    substitution is not safe, so we decline rather than risk changing meaning.
    """
    if sql.count(" JOIN ") != 1:
        return sql
    head, _, tail = sql.partition(" JOIN ")
    table_part, _, rest = tail.partition(" ON ")
    cond, sep, remainder = rest.partition("\n")
    if not sep:
        return sql
    joined = f"{head}, {table_part}"
    if " WHERE " in remainder:
        before, _, after = remainder.partition(" WHERE ")
        return f"{joined} WHERE {cond.strip()} AND {after}{before}"
    return f"{joined} WHERE {cond.strip()}\n{remainder}"


def _alias_shift(sql: str) -> str:
    """Rename table aliases. Meaning-preserving, text-changing.

    Uses word-boundary regex rather than string replacement: an alias can be
    followed by a newline as easily as a space, and an earlier substring-based
    version renamed the references without renaming the declaration, producing
    invalid SQL. The test suite caught it.
    """
    out = sql
    for old, new in (("f", "s0"), ("p", "d1"), ("c", "k2"), ("s", "t3"), ("d", "e4")):
        out = re.sub(rf"\b{old}\b(?=\s*\.)|(?<=\s){old}\b(?=\s|$|\n)", new, out)
    return out


def _wrap_in_cte(sql: str) -> str:
    """Wrap the query in a trivial CTE. The optimiser inlines it away."""
    return f"WITH agent_scratch AS (\n{sql}\n) SELECT * FROM agent_scratch"


def _reformat(sql: str) -> str:
    """Change only whitespace and keyword case."""
    return " ".join(sql.split()).replace("SELECT", "select").replace("FROM", "from")


def _add_true_predicate(sql: str) -> str:
    """Append a tautological predicate, as an agent adding a redundant guard."""
    if " WHERE " in sql:
        return sql.replace(" WHERE ", " WHERE 1=1 AND ", 1)
    if "\n           GROUP BY" in sql:
        return sql.replace("\n           GROUP BY", "\n           WHERE 1=1 GROUP BY", 1)
    return sql


MUTATIONS = (_reformat, _alias_shift, _wrap_in_cte, _add_true_predicate, _to_comma_join)
"""Meaning-preserving rewrites, ordered roughly by how much text they change.

`tests/test_sqlgen.py` asserts that each one returns identical result sets to
the canonical form. A mutation that fails that test is a bug in the harness, not
a finding about caches.
"""


def mutate(sql: str, rng: random.Random) -> str:
    """Return a semantically equivalent rewrite of `sql`.

    Args:
        sql: Canonical SQL.
        rng: Seeded RNG, so a run is reproducible.

    Returns:
        A rewritten query. May equal `sql` if the chosen mutation declined to
        apply -- which is itself realistic, since an agent sometimes re-issues a
        query verbatim.
    """
    return rng.choice(MUTATIONS)(sql)


def break_query(sql: str, rng: random.Random) -> str:
    """Return a *broken* variant, standing in for an agent's failed first attempt.

    Errors are drawn from the failure modes agents actually exhibit against a
    real schema: a hallucinated column, a wrong table, an ungrouped column.
    """
    kind = rng.choice(("bad_column", "bad_table", "bad_group"))
    if kind == "bad_column":
        return sql.replace("net_amount", "total_revenue_amt", 1)
    if kind == "bad_table":
        return sql.replace("fact_sales", "sales_fact", 1)
    return sql.replace("GROUP BY", "GROUP BY 999,", 1)


# ---------------------------------------------------------------------------
# Question space
#
# A fixed six-question corpus is not enough to run a fleet-size sweep. With a
# small corpus and a shared cache, a larger fleet trivially repeats itself, the
# cache hit rate climbs with fleet size, and cost per answered question *falls*
# -- an artifact of the corpus, not a property of the workload. Our first sweep
# reproduced exactly that, which is why this exists.
#
# Real deployments ask a few common questions very often and a long tail of
# specific ones rarely. We model that with a parameterised question space drawn
# from a Zipf-like distribution, so that question diversity is a property of the
# workload rather than an accident of fleet size.
# ---------------------------------------------------------------------------

_FACT_THRESHOLDS: tuple[int, ...] = (2, 4, 6, 8)
"""Quantity thresholds on the fact table. These apply to every question
regardless of which dimensions it joins, which is what keeps the question space
large enough for a fleet-size sweep to be meaningful."""

_FILTERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("dim_customer", "c.country", ("US", "UK", "DE", "IN", "JP", "BR")),
    ("dim_customer", "c.segment", ("enterprise", "mid_market", "smb")),
    ("dim_product", "p.category", ("electronics", "apparel", "grocery", "home", "sports")),
    ("dim_store", "s.region", ("north", "south", "east", "west")),
)


def question_space(base: tuple[Question, ...] = CORPUS) -> tuple[Question, ...]:
    """Expand `base` into a larger space by parameterising filter predicates.

    Each base question is combined with every applicable (column, value) filter
    on a dimension it already joins, producing a distinct question with distinct
    SQL. The base questions are retained as the unfiltered head of the
    distribution.

    Returns:
        The expanded question space, base questions first.
    """
    out: list[Question] = list(base)
    for q in base:
        for table, column, values in _FILTERS:
            if table not in q.tables:
                continue
            # Skip columns the question already constrains. Injecting a filter
            # on such a column yields either an exact duplicate of the base
            # question or a contradiction that returns nothing -- degenerate
            # either way, and both would show up as spurious plan-key
            # collisions rather than as distinct questions.
            if column in q.sql:
                continue
            for value in values:
                sql = _inject_filter(q.sql, column, value)
                if sql == q.sql:
                    continue
                out.append(
                    Question(
                        qid=f"{q.qid}__{column.replace('.', '_')}_{value}",
                        text=f"{q.text[:-1]} for {column.split('.')[-1]} = {value}?",
                        sql=sql,
                        tables=q.tables,
                    )
                )
    for q in base:
        if "f.quantity" in q.sql:
            continue
        for threshold in _FACT_THRESHOLDS:
            sql = _inject_filter(q.sql, "f.quantity >", str(threshold), quote=False)
            if sql == q.sql:
                continue
            out.append(
                Question(
                    qid=f"{q.qid}__qty_gt_{threshold}",
                    text=f"{q.text[:-1]} where quantity > {threshold}?",
                    sql=sql,
                    tables=q.tables,
                )
            )
    return tuple(out)


def _inject_filter(sql: str, column: str, value: str, *, quote: bool = True) -> str:
    """Add a predicate to a query's WHERE clause, or create one.

    Args:
        sql: Query to modify.
        column: Column reference, optionally including a comparison operator.
        value: Literal to compare against.
        quote: Whether to quote `value` as a string literal.

    Returns:
        The modified query, or `sql` unchanged if no safe injection point is
        found -- a failed injection degrades to a duplicate question rather than
        to broken SQL.
    """
    literal = f"'{value}'" if quote else value
    predicate = f"{column} = {literal}" if quote else f"{column} {literal}"
    if " WHERE " in sql:
        return sql.replace(" WHERE ", f" WHERE {predicate} AND ", 1)
    marker = "GROUP BY"
    idx = sql.find(marker)
    if idx == -1:
        return sql
    return f"{sql[:idx]}WHERE {predicate}\n           {sql[idx:]}"


def zipf_choice(space: tuple[Question, ...], rng, skew: float = 1.1) -> Question:
    """Draw a question with Zipf-like frequency: a popular head, a long tail.

    Args:
        space: Question space to draw from.
        rng: Seeded `random.Random`.
        skew: Zipf exponent. Higher concentrates more mass on the head;
            `skew=0` gives a uniform draw.

    Raises:
        ValueError: If `space` is empty.
    """
    if not space:
        raise ValueError("question space must not be empty")
    weights = [1.0 / ((i + 1) ** skew) for i in range(len(space))]
    return rng.choices(space, weights=weights, k=1)[0]
