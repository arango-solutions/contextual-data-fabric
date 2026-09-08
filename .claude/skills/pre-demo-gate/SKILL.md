---
name: pre-demo-gate
description: Run the golden gate before any demo of the Contextual Data Fabric — PJ's runbook rule, mechanized. Use before showing the browser demo, recording a walkthrough, or presenting the federation live. A flaky demo is a named failure mode (M9 NFR); this is the mandatory pre-flight.
---

# Pre-Demo Gate

**PJ's runbook rule: run the eval gate before any demo, no exceptions.** A demo that hasn't
just passed `make gate` is not cleared to run.

## Invocation
`/pre-demo-gate` — verify the federation is green before demoing.

## Protocol
1. **Stacks up?** `docker ps` for the CDF containers (arango, ontop, postgres; clickhouse if in
   play). If any are down, `make up` (and `make seed` if data isn't loaded). Snowflake is a cloud
   service — confirm `.env` has the `SNOWFLAKE_*` creds so the gate wires that leg.
2. **Run the gate:** `make gate`. It sources `.env` (Snowflake creds), wires
   `FederationService.from_env`, and runs every `deploy/golden/*.json` live.
3. **Read the result:**
   - **All green (N/N)** → cleared to demo. Note which golden is the centerpiece (g5 = the
     three-source `Account⋈UsageMetric⋈Document` join).
   - **Any red** → **do NOT demo.** Report the failing golden + its mismatch. Common causes: a
     stack down, stale seed, a source's creds missing, or a mapping/concept-ownership regression.
4. **Rehearse the failure act (optional but recommended):** the "kill a leg mid-demo → declared
   partial envelope" story (CC-5) is part of the scripted arc — confirm it still declares, never
   silently drops.

## Acceptance
- `make gate` exits 0 with every case green, moments before the demo.

## Gotchas
- The gate pins the NL front-end OFF (`CDF_NL_DISABLED`) for determinism — golden outcomes can't
  drift with a model. Don't "fix" a red by enabling NL.
- If a golden touches a concept owned by a live-only source (e.g. `UsageMetric`→Snowflake), the
  gate needs that source reachable; an offline demo can only cover the Postgres+Arango core.

## References
- `deploy/demo/gate.py`, `Makefile` (`gate`/`demo`/`up`/`seed`), `docs/archive/p1-closeout-plan.md (archived)`.
