# telemetry-rca-agentic-runtime

**Automated root-cause analysis (RCA) with deterministic rule validation for
self-healing observability** -- and, as this repo's centerpiece, a
**zero-copy, multi-tenant RCA data model spanning Azure and GCP**: an
agentic pipeline that correlates evidence across services and tenants
*without ever copying a tenant's telemetry out of their own cloud account*.

```
OTel Mock Log/Trace/Metric Generator -> RCA Agent Runtime -> Rule Execution Engine -> Audit Log Store
                                              |
                                   Zero-Copy Federation Layer
                          (query pushdown across per-tenant stores)
```

> **Status**: a complete, runnable, fully-tested local system (Python 3.11+,
> DuckDB, pytest, Docker). The cross-cloud zero-copy architecture is
> **documented in depth and proven as a local functional analog** -- it is
> not a live Azure/GCP deployment. See [Limitations](#honest-limitations).

---

## Table of contents

- [Core concept](#core-concept)
- [Architecture](#architecture)
- [Quickstart](#quickstart)
- [Demo script](#demo-script)
- [Zero-copy multi-tenant data model](#zero-copy-multi-tenant-data-model)
- [The agentic RCA loop](#the-agentic-rca-loop)
- [Rule execution engine](#rule-execution-engine)
- [LLM-jury evaluator](#llm-jury-evaluator)
- [Audit log](#audit-log)
- [Testing](#testing)
- [Deployment](#deployment)
- [Honest limitations](#honest-limitations)
- [Repository layout](#repository-layout)

---

## Core concept

Most RCA demos either (a) assume all telemetry already lives in one place,
or (b) quietly centralize it there. This repo instead treats **"where is
the data allowed to live and who is allowed to touch it"** as a first-class
design constraint, equal in importance to the RCA logic itself:

1. **`otel_mock/`** generates realistic, labeled synthetic OTel-like
   telemetry for several simulated tenants, each with their own small
   microservice topology, and can inject one of five labeled failure
   scenarios with a ground-truth root cause attached.
2. **`federation/`** -- the centerpiece -- stores each tenant's telemetry in
   its own isolated store and answers cross-tenant/cross-service questions
   by pushing queries down into those stores, never copying rows into a
   shared table.
3. **`agent/`** runs an agentic loop: triage an alert, hypothesize
   candidate root causes, gather evidence via the federation layer, and
   rank hypotheses by evidence strength.
4. **`rules/`** is a deterministic, YAML-defined rule engine that turns
   gathered evidence into an explainable, reproducible confidence score --
   a fast baseline signal that doesn't depend on any model call.
5. **`evaluator/`** is a small panel of independent "judges" that votes on
   whether the top hypothesis is actually well-supported, and combines that
   with the rule engine's confidence into a final decision.
6. **`audit/`** is an immutable, hash-chained log of every step above,
   recording *what was queried and decided* -- never raw tenant data --
   with a tamper-detection function.

## Architecture

```mermaid
flowchart TB
    subgraph CP["CONTROL PLANE (centrally operated)"]
        direction TB
        ALERT[/"Alert / Anomaly"/] --> AGENT["RCA Agent Runtime<br/>agent/"]
        AGENT -->|"1. hypothesize"| HYP["Candidate Hypotheses"]
        HYP -->|"2. gather evidence"| FED["Federation / Query-Pushdown Layer<br/>federation/"]
        FED -->|"evidence features"| RULES["Rule Execution Engine<br/>rules/ (YAML DSL)"]
        RULES -->|"deterministic confidence"| JURY["LLM-Jury Evaluator<br/>evaluator/"]
        JURY -->|"consensus"| DECIDE{{"Combine Decision"}}
        DECIDE --> ACTION["auto_remediate / alert / escalate"]
        AGENT -.->|"every step"| AUDIT[("Audit Log<br/>audit/ -- hash-chained")]
        RULES -.-> AUDIT
        JURY -.-> AUDIT
        DECIDE -.-> AUDIT
    end

    subgraph DP["DATA PLANE (each tenant's own storage)"]
        direction LR
        T1[("Tenant A store<br/>(own DuckDB file locally /<br/>own ADX-Synapse-Fabric or<br/>BigQuery-BigLake in production)")]
        T2[("Tenant B store")]
        T3[("Tenant C store")]
    end

    FED -. "read-only, scoped<br/>query pushdown" .-> T1
    FED -. "read-only, scoped<br/>query pushdown" .-> T2
    FED -. "read-only, scoped<br/>query pushdown" .-> T3

    style T1 fill:#eef,stroke:#446
    style T2 fill:#eef,stroke:#446
    style T3 fill:#eef,stroke:#446
    style AUDIT fill:#efe,stroke:#464
```

The control plane never holds a standing copy of tenant telemetry. Every
arrow crossing into the data plane is a scoped, read-only query; every
dotted arrow into the audit log is metadata only.

### Failure injection -> mock telemetry generation

```mermaid
sequenceDiagram
    participant Demo as scripts/demo.py
    participant Gen as otel_mock.TelemetryGenerator
    participant Store as federation.tenant_store

    Demo->>Gen: generate_with_scenario("memory_leak", start, end)
    Gen->>Gen: build baseline request trees for every service/timestamp
    Gen->>Gen: inject per-service error/latency/metric modifiers<br/>inside the scenario's sub-window
    Gen-->>Demo: logs, traces, metrics, GroundTruth(root_cause, affected_services, window)
    Demo->>Store: write_tenant_store(tenant_db_path, logs, traces, metrics)
    Note over Store: one isolated DuckDB file per tenant -- never shared
```

### The full agentic RCA loop

```mermaid
sequenceDiagram
    participant Alert as Alert
    participant Agent as RCAAgentRuntime
    participant Fed as FederationQueryEngine
    participant Rules as RuleEngine
    participant Jury as JuryPanel
    participant Audit as AuditLogStore

    Alert->>Agent: investigate(alert, scope)
    Agent->>Audit: append(ALERT_RECEIVED)
    Agent->>Agent: hypothesize candidates (topology metadata)
    Agent->>Audit: append(HYPOTHESIS) x N
    Agent->>Fed: query_logs/traces/metrics(scope, baseline window)
    Agent->>Fed: query_logs/traces/metrics(scope, incident window)
    Fed-->>Agent: DataFrames (tenant-scoped, column-redacted)
    Agent->>Audit: append(EVIDENCE_QUERY) x 6 (metadata only, no rows)
    loop for each hypothesis
        Agent->>Rules: evaluate(evidence_bundle)
        Rules-->>Agent: RuleVerdict (matched, confidence, signals)
        Agent->>Audit: append(RULE_VERDICT)
    end
    Agent->>Agent: rank hypotheses by rule confidence
    Agent->>Jury: convene(top_rule_verdict, top_bundle)
    Jury-->>Agent: JuryVerdict (consensus, dissent)
    Agent->>Audit: append(JURY_VERDICT)
    Agent->>Agent: combine_decision(rule_verdict, jury_verdict)
    Agent->>Audit: append(FINAL_DECISION)
    Agent-->>Alert: PipelineResult
```

### Zero-copy federated query flow

```mermaid
sequenceDiagram
    participant Caller as Federation caller<br/>(RCA Agent)
    participant Engine as FederationQueryEngine
    participant Scope as AccessScope
    participant Catalog as FederationCatalog<br/>(metadata only)
    participant TA as tenant_acme.duckdb
    participant TB as tenant_globex.duckdb
    participant TX as tenant_initech.duckdb<br/>(NOT in scope)

    Caller->>Engine: query_logs(scope={acme, globex}, ...)
    Engine->>Scope: enforce_tenant_scope(requested=None)
    Scope-->>Engine: ["acme", "globex"]
    Engine->>Catalog: get("acme"), get("globex")
    Catalog-->>Engine: db_path for each (pointers only)
    Engine->>TA: ATTACH 'acme.duckdb' READ_ONLY
    Engine->>TB: ATTACH 'globex.duckdb' READ_ONLY
    Note over Engine,TX: tenant_initech.duckdb is NEVER attached --<br/>structurally unreachable on this connection
    Engine->>TA: SELECT ... (pushed down)
    Engine->>TB: SELECT ... (pushed down)
    TA-->>Engine: rows (read-only)
    TB-->>Engine: rows (read-only)
    Engine-->>Caller: UNION ALL result DataFrame + QueryDescriptor (metadata)
```

## Quickstart

Requires Python 3.11+ (tested with 3.11-3.13).

```powershell
git clone https://github.com/sehgalnamit/telemetry-rca-agentic-runtime.git
cd telemetry-rca-agentic-runtime
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows PowerShell
# source .venv/bin/activate       # macOS/Linux

pip install -r requirements.txt
pytest -q                         # 53 tests, fully offline

python scripts/demo.py --all --cross-tenant-federation-demo
```

Expected tail of the demo output for one scenario:

```
Top hypothesis: cascading_timeout -> pricing (rule confidence=0.90)
Jury: consensus_supports=True, mean_confidence=0.93, dissent_notes=0
Final decision: auto_remediate -- rule engine and jury both support the
hypothesis with high confidence (rule=0.90, jury=0.93)

Audit log: 27 entries, chain_intact=True (chain intact)

Scoring against ground truth: CORRECT (expected cascading_timeout/pricing)
```

### Docker

```powershell
docker compose config     # validate the compose file
docker compose up --build # runs the demo across all scenarios, then exits
docker compose down
```

> Docker was not available in the environment this repo was built in, so
> `docker compose up`/`build` could not be exercised here. The
> `docker-compose.yml` was validated by parsing it as YAML; see
> [Honest limitations](#honest-limitations).

## Demo script

`scripts/demo.py` is the single entry point for reproducing an end-to-end
labeled-scenario run:

```powershell
# One specific scenario
python scripts/demo.py --scenario bad_deploy

# Every scenario in the catalog, plus the standalone cross-tenant
# federation demo (separate from any one alert's investigation)
python scripts/demo.py --all --cross-tenant-federation-demo
```

For each scenario it: generates labeled synthetic telemetry, writes it into
an isolated per-tenant DuckDB file, fires a synthetic alert partway through
the injected failure window, runs the full agentic pipeline, prints the
ranked hypotheses / jury verdict / final decision, verifies the audit hash
chain, and scores the result against the ground truth. Exit code is
non-zero if any scenario's top hypothesis doesn't match ground truth.

## Zero-copy multi-tenant data model

This is the centerpiece of the repo -- see
**[`docs/ZERO_COPY_DATA_MODEL.md`](docs/ZERO_COPY_DATA_MODEL.md)** for the
full write-up. Summary:

- **Local analog (implemented and tested)**: each simulated tenant's
  telemetry lives in its own DuckDB file. `federation/query_layer.py`
  answers cross-tenant queries by `ATTACH`-ing, **read-only**, only the
  tenant files an `AccessScope` permits, then pushing a `UNION ALL` query
  down into them. Out-of-scope tenant files are never attached at all --
  refusal is structural, not just a runtime check. A lightweight
  `FederationCatalog` holds only `tenant_id -> db file path` metadata,
  never rows.
- **Production mapping (documented, not deployed)**:
  - **Azure**: ADX cross-cluster/cross-database queries, Synapse
    serverless SQL external tables over each tenant's own ADLS Gen2 via
    managed identity, Microsoft Fabric OneLake shortcuts (zero-copy
    references), and **Azure Lighthouse** for cross-tenant delegated RBAC
    access without moving data.
  - **GCP**: **BigQuery Omni** (query in place across clouds/regions),
    **Analytics Hub** (data sharing without copying), **BigLake
    authorized views** over each tenant's own GCS/BigQuery data, and
    cross-project IAM-scoped access.
- This is **a documented architecture plus a local functional analog that
  proves the federation pattern**, not a live cross-cloud deployment -- no
  real Azure/GCP cross-tenant resources were provisioned.

## The agentic RCA loop

`agent/runtime.py`'s `RCAAgentRuntime.investigate(alert, scope)`:

1. **Triage**: derive a baseline window (an earlier, presumed-healthy
   period) and an incident window (around the alert) from the alert
   timestamp.
2. **Hypothesize**: `agent/hypothesis.py` proposes a bounded set of
   candidates from topology *metadata* (the alerting service itself, its
   direct dependencies, its direct external dependencies, and same-node
   unrelated peers) -- one hypothesis per plausible root-cause type.
3. **Gather evidence**: issues six scoped, read-only federated queries
   (logs/traces/metrics x baseline/incident) via `federation/`, never
   copying rows; builds a per-hypothesis `EvidenceBundle` of aggregated
   features (error rates, latency ratios, metric trends, log pattern
   matches, attribute changes).
4. **Rank hypotheses**: runs the deterministic rule engine over each
   bundle and sorts by resulting confidence.
5. **Jury gate + decide**: convenes the jury on the top hypothesis and
   combines rule + jury into `auto_remediate` / `alert` / `escalate`.

Every step is appended to the hash-chained audit log.

## Rule execution engine

`rules/definitions/default_rules.yaml` defines one deterministic signature
per failure type as a set of `SignalCondition`s (e.g. `latency_ratio_gte`,
`log_pattern`, `metric_trend_ratio_gte`, `attribute_changed`,
`peer_shared_node_degraded`), each targeting either the hypothesis's
primary candidate or any other service in the evidence bundle. A rule fires
when enough of its signals match (`min_match_ratio`), producing a fixed,
reproducible confidence -- no randomness, no model call, fully explainable
via `RuleVerdict.rationale` and the matched/unmatched signal lists.

Scored against the 5 labeled scenarios in `otel_mock/scenarios.py`, this
achieves perfect precision and recall (see
`tests/test_rules.py::test_rule_engine_precision_and_recall_across_all_scenarios`)
-- on this canonical, well-separated scenario set; it is not a claim about
production incident diversity.

## LLM-jury evaluator

This repo's validation gate for a ranked RCA hypothesis has two layers:
a fast, cheap, **confidence-gated** first pass using **TypeSafe Jev**, and
an **LLM-jury "appeals court"** that only gets invoked when Jev's
confidence is low/ambiguous or it disagrees with the deterministic rule
engine.

```mermaid
flowchart TB
    HYP["RCA Hypothesis<br/>(top-ranked by the rule engine)"] --> GATE{{"Jev Confidence-Gated<br/>Decision Gate"}}
    GATE -->|"high confidence<br/>+ agrees with rule engine"| AUTO["Auto accept<br/>(auto_remediate / alert)"]
    GATE -->|"low/ambiguous confidence,<br/>OR disagreement with<br/>the rule engine"| JURY["LLM-Jury<br/>'appeals court'"]
    JURY --> VERDICT["Final verdict +<br/>textual rationale"]
    VERDICT --> AUDIT[("Audit entry<br/>(path + rationale, metadata only)")]
    AUTO --> AUDIT
```

### TypeSafe Jev + Confidence-Gated Routing

[Jev](https://typesafe.ai/) ([docs](https://docs.typesafe.ai/introduction))
is a real, commercially-available "System One model" from TypeSafe for
fast, cheap, *calibrated* structured decisions: instead of free-text, you
ask it typed questions about some state and get back a typed answer with a
documented [confidence formula](https://docs.typesafe.ai/confidence):

- **Noul** (yes/no): `{"noul": p}` -- a single probability.
- **Choice** (pick one of an unordered set): `{"choice": id, "probabilities": {...}, "confidence": c}`,
  where `confidence = (n * p_max - 1) / (n - 1)` for `n` options.
- **Score** (pick a point on an ordered rubric): `{"score": s, "probabilities": {...}, "confidence": c}`,
  where `score = sum(k * p_k)` and confidence is derived from how
  concentrated the probability mass is around its mode versus a uniform
  baseline.

`evaluator/jev_gate.py` implements this exact contract, plus
`LocalJevStandIn`: a **deterministic, fully offline stand-in** built from
this repo's own rule-engine confidence scores (not a trained model) that
implements the identical contract and the identical confidence formulas,
so `evaluator/cascade.py`'s routing logic is fully testable without any
API key. Swapping in the real hosted model later is one line:
`get_default_jev_client()` returns `TypeSafeJevClient` automatically once
`TYPESAFE_API_KEY` is set (see `requirements-jev.txt` for its optional
`httpx` dependency).

`evaluator/cascade.py`'s `ConfidenceGatedCascade` asks Jev a Choice
question ("which ranked hypothesis is best supported?") and a Score
question ("how well-supported is the top one, on a rubric from unsupported
to fully supported?"), then applies TypeSafe's own documented
architectural pattern, **["Confidence-Gated Routing"](https://docs.typesafe.ai/patterns/confidence-routing)**:
confidence is a *second* decision axis, independent of the answer itself --
high confidence acts automatically, medium proceeds with caution, low
falls back to a different system. Concretely:

- **Jev agrees with the rule engine's top pick, at high confidence** ->
  the fast path: Jev alone gates the decision (`auto_remediate`/`alert`
  via the same `combine_decision` thresholds used elsewhere), the jury is
  never invoked.
- **Jev's confidence is low/ambiguous, or it disagrees with the rule
  engine** -> escalate to the existing jury as an "appeals court" for a
  nuanced, textual rationale and the final verdict.

Both paths, and the reason for the routing decision, are recorded in a
`JEV_VERDICT` audit entry (plus a `JURY_VERDICT` entry only when the jury
was actually invoked) -- see `tests/test_cascade.py` for routing tests
against both hand-constructed cases and all 5 labeled scenarios. Only
metadata (candidate labels + rule-engine confidence scores) is ever placed
in the `state` handed to Jev, never raw tenant telemetry rows.

**When to use which:**

| Situation | Recommended gate |
|---|---|
| Production self-healing monitoring, well-characterized failure signatures, latency/cost-sensitive | Jev only (confidence-gated, skip the jury when confident+agreeing) |
| Ambiguous, high-impact, or novel failure signatures; post-incident review | Jev + jury appeals court (always escalate, or lower the confidence threshold) |

### The jury panel itself

`evaluator/judges.py` implements a small panel of **independent,
deterministic judges** (CI-safe, no external calls) that each look at the
rule verdict + evidence from a different angle:

- `RuleAlignmentJudge` -- mirrors the rule engine's own verdict.
- `EvidenceCoverageJudge` -- looks only at the fraction of signals matched.
- `ConservativeSkepticJudge` -- a deliberately stricter bar (every signal,
  including optional ones, must match at high confidence); this is the
  judge most likely to dissent on borderline matches, by design.

`JuryPanel.convene()` aggregates votes into a majority consensus, mean
confidence, and **visible dissent notes** rather than a single smoothed
number. `evaluator/decision.py`'s `combine_decision()` then combines a
rule-engine verdict with *either* the jury's consensus *or* a Jev-derived
pseudo-consensus (same thresholds, one source of truth either way):

| Rule engine | Jury/Jev | Confidence | Action |
|---|---|---|---|
| matched | supports | both >= high threshold | `auto_remediate` |
| matched | supports | moderate | `alert` |
| disagree, or either confidence low | -- | -- | `escalate` |

**Honest grounding**: a diverse judge panel reduces the single-point-of-failure
bias/variance of asking one judge once, and a small panel of cheap,
deterministic heuristics -- or a single calibrated Jev call -- is
faster/cheaper than one large model call by construction. This repo does
not claim or fabricate any benchmark numbers about accuracy uplift; Jev's
own speed/cost multiplier claims are TypeSafe's self-reported figures on
their own workflows and are not independently verified here.
`evaluator/judges.py` documents an unimplemented `LLMJudge` extension
point for plugging in a real small/local model as an additional juror; it
is intentionally not wired in, to keep CI fully offline.

DeepEval's own Jev integration, ["Jev-as-a-judge"](https://deepeval.com/blog/introducing-jev-as-a-judge),
combines several typed questions into one weighted score,
`JevEval = sum(w_i * v_i) / sum(w_i)` -- cited here only as the inspiration
for *why* combining multiple typed questions into one signal is a
reasonable pattern; this repo does not reimplement JevEval itself.

## Audit log

`audit/log_store.py`'s `AuditLogStore` is an append-only, in-memory (and
optionally JSONL-exportable) log where every entry embeds the SHA-256 hash
of the previous entry:

```
entry_hash[i] = sha256(index, timestamp, entry_type, tenant_id, payload, prev_hash[i])
prev_hash[i+1] = entry_hash[i]
```

`verify_chain()` walks the whole chain, recomputing each hash and checking
each `prev_hash` link; any tampered payload or reordered entry is detected
and reported with the index where the break occurs. Every payload is
metadata only -- alert fields, hypothesis descriptions, `QueryDescriptor`s
(table/tenants/columns/row-count, never raw rows), rule/jury/Jev verdicts
(including which cascade path was taken and why), and final decisions --
preserving zero-copy even inside the audit trail itself.

## Testing

```powershell
pytest -q                               # all 80 tests
pytest -q --cov=. --cov-report=term-missing   # with coverage (97% at last run)
```

| Module | What's covered |
|---|---|
| `tests/test_otel_mock.py` | Generator determinism (same seed -> identical output), ground-truth labeling correctness for all 5 scenarios, external-dependency-emits-no-telemetry invariant |
| `tests/test_federation.py` | Cross-tenant federated query correctness, structural out-of-scope refusal, column redaction, metadata-only `QueryDescriptor` shape |
| `tests/test_rules.py` | Per-scenario top-verdict correctness + precision/recall across the labeled scenario catalog |
| `tests/test_jury.py` | Consensus/dissent aggregation, rule+jury decision-combination policy (auto_remediate/alert/escalate) |
| `tests/test_jev_gate.py` | Jev confidence formulas against the documented spec (Choice, Score, boundary cases), `LocalJevStandIn` determinism and behavior |
| `tests/test_cascade.py` | Cascade routing (high-confidence+agreement -> Jev-only; low-confidence or disagreement -> jury), the candidate-label-collision regression, full pipeline across all 5 scenarios, audit-trail path recording |
| `tests/test_audit.py` | Hash-chain integrity, tamper detection (payload edit + reordering), JSONL export |
| `tests/test_e2e.py` | Full pipeline per scenario: correct root cause, complete/intact audit trail, no raw telemetry text leaking into audit payloads, end-to-end tamper detection |

Everything runs fully offline with no external API keys.

## Deployment

- **[`docs/AZURE_DEPLOYMENT.md`](docs/AZURE_DEPLOYMENT.md)** +
  **[`deployment/azure/`](deployment/azure/)**: Bicep templates for the
  control plane (Container Apps, Container Registry, Key Vault, Log
  Analytics + Application Insights) and a standalone Azure Lighthouse
  delegation template. **Illustrative only -- not applied to any live
  subscription.**
- **[`docs/GCP_DEPLOYMENT.md`](docs/GCP_DEPLOYMENT.md)** +
  **[`deployment/gcp/`](deployment/gcp/)**: Terraform for the control
  plane (Cloud Run v2, Artifact Registry, Secret Manager, Monitoring/Trace
  IAM) and an illustrative cross-project BigQuery IAM binding.
  **Illustrative only -- not applied to any live project.**
- **[`docs/ZERO_COPY_DATA_MODEL.md`](docs/ZERO_COPY_DATA_MODEL.md)**: the
  full architecture deep-dive tying both clouds' data-plane primitives back
  to the local `federation/` implementation.

## Honest limitations

- **No real cross-cloud deployment.** `deployment/azure/` and
  `deployment/gcp/` are reviewable, syntactically-checked IaC templates.
  They were not `terraform apply`'d or `az deployment group create`'d
  against a live subscription/project in this exercise.
- **Local analog, not a live federation.** The zero-copy query pattern is
  fully implemented and tested with DuckDB `ATTACH` on one machine. It
  proves the pattern (metadata catalog, scoped read-only attach, pushdown
  query, structural isolation) works and is testable -- it does not itself
  span clouds. DuckDB cannot attach to an ADX cluster or a BigQuery
  dataset.
- **Docker was not available in this build environment.** `docker compose
  up --build` could not be exercised here; the compose file was validated
  by parsing it as YAML, and the equivalent flow (`scripts/demo.py --all`)
  was run directly and verified.
- **Synthetic, labeled scenarios only.** The rule engine's perfect
  precision/recall is measured against this repo's own 5 canonical,
  well-separated injected scenarios -- it is not a claim about production
  incident diversity or ambiguity.
- **Jury judges are deterministic stubs.** No real LLM call is wired in by
  default (by design, for offline CI); `evaluator/judges.py` documents how
  to add one.
- **TypeSafe Jev's real hosted model was not exercised.** `TYPESAFE_API_KEY`
  was not available in this build environment, so only `LocalJevStandIn`
  (the deterministic, offline, contract-compatible stand-in) was run and
  tested. `TypeSafeJevClient` is implemented against the documented REST
  API (verified against docs.typesafe.ai at the time of writing) but its
  network path is unexercised; switching to the real model is a one-line
  change (`get_default_jev_client()`) once an API key is available.
  TypeSafe's own speed/cost multiplier claims are self-reported on their
  own workflows and are repeated here only with that explicit caveat.

## Repository layout

```
otel_mock/      synthetic OTel-like log/trace/metric generator + labeled failure scenarios
federation/     zero-copy, multi-tenant federated query layer (the centerpiece)
agent/          agentic RCA loop: triage, hypothesize, gather evidence, rank
rules/          deterministic YAML rule DSL + evaluation engine
evaluator/      TypeSafe Jev confidence-gated cascade + LLM-jury panel + decision combination
audit/          hash-chained, append-only audit log
common/         shared, generic call-graph helpers
tests/          pytest suite (80 tests, fully offline)
scripts/        demo.py -- CLI entry point for the end-to-end demo
deployment/     Azure (Bicep) + GCP (Terraform) illustrative IaC templates
docs/           ZERO_COPY_DATA_MODEL.md, AZURE_DEPLOYMENT.md, GCP_DEPLOYMENT.md
.github/workflows/ci.yml   lint (ruff) + pytest on every push/PR
Dockerfile, docker-compose.yml   local demo container
requirements.txt            core, offline-safe dependencies
requirements-jev.txt        optional deps for the real TypeSafe Jev adapter (httpx, typesafe-sdk)
```

## License

[MIT](LICENSE)
