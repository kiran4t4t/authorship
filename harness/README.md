# agentlake

A harness for characterizing **multi-agent workloads on a lakehouse**.

It drives a configurable fleet of analytical agents against a lakehouse under test
and instruments the **platform** side: query fan-out, scan amplification, result-cache
behaviour under semantically equivalent rewrites, catalog request skew, and cost per
*answered question* rather than per *issued query*.

It deliberately does **not** measure answer accuracy. That is well covered by the
text-to-SQL benchmark literature (SQLStorm, Text-to-Big SQL, AgenticDataBench,
FDABench). Those benchmarks evaluate one agent answering one question. This harness
asks what happens to the platform when many agents share it.

## Quick start

```bash
pip install duckdb
python run_sweep.py --sizes 1,2,4,8,16,32 --turns 192 --rows 400000
python run_sweep.py --sizes 1,2,4,8,16,32 --turns 192 --rows 400000 --baseline  # control
python -m unittest discover -s tests -v
```

No cluster, no API key, no network. DuckDB runs in-process and the dataset is
generated natively, so a reviewer can reproduce any reported number on a laptop.

## Design

**Phase model.** A user question handed to an agent does not become one query. It
becomes a burst: `DISCOVERY` (catalog introspection), `SAMPLING` (LIMITed reads to
learn column semantics), `CANDIDATE` (the real analytical query), `CORRECTION`
(re-issue after an error or failed self-check). Abandonment is tracked as a
*disposition* on each query rather than as a phase, because a discovery query and a
candidate query can both be abandoned — modelling it as a phase destroyed per-phase
cost attribution.

**Semantics-preserving mutation.** Correction-phase queries are near-duplicates of
what they replace. The mutator rewrites a query into an equivalent form — alias
renaming, comma-join, CTE wrapping, tautological predicates, reformatting — so the
cache question can be posed deliberately. `tests/` asserts every mutation returns
identical rows.

**Two cache models.** `ExactTextCache` keys on normalised query text (what engines
conventionally ship). `PlanCache` keys on the optimised operator tree. Simulating
both over the identical query stream gives the counterfactual a real engine cannot.

**Plan fingerprinting.** Derived from the operator tree with aliases, whitespace,
case and cardinality estimates stripped, but **filter literals kept**. The test suite
checks both directions: semantics-preserving mutations must collide, and questions
differing only in a filter literal must not. Over-collision would make the plan cache
serve wrong results and inflate its measured hit rate.

## Experiment design

The sweep holds **total turns constant** across fleet sizes. Holding turns *per agent*
constant instead would make a larger fleet do proportionally more total work, warming
the shared cache further and lowering per-question cost for reasons unrelated to
concurrency. An early version did exactly that and produced a spurious negative
scaling exponent. Fixing total work makes fleet size the only variable.

Questions are drawn Zipf-style from a parameterised space, so question diversity is a
property of the workload rather than an accident of fleet size.

## Calibration

**Every default in `PhaseModel` is an assumption, not a measurement.** `provenance`
records where the numbers came from and `is_calibrated` reports whether they claim to
derive from real data; the defaults declare themselves uncalibrated. Any paper using
this harness must report the `PhaseModel` it ran with. Recalibrating against real
agent traces is the single highest-value contribution someone could make to it.

## Limitations

Read these before citing any number this harness produces.

- **In-process engine.** DuckDB has no cluster, no shuffle, no shared executor pool
  and no catalog service under load. Contention effects that would appear on Trino,
  Spark or a vendor lakehouse **cannot** appear here. The flat scaling result below is
  evidence about this configuration, not about distributed engines.
- **Simulated caches.** Hit rates are computed over the real query stream but the
  caches are models, not the engine's own.
- **Synthetic agents.** No LLM is in the loop. The phase model reproduces the *shape*
  of agent traffic, not the decisions of any particular agent.
- **`bytes_read` is unreliable** for in-memory tables; `rows_scanned` is the metric to
  use.
- **Six base questions.** The parameterised space expands them, but the schema is one
  star and the question shapes are conventional aggregations.

## Findings from the reference configuration

Run: 6 fleet sizes (1–32), 192 total turns, 400k fact rows, uncalibrated defaults.
Raw output in `results/`.

| | agentic | human control | ratio |
|---|---|---|---|
| Queries per answered question | 3.81 | 1.00 | **3.8x** |
| Rows scanned per answered question | 969,865 | 375,731 | **2.58x** |
| Rows scanned per *issued query* | 254,416 | 375,731 | **0.68x** |
| Scanned rows never delivered | 60.7% | 0.0% | — |

1. **Per-query monitoring inverts the sign of the result.** Agent queries scan 32%
   *fewer* rows each than the human baseline, while costing 2.58x more per unit of
   delivered value. Conventional per-query dashboards would report an efficiency
   improvement. The discrepancy is the fan-out factor.
2. **About 60% of scanned rows never reach a user** — sampling, superseded
   corrections, and abandoned turns. Roughly 7% is outright abandonment, which is
   invisible to every accuracy metric.
3. **Plan-keyed caching closes ~41% of the residual misses** left by exact-text
   caching (93.4% vs 88.8% hit rate). The gap is the correction phase.
4. **No superlinearity.** Cost per answered question is flat in fleet size from 1 to
   32 agents (scaling exponent k = -0.026). A negative result: under this
   configuration, agent cost is additive rather than compounding. Given the in-process
   limitation above, this should be re-tested on a distributed engine before being
   generalised.

## Mitigation ablation

```bash
python run_mitigations.py --fleet 8 --turns 192 --rows 400000
```

Five interventions against the same dataset, fleet and seed. Independent RNG streams
per decision type mean changing one phase's parameters leaves the others' decisions
bit-identical — without that control, disabling discovery (which removes no scan work
at all) appeared to make the workload 6% more expensive.

| Intervention | Scan/question | vs. base | Fan-out | Answered | Space retained |
|---|---|---|---|---|---|
| None (baseline) | 997,219 | — | 4.01 | 165 | 100% |
| Pinned schema context | 997,219 | 0.0% | 4.01 | 165 | 100% |
| Cached column profiles | 639,514 | **−35.9%** | 1.68 | 165 | 100% |
| Tight correction budget | 1,031,613 | **+3.4%** | 4.28 | 149 | 100% |
| Semantic layer boundary | 665,695 | −33.2% | 1.67 | 164 | **15%** |
| Semantic layer + budget | 673,400 | −32.5% | 1.70 | 149 | 15% |

- **Cached profiles are the only near-free win**: −35.9% scan cost, same answers.
  Sampling, not analytical querying, is where the budget goes.
- **Pinned schema saves zero scan** but eliminates all 1,152 catalog requests. Right
  fix for a catalog-bound platform, irrelevant to a scan-bound one.
- **Capping retries backfires** (+3.4%): answers fall 165→149 while fan-out rises. A
  capped agent still pays for its failed attempt and loses the retry that would have
  redeemed it.
- **The semantic layer has a capability cliff**: 33.2% saving, 15% of questions still
  answerable. Argue it on governance grounds, not efficiency.
- **Cache hit rates flatter.** Removing sampling drops the exact-text rate from 89.2%
  to 70.0% — the healthy baseline number is an artifact of repetitive `LIMIT` queries.

## Layout

```
agentlake/
  phases.py    Phase enum and PhaseModel parameters
  dataset.py   Synthetic retail star schema
  engine.py    EngineAdapter interface, DuckDBAdapter, plan fingerprinting
  sqlgen.py    Question corpus, parameterised space, semantics-preserving mutator
  agent.py     SyntheticAgent: one turn, decomposed into phases
  cache.py     ExactTextCache and PlanCache models
  metrics.py   Aggregation, Gini skew, RunReport
  fleet.py     Fleet driver
  sweep.py     Fleet-size sweep and scaling exponent
  mitigations.py  Intervention ablation
run_sweep.py       CLI: fleet-size sweep
run_mitigations.py CLI: mitigation ablation
tests/         24 tests, including meaning-preservation of every mutation
```

To target another engine, implement `EngineAdapter`. The `RunReport` contract is
unchanged.
