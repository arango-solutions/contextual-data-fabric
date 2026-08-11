---
title: "Contextual Data Fabric — Project Scorecard"
type:
  - internal
  - scorecard
status: current
version: 1.2
date: 2026-08-06
requirements: "docs/contextual-data-fabric-prd.md §10.1"
related:
  - "docs/architecture/project-sota-scorecard.md"
---

# Contextual Data Fabric — Project Scorecard

This scorecard records evidence, not an invented maturity percentage. The
implementation score is the share of required, executable gates that passed.
Deployment integrations that cannot be proved in this checkout are listed
separately and are not silently counted as green.

Competitor-relative leadership is scored separately in the
[[contextual-data-fabric/docs/architecture/project-sota-scorecard|SOTA
scorecard]]; implementation completeness is not itself evidence of SOTA.

## Result

- **Recommended sequence:** 4/4 increments implemented.
- **Implementation work packages:** 20/20 implemented, including the conditional
  RSA → CSI adapter.
- **Required executable gates:** 7/7 passed (**100% implementation gate score**).
- **Production deployment certification:** not claimed; external identity,
  policy, delegation, and released-AER evidence remains.

## Recomputed evidence — 2026-08-06

1. **Live end-to-end contracts — PASS (15/15 hosted; 10/15 merge-blocking).**
   The full gate passed all Postgres/Ontop, Snowflake, ClickHouse, and ArangoDB
   cases, including the five-question arc, empty answer, PII refusal, and prompt
   injection. Two live cases assert exact bindings; the remaining 13 assert
   routing, reconciliation, grounding, or refusal contracts.
   [Hosted run 31090989998](https://github.com/ArthurKeen/contextual-data-fabric/actions/runs/31090989998)
   used Snowflake RSA key-pair authentication and passed all three jobs on
   `feat/hosted-sota-evidence`. Merge-blocking `live-local` runs the 10 cases
   that do not require Snowflake. A post-merge full run on `main` remains.
2. **Unit and contract suite — PASS (322 passed, 5 skipped).**
   `make test` passed the full test suite; skips are environment-gated live
   adapter tests, not failures.
3. **Static quality — PASS.**
   Ruff reported no findings and mypy reported no issues across 55 source files.
4. **NL decomposition — PASS (10/10).**
   Corpus v1.0.0 passed parse validity, partition validity, expected sources,
   join keys, refusal behavior, and ingress-path checks.
5. **Canonical resolution safety — PASS.**
   Resolution corpus v1 passed with precision **1.0**, recall **0.667**,
   abstention rate **0.375**, zero cross-scope violations, and evidence
   completeness **1.0**. Recall is reported as an improvement metric; the
   precision-first safety gate requires perfect precision, scope, and evidence.
6. **Authorization — PASS (30/30).**
   The authorization golden covers allow/rewrite/deny, governed seeds and rows,
   masking, citations, introspection, and fail-closed OpenFGA behavior.
7. **Catalog integrity — PASS.**
   The checked-in M11 manifest exactly rebuilds and validates from the current
   CSI/R2RML inputs.

The implementation score is:

`passed required gates / required executable gates = 7 / 7 = 100%`

## Sequence completion

### 1 — Measure and expose

Versioned NL corpus and evaluator, deterministic routing with governed LLM
fallback, per-plan/per-leg telemetry, semantic MCP, and Snowflake key-pair
configuration are implemented.

### 2 — Optimize and assemble

CSI statistics, deterministic cost-based stages, admission control, bounded
seed batching/refusal, and explicit job-scoped assembled execution are
implemented.

### 3 — Resolve and rotate

SecretResolver backends, generation-aware executor rotation, central redaction,
strict catalog-source startup validation with degraded health reporting, the
guarded canonical-hub contract, and the precision evaluation gate are
implemented.

### 4 — Govern runtime execution

Runtime canonical normalization, the authoritative M11 manifest, immutable
request identity, OIDC verification, delegated execution contracts, and
preflight/execution/postflight OBAC enforcement are implemented.

## Evidence still required before a production-readiness claim

- Cut a clean AER release, reconcile mirrors, pin it in CDF, and rerun CC-9
  goldens.
- Provision and exercise production OpenFGA, OIDC/IdP, and STS/delegation
  services; current tests use deterministic transports and contracts.
- Validate source-native RLS/masking and real Snowflake/Postgres delegated
  identities.
- Improve the public CK25 live-provider result from the measured 17/147 (11.6%)
  without tuning on the 49 scored answers; retain the 10-case deterministic
  decomposition/refusal gate separately.
- Make the four-engine hosted gate merge-blocking once Snowflake cost and
  availability controls are acceptable for every pull request.

These are deployment and portfolio-scale evidence gaps, not missing code paths
from the requested sequence.

## Recompute

```bash
make gate
make test
.venv/bin/python -m cdf.eval.ck25_eval \
  --validate-evidence docs/evidence/ck25-gpt-4o-mini-3x.json
.venv/bin/python -m cdf.eval.nl_eval --json
.venv/bin/python -m cdf.eval.resolution_eval --json
make sota-baseline-live
```
