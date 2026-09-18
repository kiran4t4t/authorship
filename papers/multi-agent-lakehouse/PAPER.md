# The Noisy Neighbour Is a Robot: Characterizing Multi-Agent Workloads on the Open Lakehouse

**Target:** VLDB 2027 Industrial Track (deadline 2 Mar 2027 — verify)
**Status:** Draft v0.2 — Sections 1–5 drafted with harness results; 6–7 scaffolded
**Artifact:** `harness/` in this repo (agentlake), 24 tests passing
**Author:** Kiran Bhusnurmath

> **Drafting conventions used below:**
> `[[EVIDENCE: ...]]` marks a claim that must be backed by your measurement or deployment data before
> submission. Do not submit with any of these unresolved.
> `[[VERIFY: ...]]` marks a factual claim taken from search results that I could not read the source
> for. Check it against the primary source.
> `[[DECIDE: ...]]` marks a choice that depends on which evidence route you take.

---

## Abstract

*(Draft — rewrite last, once results are in.)*

Analytical data platforms were designed for a consumer that no longer dominates their workload. A
human analyst issues a small number of deliberate queries, reuses saved logic, and stops when an
answer looks right. An LLM agent does none of these things: it discovers schemas by sampling,
generates several semantically equivalent candidate queries for a single question, retries on error
or low confidence, and abandons partial work when a plan changes. The resulting workload is bursty,
redundant, and exploratory — and enterprises are now pointing fleets of such agents at a shared open
lakehouse.

Existing work evaluates this consumer one agent and one question at a time, asking whether the
generated SQL is correct and, more recently, whether it is efficient. We ask a different question:
what happens to the platform when many agents share it. We present a workload characterization of
multi-agent analytical traffic against an Apache Iceberg lakehouse, drawn from
`[[DECIDE: production telemetry / an open harness we release / both]]`, and show that the aggregate
behaviour is not the sum of its parts. `[[EVIDENCE: headline quantitative findings — scan
amplification factor, result-cache hit-rate collapse, catalog metadata request skew, cost per
answered question vs. per issued query]]`.

We identify three structural mismatches between lakehouse design assumptions and agentic consumption:
workload shape, governance under non-deterministic consumers, and colocation of agent write state
with analytical read state. We describe the mitigations deployed in practice, quantify what they
recover `[[EVIDENCE]]`, and set out what remains unsolved. `[[DECIDE: artifact availability statement]]`

---

## 1. Introduction

The open lakehouse settled its architectural argument faster than most people expected. By 2026 the
table format question is effectively closed — Apache Iceberg v3 reached general availability across
the major platforms, and the REST catalog became the interoperability point that lets independent
engines discover tables, resolve metadata, coordinate atomic commits, and enforce access over a
single copy of data `[[VERIFY: Iceberg v3 GA across Snowflake and Databricks; Polaris as the
vendor-neutral REST catalog; S3 Tables exposing the REST Catalog API]]`. The industry spent fifteen
years arguing about storage formats and metadata layers, and won.

It won just in time to face a consumer it was not designed for.

The design centre of every analytical engine in production today is a human being with a question.
That consumer has properties the whole stack quietly depends on. They issue few queries relative to
the thinking time between them. They reuse logic — saved queries, dashboards, dbt models, semantic
definitions — so the same shapes recur and caches work. They possess tacit knowledge of the schema
and do not need to rediscover it. They exercise judgement about cost: an analyst who watches a query
scan a petabyte will usually add a partition filter before running it again. And critically, their
access pattern is stable enough to be audited, which is the assumption underneath every row- and
column-level security model in the field.

An LLM agent violates all five.

A single user question handed to an agent does not become a single query. It becomes a burst:
metadata introspection to find candidate tables, sampling to understand column semantics and
cardinality, one or more candidate queries — often several semantically equivalent formulations, which
in a Big Data engine can differ enormously in physical plan quality, shuffle volume and scan
efficiency `[[VERIFY: this characterization is from arXiv:2602.21480 — read before citing]]` — then
self-correction retries when a query errors or the agent's own confidence check fails, and abandoned
work when the plan changes mid-task. The agent has no tacit schema knowledge; it rediscovers context
on every cold start. It has no felt sense of cost. And its access pattern is generated at inference
time from an input that may be adversarial.

One agent behaving this way is a curiosity. The situation now arriving in enterprises is different:
fleets of agents — embedded in applications, invoked by other agents, running on schedules — sharing
one governed lakehouse. `[[EVIDENCE: your deployment's scale — how many distinct agents, what fraction
of total platform query volume is agent-originated, growth rate over the observation window. This
number is the paper's justification for existing; make it concrete.]]`

The research community has moved quickly on the agent side of this interface and barely at all on the
platform side. A now-substantial benchmark literature asks whether an agent produces *correct* SQL,
and the most recent work has extended that to whether it produces *efficient* SQL at scale
`[[VERIFY: SQLStorm, Text-to-Big SQL, AgenticDataBench, FDABench — read before characterizing]]`.
This is real progress, and it is all single-agent. Correctness and per-query efficiency are
properties of one agent answering one question. They tell you nothing about what a hundred agents do
to a shared result cache, a shared catalog, or a shared cost centre.

That gap is the subject of this paper. Our contributions:

1. **A workload characterization of multi-agent analytical traffic** against an open Iceberg
   lakehouse, along platform-side axes that the accuracy-oriented literature does not instrument:
   scan amplification, cache behaviour, catalog metadata request distribution, small-file and
   manifest pressure, and cost per *answered question* rather than per *issued query*
   (Sections 3 and 5).
2. **A taxonomy of agent-originated query traffic** that separates the phases of an agent's turn —
   discovery, sampling, candidate generation, correction, and abandonment — and shows that these
   phases have sharply different platform cost profiles, which matters because they are conventionally
   measured as one undifferentiated workload (Section 3).
3. **An account of three structural mismatches** between lakehouse design assumptions and agentic
   consumption: workload shape, governance under non-deterministic consumers, and colocated agent
   write state (Section 4).
4. **Deployed mitigations and what they actually recover**, including the semantic layer as an
   enforcement boundary rather than merely a convenience layer (Section 6).
5. `[[DECIDE: if Route B — an open harness for reproducing multi-agent platform load, released as an
   artifact. State it here; it is a large part of this paper's value.]]`

We deliberately do not contribute another accuracy benchmark. The question of whether an agent gets
the answer right is well covered. The question of whether the platform survives a thousand agents
trying is not.

---

## 2. Background

### 2.1 The open lakehouse as it now stands

`[[Draft. Cover concisely — reviewers know this, so compress hard and cite rather than explain:
Iceberg/Delta convergence and the v3 feature set; the REST catalog as the coordination point;
separation of storage, catalog, and engine; multi-engine access to one copy. Land on the specific
architectural properties that matter later: metadata is a shared service with its own request
pattern; result caches are keyed on query text or plan; partitioning and clustering assume a
predictable predicate distribution. Each of those is an assumption an agent fleet breaks.]]`

`[[VERIFY: Iceberg v4's proposed adaptive metadata tree, and the suggestion that Delta 5.0 adopt the
same structure. This came from a vendor blog. If it holds it is directly relevant to Section 4.1 —
adaptive metadata is exactly the kind of change agent workloads would stress differently — but do not
cite a vendor blog as settled fact.]]`

### 2.2 The agentic consumer

`[[Draft. The anatomy of an agent turn against a data platform. Tool-calling loop; schema
introspection; the Model Context Protocol as the emerging interface between agent and governed
data; semantic layer serving governed measures and dimensions rather than raw DDL. Note the industry
convergence here — multiple independent vendors arrived at "expose the semantic model over MCP, not
the tables" within roughly the same year, which is worth remarking on as a signal that the raw-table
interface is failing in the field. VERIFY against the vendor sources in NOTES.md.]]`

### 2.3 What the existing literature measures

`[[Draft. Short, fair, and precise. The accuracy benchmarks and what they established; the recent
efficiency extension and why it mattered; then the scoping observation that all of it is single-agent.
Be scrupulously fair here — this is the section where reviewers who wrote those papers will check
whether you read them. Do not overstate the gap. Some of these papers may gesture at concurrency in
future-work sections; acknowledge that explicitly if so.]]`

---

## 3. Characterizing the workload

### 3.1 A phase taxonomy of the agent turn

We decompose a single agent turn into five phases, each with a distinct platform cost signature:

| Phase | What the agent is doing | Platform cost signature |
|---|---|---|
| **Discovery** | Enumerating namespaces, tables, schemas | Catalog metadata requests; negligible scan; high request count, low bytes |
| **Sampling** | `LIMIT`ed reads to learn column semantics, cardinality, value distributions | Small scans, poor selectivity, frequently unpartitioned; high file-open count relative to bytes returned |
| **Candidate generation** | Issuing one or more full analytical queries | The only phase resembling a conventional BI workload — and the only one existing benchmarks measure |
| **Correction** | Re-issuing after an error, an empty result, or a failed self-check | Near-duplicate of the prior query; defeats exact-match result caching while duplicating nearly all of its work |
| **Abandonment** | Work discarded when the plan changes or the turn is cut short | Full cost incurred, zero value delivered; invisible to any accuracy metric |

The taxonomy matters because these phases are conventionally aggregated into one number — "agent query
volume" — and that number misleads in both directions. Discovery and sampling are cheap in bytes but
expensive in request count, which is exactly the load profile that stresses a catalog rather than an
engine. Correction and abandonment are expensive in bytes and invisible to accuracy metrics, so a
system that scores well on a text-to-SQL benchmark can be ruinous in production.

`[[EVIDENCE: phase distribution in your workload. What fraction of issued queries, and of bytes
scanned, falls in each phase? The abandonment fraction is the number most likely to surprise a
reviewer — if you can measure it, lead with it.]]`

### 3.2 Measurement methodology

We release `agentlake`, an open harness that drives a configurable fleet of analytical
agents against a lakehouse under test and instruments the platform side. It runs
in-process on DuckDB over a natively generated star schema, with no cluster, no API key
and no network access, so any number reported here can be reproduced on a laptop.

**Agents are synthetic.** No model is in the loop. A turn is generated from an explicit
phase model — counts for discovery and sampling, a correction probability, an
abandonment probability — and is fully determined by a seed. This is a deliberate
trade: we give up the realism of a live agent's decisions in exchange for
reproducibility and for the ability to vary one parameter at a time. The phase model
reproduces the *shape* of agent traffic, not the judgement of any particular agent.

**Every phase parameter is an assumption, not a measurement.** The harness records this
explicitly: each `PhaseModel` carries a `provenance` field, and the defaults declare
themselves uncalibrated. All results below were produced with uncalibrated defaults and
should be read as characterizing the *structure* of the cost, not its magnitude in any
real deployment. `[[Recalibrate against production traces if available; this is the
single change that would most strengthen the paper.]]`

**Corrections are constructed, not hoped for.** To ask whether a result cache catches
correction-phase queries, we need correction queries that are semantically equivalent to
what they replace. The harness rewrites a canonical query into equivalent forms — alias
renaming, comma-join, CTE wrapping, tautological predicates, reformatting — and the test
suite asserts that every rewrite returns identical rows. Without that assertion, any
cache finding would be an artifact of the mutator emitting genuinely different queries.

**Two caches are simulated over one query stream.** `ExactTextCache` keys on normalised
query text, as analytical engines conventionally do. `PlanCache` keys on a fingerprint
of the optimised operator tree. Simulating both over the identical stream provides the
counterfactual that a real engine, which gives you one cache, cannot.

The fingerprint strips aliases, whitespace, case and cardinality estimates but retains
filter literals. This matters more than it sounds: an earlier, coarser fingerprint
collided `revenue by region` with `units by store`, which would have made the plan cache
serve wrong results and inflated its measured hit rate. The test suite now checks both
directions — equivalent rewrites must collide, and questions differing only in a filter
literal must not.

**The sweep holds total work constant.** Fleet size varies; total turns across the fleet
does not. Our first attempt held turns *per agent* constant instead, which made larger
fleets do proportionally more total work, warmed the shared cache further, and produced
a spurious *negative* scaling exponent — per-question cost appeared to fall with fleet
size for reasons that had nothing to do with concurrency. Questions are drawn Zipf-style
from a parameterised space so that question diversity is a property of the workload
rather than an accident of fleet size.

### 3.3 Findings

Reference configuration: fleet sizes 1, 2, 4, 8, 16 and 32; 192 total turns held
constant across sizes; 400,000 fact rows; uncalibrated default phase model. Each fleet
size gets a fresh engine and fresh caches. The control condition is an idealised human
analyst — one deliberate query per question, no rediscovery, no retry, no abandonment.

| | agentic | human control | ratio |
|---|---|---|---|
| Queries per answered question | 3.81 | 1.00 | 3.8x |
| Rows scanned per answered question | 969,865 | 375,731 | **2.58x** |
| Rows scanned per issued query | 254,416 | 375,731 | **0.68x** |
| Scanned rows never delivered | 60.7% | 0.0% | — |
| Rows scanned on abandoned turns | 7.6% | 0.0% | — |

**Finding 1: per-query monitoring inverts the sign of the result.** Agent-issued queries
scan 32% *fewer* rows each than the control, while the workload costs 2.58x more per
unit of delivered value. A conventional per-query dashboard would report that the
platform had become more efficient. The discrepancy is exactly the fan-out factor: the
denominator changed and the metric did not follow. We consider this the paper's most
practically consequential result, because it means the instrumentation most
organizations already have will mislead them in the reassuring direction.

**Finding 2: roughly 60% of scanned rows never reach a user.** Sampling, superseded
corrections and abandoned turns account for the majority of platform work. Abandonment
alone is about 7.6% — and abandoned work is invisible to every accuracy metric in the
existing literature, because a benchmark that scores the final answer cannot see what
was discarded on the way to it.

**Finding 3: plan-keyed caching closes about 41% of the residual misses** left by
exact-text caching (93.4% vs 88.8% hit rate). The gap is the correction phase: rewrites
that are semantically identical and textually distinct. This is a concrete, actionable
result for an engine team, and it does not require any change to how agents behave.

**Finding 4 (negative): no superlinearity.** Cost per answered question is flat in fleet
size from 1 to 32 agents — a fitted scaling exponent of k = -0.026, where k = 0 is exact
linearity. We expected to find the opposite. Under this configuration, agent cost is
additive rather than compounding.

We report this negative result prominently because it constrains the argument. The case
for treating agentic workloads as a distinct systems problem rests on the *per-question*
cost structure and on the measurement inversion in Finding 1 — not on any claim that
matters get worse with scale. It also comes with a real caveat: an in-process engine has
no cluster, no shuffle, no shared executor pool and no catalog service under load, so
contention effects that would appear on a distributed engine **cannot** appear here.
Finding 4 is evidence about this configuration, and re-testing it on a distributed
engine is the first thing we would do next. `[[If you can run the harness against Trino
or Spark before submission, do it — a distributed replication would materially
strengthen the paper, and a *different* answer there would be more interesting still.]]`

**Catalog skew** sits at 0.27–0.31 (Gini) and is stable across fleet sizes. We note it
without drawing a conclusion: skew here is a product of our question-driven discovery
model, and while an earlier uniform-draw model made skew unmeasurable by construction,
the present value still reflects modelling choices more than platform behaviour.

## 4. Three structural mismatches

### 4.1 Workload shape

`[[Draft from findings. The argument: partitioning, clustering, caching and cost-based optimization
all assume a predicate distribution that recurs. Agent traffic has a long tail of one-shot predicates
generated fresh at inference time, plus a heavy head of near-duplicate corrections. Neither is what
these mechanisms were tuned for. Connect back to specific findings from 3.3 rather than arguing in
the abstract.]]`

### 4.2 Governance under non-deterministic consumers

This is the mismatch with the fewest good answers, and it deserves the section's weight.

Row- and column-level security, data masking, and access audit were built for consumers whose access
pattern is a stable property of a known principal. An analyst's queries vary, but the set of things
they might legitimately ask for is bounded by their role, and unusual access shows up as an anomaly
worth investigating. An agent's access pattern is generated at inference time and is a function of
its input. If that input can be influenced — by a document the agent reads, by a prior agent's output,
by a user's prompt — then the access pattern can be influenced. Prompt injection stops being a model
safety concern and becomes a data access control problem.

The response emerging in practice is to stop exposing raw tables to agents at all, and instead expose
a governed semantic model — measures, dimensions, and their definitions — over a protocol the agent
introspects and calls, with the Model Context Protocol currently filling that role
`[[VERIFY against the vendor sources in NOTES.md; this is an industry pattern, and I want to be
careful not to present vendor positioning as an established architectural result]]`. Framed
generously, this is a good idea for a second reason: it fixes metric consistency, because an agent
pointed at raw tables re-derives joins, grain and metric logic on every prompt and can return
different answers to the same question on different runs.

But the security argument for this boundary is weaker than it is usually presented. `[[Draft the
critique. The semantic layer constrains *what* can be asked, not *why* it is being asked. An injected
agent restricted to governed measures can still exfiltrate through a sequence of individually
legitimate aggregate queries. The boundary narrows the attack surface; it does not close it. Whether
your deployment observed anything like this is worth stating either way — a clean negative finding,
honestly reported, is credible and rare.]]`

`[[EVIDENCE: what you actually deployed for agent governance, and what it cost in capability. The
honest trade-off — how much agent usefulness was given up for how much control — is the kind of
detail an industrial-track reviewer values and a research paper cannot supply.]]`

### 4.3 Colocated agent state

`[[Draft. Agents write: conversation memory, task checkpoints, intermediate results, execution traces.
These are small, frequent, latency-sensitive writes — an OLTP shape — landing on infrastructure
designed for large, batched, throughput-oriented analytical writes. Meanwhile the agent's reads want
OLAP. Options observed in the field: separate OLTP store alongside the lakehouse (operational
complexity, consistency seams), NoSQL store for agent memory (the common choice), or forcing it into
the lakehouse (small-file and manifest pressure, per 3.3). Describe what you chose and what it cost.
This subsection is where your OLTP/OLAP/NoSQL background is doing work no purely-analytics author
could do — give it room.]]`

---

## 5. Artifact

`agentlake` is released with this paper. `[[DECIDE: repository URL and licence before
submission.]]`

The harness is ~1,100 lines of Python with one dependency (DuckDB) and a suite of 24
tests. The tests are part of the contribution rather than hygiene: the central claim
that correction-phase queries *should* be cache hits is only meaningful if the mutations
really preserve meaning, so the suite executes every mutation of every corpus question
and compares result sets. Two substantive bugs reported in Section 3 — the over-colliding
plan fingerprint and the alias rewriter that silently failed on quoted identifiers —
were found by those tests rather than by inspection.

To target another engine, implement the `EngineAdapter` interface; the report contract
is unchanged. A distributed adapter is the obvious next contribution, for the reason
given in Finding 4.

## 6. Mitigations in practice

`[[EVIDENCE + draft. Candidate mitigations to cover, each with what it recovered:

- Semantic-layer / MCP boundary: metric consistency, governance, and the capability cost.
- Semantic result caching keyed on normalized plan rather than query text — the direct response to
  the correction-phase finding, if 3.3 confirms it.
- Phase-aware admission control or rate limiting: treating discovery traffic differently from
  candidate-generation traffic.
- Pre-computed schema and profile context, so agents stop rediscovering the same metadata cold.
  (Related: arXiv:2412.07786 on agentic schema refinement, arXiv:2602.13521 on tribal knowledge —
  read both.)
- Cost attribution per agent and per answered question, and what changed once it was visible. This
  one is often the highest-leverage and least technical intervention; say so if that was your
  experience.
- Small-model routing for discovery and sampling phases, reserving frontier models for candidate
  generation. (arXiv:2506.02153 argues small models suit agentic work generally — read before citing.)

For each: what it recovered, what it cost, and what it did not fix. The "did not fix" column is what
separates an industrial-track paper from a vendor talk.]]`

---

## 7. Open problems

`[[Draft last. Strongest candidates from the analysis above:

1. Caching and plan reuse under semantically-near-duplicate queries. Exact-match caching is the wrong
   primitive for this consumer, and nothing has replaced it.
2. Access control for consumers whose intent is not verifiable from their identity. The semantic layer
   narrows the surface; the general problem is open.
3. Benchmarks that measure platform-side aggregate behaviour rather than per-query accuracy. Name
   what such a benchmark would need to specify.
4. Cost models and admission control for workloads where the unit of value is an answered question
   and the unit of cost is an issued query.
5. Storage layouts for colocated analytical reads and agent-state writes.

Keep it short and specific. A long open-problems section reads as padding.]]`

---

## 8. Conclusion

`[[Write last.]]`

---

## References

`[[Build from NOTES.md. Read every paper before citing it. The related-work list there was assembled
from search results in an environment where the papers themselves could not be fetched — the titles
and identifiers are real, the characterizations are provisional.]]`
