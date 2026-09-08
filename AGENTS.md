# AGENTS.md — contextual-data-fabric

Canonical instructions for AI coding agents. `CLAUDE.md` is a symlink to this file.

## Identity
- PROJECT_ID: contextual-data-fabric
- PROJECT_TYPE: service
- PRD_FILE: docs/contextual-data-fabric-prd.md
- TECH_STACK: Python, FastAPI (POST /federate seam), SPARQL federation over ArangoDB (arango-sparql-py), Postgres (Ontop), and native Snowflake + ClickHouse executors; r2g-emitted CSI/R2RML mappings

## Repo topology (decided 2026-09-06)
- **PRIMARY: `arango-solutions/contextual-data-fabric`** — pull from it first;
  **every PR is opened and reviewed there** (PJ handles reviews on the org).
- **SECONDARY: `ArthurKeen/contextual-data-fabric`** — a synced mirror kept
  for continuity/CI history. Never open PRs there; never let it lead.
- The local `origin` fetches from the org and pushes to BOTH (org first).
  After a PR merges on the org, run `make sync-secondary` so the mirror
  follows. If the two mains ever disagree, arango-solutions wins.

## PRD location
The PRD is at `docs/contextual-data-fabric-prd.md`. It is the source of truth for
what this system must do; all implementation should be traceable to a requirement
in it (the code already cites `FR-*` / `NFR` / `M*` markers from this file).

`docs/contextual-data-fabric-product-strategy-prd.md` is a later product-framing draft
(`status: draft`) layered on top and referencing this PRD — it is **not** the
requirements spec, so do not point `/prd-sync` at it.
