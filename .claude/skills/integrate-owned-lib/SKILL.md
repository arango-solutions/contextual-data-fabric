---
name: integrate-owned-lib
description: Safely bump or integrate a change from an owned sibling library (arango-sparql-py, arango-query-core, r2g / r2g-arango, arango-schema-analyzer, arango-cypher-py, arango-entity-resolution) into a consumer — CC-9 pin discipline. Use when bumping a git-pinned dependency, wiring a teammate's new capability, or before building any integration on top of an owned lib. Its FIRST step is checking both org mirrors for in-flight work.
---

# Integrate an Owned Library (CC-9, mirror-aware)

Bump or build on an owned sibling lib **without duplicating in-flight work or fighting mirror
drift**. Written after a real miss: a pin was bumped + dense wiring re-implemented on a
148-commit-stale base, duplicating a teammate's already-pushed work, because the remotes weren't
checked first. This skill's whole point is the check that would have caught it.

## Invocation
`/integrate-owned-lib <lib>` — reconcile and integrate an owned lib safely.

## The topology you're working against
Owned libs live in **two GitHub orgs**: `ArthurKeen/*` (nominal upstream, dev-first) and
`arango-solutions/*` (the team mirror). They **drift, and the drift inverts** — a teammate may
develop many commits on the arango-solutions mirror while `ArthurKeen` falls behind. Slugs differ
across orgs (`arango-schema-mapper`↔`arango-schema-extractor`, `arango-cypher-py`↔`arango-cypher`,
`r2g-arango` both). See [[repo-mirror-topology]] and the `~/code/bin/arango-mirror` tool.

**Exception (2026-09-06): `contextual-data-fabric` itself inverted** — arango-solutions is its PRIMARY and ArthurKeen the synced secondary; PRs go to the org. The generic note above still holds for the owned libraries until each repo decides otherwise.

## Protocol

### Phase 0 — CHECK BOTH MIRRORS FIRST (do not skip)
Before writing any integration code or bumping any pin:
- `git ls-remote --heads` **both** org copies of the lib — compare `main` HEADs and enumerate
  branches (a teammate's work may be a feature branch, or — as seen — 148 commits ahead on `main`).
- For any divergence, `git log` the ahead side (author + date + subject). **If a teammate already
  did what you're about to do, STOP and adopt their line instead of duplicating it.**
- `arango-mirror status` gives the two-org HEAD comparison in one shot.

### Phase 1 — Establish the authoritative base
- Identify which org/commit is authoritative (usually the most-advanced `main`). Fetch it; base
  your work on THAT, not on a stale local checkout or an old pin.
- If the mirrors diverge, reconcile first (see Phase 4) so you're not building on a fork.

### Phase 2 — Bump the pin (deliberate — CC-9)
- Update the git pin to the authoritative commit (or a cut tag — cleaner for CC-9). Prefer a
  release tag over a bare SHA when the lib supports it.
- Reinstall in the consumer's venv; confirm the new symbol/API is importable.

### Phase 3 — Re-run the goldens (CC-9's teeth)
- A pin bump is not done until the consumer's goldens/gate re-run green. For the fabric that's
  `make gate` (+ the relevant unit goldens). A bump that changes behavior must show its effect on
  the numbers, not just install.

### Phase 4 — Reconcile mirrors (if you pushed / if they drifted)
- Sync with `arango-mirror sync` (FF-only; never force). 
- **Never force-push a protected `main`.** ArthurKeen mains are branch-protected — a force-push is
  rejected server-side. To rewind a mistaken commit there you need an admin to lift protection
  temporarily; a forward merge/revert otherwise leaves cruft. Decide with the owner.
- Release-gated repos (e.g. `arango-ontoextract`) reject non-tagged pushes to the org `main` —
  cut a release, don't push WIP.

## Acceptance
1. You confirmed no teammate already did this (Phase 0 evidence).
2. Pin points at the authoritative commit/tag; the new API imports.
3. Consumer goldens/gate green after the bump.
4. Mirrors in sync (`arango-mirror status`), or the divergence is explicitly owned.

## Gotchas
- **The stale-base trap.** `git ls-remote` before you start; a local checkout or a pyproject pin
  can be many commits behind a teammate's active line.
- **Redundant re-implementation.** If the lib's recent commits already add the capability
  (retriever mode, an extra, an adapter), wire to theirs — don't rebuild it on an old base.
- **Protected mains + inverted drift** make "just force-push it" impossible; plan the reconcile.

## References
- `~/code/bin/arango-mirror` (status/setup/sync), [[repo-mirror-topology]], [[snowflake-sprint-decisions]].
- CC-9 (pinning + re-run-goldens) in the PRD §10.
