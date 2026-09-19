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

- **In-process engine.** The concurrent driver gives a real shared buffer pool, worker pool
  and contention, which is what the latency result rests on. It does **not** give network
  shuffle, a distributed catalog under load, or cross-node scheduling. The latency exponent
  establishes that contention appears once a shared pool exists; it does not predict the
  magnitude on Trino or Spark.
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
4. **Additive in work, superlinear in latency.** Rows scanned per answered question is flat
   in fleet size from 1 to 32 agents (k = −0.023) under both the serial and the concurrent
   driver. Mean latency is not: under real concurrent execution it scales as fleet^0.76 and
   throughput saturates at four agents. See the concurrency sweep above.

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

## Concurrency sweep

```bash
python run_concurrency.py --sizes 1,2,4,8,16,32 --turns 192 --threads 4 --memory 512MB
```

The serial driver measures the work a fleet *creates*. This one measures the contention it
*causes*: agents run through a thread pool against a shared database with a bounded budget,
one cursor per thread, so queries genuinely execute in parallel and compete for the buffer
pool, memory limit and worker threads.

| fleet | scan/question | mean latency | p95 | throughput |
|---|---|---|---|---|
| 1 | 998,791 | 3.45 ms | 6.73 ms | 172 q/s |
| 2 | 925,818 | 3.72 ms | 7.48 ms | 314 q/s |
| 4 | 869,869 | 5.84 ms | 12.41 ms | **364 q/s** |
| 8 | 909,145 | 12.32 ms | 21.81 ms | 309 q/s |
| 16 | 928,427 | 22.61 ms | 39.71 ms | 299 q/s |
| 32 | 885,560 | 39.45 ms | 67.56 ms | 326 q/s |

Scaling exponents: **scan/question k = −0.023** (flat), **mean latency k = +0.756**,
**p95 latency k = +0.705**.

The workload is **additive in work but superlinear in latency**. Throughput saturates at
four concurrent agents; beyond that a fleet adds no work and no throughput, only queue.
Cost-based monitoring sees linear growth and reports nothing wrong while the agents become
progressively less usable.

Still in-process: real shared buffer pool and worker pool, but no network shuffle, no
distributed catalog under load, no cross-node scheduling.

## Reproducing the related-work evidence

Section 2.3 of the paper claims the field's own survey does not discuss platform-side
concerns. That is a quantitative claim, so it is auditable:

```bash
pip install pypdf
git clone --depth 1 https://github.com/HKUSTDial/awesome-data-agents
python analyse_survey_coverage.py awesome-data-agents/reports/Data_Agents_Survey.pdf
python analyse_survey_coverage.py .../Data_Agents_Survey.pdf --context catalog
```

Across 28,688 words: throughput 0, queue 0, contention 0, concurrent execution 0, result
cache 0, scan 0, fan-out 0, retry 0, abandon 0, admission control 0, lakehouse 0, Iceberg 0,
catalog 1 — against LLM 178, tool 61, planning 24, memory 15, benchmark 12, accuracy 8. Use
`--context` to inspect any non-zero count before citing it; a substring match is not a
discussion of the topic.

## Paper consistency check

```bash
python check_paper.py ../papers/multi-agent-lakehouse/PAPER.md
```

Verifies section numbering, citation closure (every reference cited, every citation
defined), cross-reference validity, abstract length, absence of drafting markers, and every
headline figure against the JSON in `results/`. Run it before any submission — it fails if a
number in the paper has drifted from what the harness produced.

## PDF export

```bash
pip install markdown weasyprint
python export_pdf.py ../papers/multi-agent-lakehouse/PAPER.md -o paper.pdf
```

```bash
python export_pdf.py ../papers/multi-agent-lakehouse/PAPER.md -o paper-2col.pdf --columns 2
```

Two renderings from one source. `--columns 1` (default) is the single-column reading copy:
Bitstream Charter on US Letter, justified with hyphenation, numeric table columns
right-aligned, running page numbers — 19 pages. `--columns 2` approximates a conference
two-column geometry — 12 pages.

Wide tables are detected (five or more columns, or any cell over 60 characters) and lifted
out of the column flow so they run the full measure, as LaTeX's `table*` does. This is done
structurally rather than with `column-span: all`, which WeasyPrint does not implement — set
on a table inside a multi-column container it is silently ignored and the table is squeezed
to column width instead.

**Neither output is a submission artifact.** A camera-ready paper needs the venue's own
template (PVLDB ships LaTeX and Word styles); the two-column mode only shows how the
content behaves at that measure. Its page count is indicative, not a conformance check.

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
  concurrent.py   Thread-pool driver, per-thread cursors, latency metrics
run_sweep.py        CLI: fleet-size sweep (serial)
run_mitigations.py  CLI: mitigation ablation
run_concurrency.py  CLI: fleet-size sweep under real concurrency
analyse_survey_coverage.py  Recomputes the Section 2.3 term-frequency evidence
check_paper.py              Consistency check: paper vs harness results
export_pdf.py               Renders PAPER.md to a typeset PDF
tests/         27 tests, including meaning-preservation of every mutation
```

To target another engine, implement `EngineAdapter`. The `RunReport` contract is
unchanged.
