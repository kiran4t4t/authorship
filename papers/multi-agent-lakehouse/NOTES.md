# Paper 1 — Working Notes

**Working title:** *The Noisy Neighbour Is a Robot: Characterizing Multi-Agent Workloads on the
Open Lakehouse*

**Primary target:** VLDB 2027 Industrial Track — deadline **2 Mar 2027** (verify).
Requires at least one author with industry affiliation. You qualify.

**Fallback targets, in order:**
1. **IEEE Computer** — "Future of Software Engineering in an AI-Native World" SI, deadline 1 Nov *or*
   1 Dec 2026 (conflicting; verify). Takes experience reports. Would need reframing toward
   engineering practice, and the timeline is tight.
2. **MIS Quarterly Executive** — agentic AI SI, 1 Mar 2027. Same underlying material, reframed for a
   senior-management audience: cost governance and organizational control rather than systems.
3. **IEEE Software / IT Professional** — rolling, no deadline. The safety net.

**Criterion tag:** Authorship. If accepted at VLDB, the conference talk also supports Speaking.

---

## Why this thesis and not the obvious one

The obvious paper — "LLM-generated SQL workloads differ from human BI workloads" — is **already taken**.
Verified prior art as of this sweep:

- **SQLStorm: Taking Database Benchmarking into the LLM Era** (Schmidt et al., PVLDB vol. 18, p. 4144).
  Establishes that LLM-generated query workloads cover a wider feature range than TPC-H/TPC-DS,
  producing >10,000 unique query plans and execution traces.
- **Both Ends Count! Just How Good are LLM Agents at Text-to-"Big SQL"?** (arXiv 2602.21480, also
  ACM DL 10.1145/3805621.3807640, published 13 Apr 2026). Introduces text-to-Big SQL measures
  capturing the interaction between agents, LLMs, Big Data engines, and data scale. Notes that
  semantically equivalent SQL forms produce materially different physical plans, shuffle volumes,
  and scan efficiency.
- **AgenticDataBench** (arXiv 2607.01647) and **FDABench** (arXiv 2509.02473). Benchmarks for data
  agents over analytical and heterogeneous-source queries.

Submitting the obvious paper into that field is a desk reject. Worse for your purposes: a paper that
restates known results is weak evidence of original contribution even if it gets in somewhere.

**What is still open** — and is squarely your expertise:

Every result above evaluates **one agent answering one question correctly**. None of them
characterizes **N agents sharing one platform**. That is a systems and platform-economics problem,
not a text-to-SQL problem, and it is where your OLAP/OLTP/NoSQL/Spark/lakehouse background is the
qualification rather than a nice-to-have.

Three specific gaps:

1. **Concurrency and contention.** Agent workloads are bursty, retry-heavy, and exploratory. A single
   user question fans out into schema discovery, sampling, several candidate queries, and
   self-correction retries. Multiply by a fleet. Nobody has published what this does to scan
   amplification, result-cache hit rates, catalog metadata hot-spotting, or small-file pressure on
   Iceberg manifests.
2. **Governance under non-deterministic consumers.** Row- and column-level security was designed for
   consumers whose access pattern is auditable and stable. An agent's access pattern is generated at
   inference time and is influenced by its input — which means prompt injection becomes a data-access
   control problem, not just a model-safety problem. The semantic layer / MCP boundary is emerging as
   the de facto enforcement point, but as an industry practice rather than a studied one.
3. **Colocated agent state.** Agents write as well as read: memory, checkpoints, intermediate results,
   traces. That is an OLTP-shaped write workload landing next to an OLAP-shaped read workload on
   shared storage. The OLTP/OLAP/NoSQL convergence question, with a new forcing function.

Gap 1 is the paper's spine. Gaps 2 and 3 are the discussion and the open-problems section.

---

## Honest assessment of feasibility

**This paper needs real measurements.** VLDB's Industrial Track exists for production experience;
reviewers will look for a deployed system and numbers from it. I cannot supply those, and inventing
them would be fatal — to the paper and to the filing it is meant to support.

Three routes, in descending order of evidence strength:

- **Route A — production telemetry (strongest).** You have query logs from a real deployment where
  agents hit the platform. Anonymize, aggregate, clear it with your employer. This is the paper VLDB
  wants.
- **Route B — reproducible harness (viable, and independently useful).** Build a public, open-source
  benchmark harness that drives a configurable fleet of agents against a standard lakehouse
  (Iceberg on object storage, Spark or Trino or DuckDB) and instruments the platform side rather than
  the answer-accuracy side. Release it. The artifact itself becomes original-contribution evidence
  separate from the paper, and it sidesteps every disclosure problem.
- **Route C — position paper (weakest as evidence).** No measurements, argument only. Retarget to
  IEEE Computer or IEEE Software. Publishable, but thin as an "original contribution" exhibit.

**My recommendation: Route B, with Route A layered on top if your employer clears it.** Route B is
fully within your control, produces a citable artifact, and is the difference between a paper that
argues and a paper that shows.

**Decision needed from you:** which route. It changes Section 5 and 6 substantially. Everything
already drafted (Sections 1–4) holds under all three.

---

## Related work — verified to exist, NOT yet read

Every item below surfaced in live search and is real. **I could not fetch or read any of them** —
arxiv.org, dl.acm.org, vldb.org and the publisher domains are all blocked by this environment's
egress policy. Read each one before citing it. Do not let any characterization below stand in a
submitted paper without checking it against the actual text.

### Directly competing / adjacent
- Schmidt et al. *SQLStorm: Taking Database Benchmarking into the LLM Era.* PVLDB vol. 18, p. 4144.
- *Both Ends Count! Just How Good are LLM Agents at Text-to-"Big SQL"?* arXiv:2602.21480 / ACM DL 10.1145/3805621.3807640.
- *AgenticDataBench: A Comprehensive Benchmark for Data Agents.* arXiv:2607.01647.
- *FDABench: A Benchmark for Data Agents on Analytical Queries over Heterogeneous Data.* arXiv:2509.02473.
- *Agent Bain vs. Agent McKinsey: A New Text-to-SQL Benchmark for the Business Domain.* arXiv:2510.07309.
- *ReViSQL: Achieving Human-Level Text-to-SQL.* arXiv:2603.20004.
- *AgentNLQ: A General-Purpose Agent for Natural Language to SQL.* arXiv:2605.19010.
- *MARS-SQL: A multi-agent reinforcement learning framework for Text-to-SQL.* arXiv:2511.01008.
- *DS-STAR: Data Science Agent for Solving Diverse Tasks across Heterogeneous Formats.* arXiv:2509.21825.

### Lakehouse architecture and field reality
- *Beyond the Data Mesh Illusion: Designing Modern AI-augmented Lakehouses.* arXiv:2605.27131.
- *What Went Wrong with Data Lakes? A 15-Year Reality Check from the Field.* arXiv:2606.08266.
- *Enterprise Data Science Platform: A Unified Architecture for Federated Data Access.* arXiv:2512.03401.
- *Democratizing cloud data lake analytics: natural language access to Apache Iceberg via LLM agents.*
  Frontiers in Big Data, doi:10.3389/fdata.2026.1785710.

### Semantics, context and schema
- *Towards Agentic Schema Refinement.* arXiv:2412.07786.
- *Arming Data Agents with Tribal Knowledge.* arXiv:2602.13521.

### Agentic systems and serving
- *HexAGenT: Efficient Agentic LLM Serving via Workflow- and Heterogeneity-Aware Scheduling.* arXiv:2605.16637.
- *Cloud-native and Distributed Systems for Efficient and Scalable LLMs — A Research Agenda.* arXiv:2604.17227.
- *Trade-offs in Decentralized Agentic AI Discovery Across the Compute Continuum.* arXiv:2605.11839.
- *A Survey of LLM-Driven AI Agent Communication: Protocols, Security Risks, and Defense Countermeasures.* arXiv:2506.19676.
- *Small Language Models are the Future of Agentic AI.* arXiv:2506.02153.

### Industry context (background reading, not citable as peer-reviewed work)
- Databricks: Iceberg v3 GA on Unity Catalog; Iceberg v4 adaptive metadata tree proposal and the
  suggestion that Delta 5.0 adopt the same structure.
- Snowflake: Iceberg v3 GA including deletion vectors, row lineage, VARIANT, default values.
- AWS S3 Tables: Iceberg REST Catalog API, Iceberg v3 support, claimed up to 10x higher TPS than
  Iceberg tables in general-purpose buckets.
- Apache Polaris as the vendor-neutral REST catalog implementation.
- Dremio: "Agentic Lakehouse Architecture: The Four Technical Layers."
- Cube: "Semantic Layer for AI Agents (2026)." dbt: "Semantic Layer vs. Text-to-SQL: 2026 Benchmark Update."

**Verify the Iceberg v3/v4 and vendor claims before they go in.** They came from vendor blogs and
newsletters, they are competitive marketing as much as engineering, and they move fast.
