# Catalog label integrity — allowlist & rename plan

`make catalog-integrity` runs `cdf-catalog validate`, which reports three
cross-source label smells found only after the four CSIs merge under one
manifest (see `src/cdf/catalog/collisions.py`):

- **collisions** — one human label carried by more than one entity.
- **synonyms** — several labels for one quantity.
- **hubs** — a join key most entities carry.

These are **warnings, not failures** by default. A collision is cleared one of
two ways: **rename** it (when the label genuinely means two different things),
or **accept** it on the allowlist (when two entities legitimately share one
quantity). Once every collision is either renamed away or allowlisted, the gate
(`--fail-on-label-collisions`) can go on in CI and only *new, unintended*
collisions will fail the build.

## Current collisions (5)

| label | entities | disposition |
| --- | --- | --- |
| `role` | Document (arango), Contact (postgres) | **rename** → `contactRole` (real clash) |
| `event date` | Document (arango), QueryEvent (clickhouse) | **rename** → `occurredAt` (real clash) |
| `contract id` | Contract, Opportunity (postgres) | **accept** — PK vs FK by design |
| `product scope` | Contract, Opportunity (postgres) | **accept** — same quantity by design |
| `renewal date` | Contract, Opportunity (postgres) | **accept** — same quantity by design |

## Allowlist — intentional collisions

`label-collisions-allow.json` (auto-discovered beside `manifest.json`) lists
collisions a curator has intentionally allowed, each with a reason:

```json
{ "allowed": [ { "label": "contract id", "reason": "PK on Contract, FK on Opportunity — one shared key by design" } ] }
```

Allowlisted collisions are still reported (tagged `[accepted — <reason>]`) so
the decision stays visible, but they do not trip `--fail-on-label-collisions`.
The three Contract/Opportunity collisions above are same-source and describe one
real shared quantity (the opportunity → contract carryover), so they are
allowlisted rather than renamed. To accept a new one, add an entry with a
reason; override the file location with `--allow-collisions PATH`.

## Rename plan — the two real cross-source clashes

`role` and `event date` each name **two different things** across two extractors,
so they are renamed, not accepted. The rename lives in the **producer config**,
not in the generated CSI: r2g maps `field_mappings: {source_column: target_property}`
and drives **both** the CSI property (`csi.py`) and the R2RML predicate
(`r2rml.py`) from the same target value, so one entry moves the label and the
grounding predicate in lockstep while the physical column is unchanged. Editing
the generated CSI by hand would desync it from R2RML and be reverted on the next
regeneration — so the override goes upstream.

| collision | rename | where the override lives | status |
| --- | --- | --- | --- |
| `role` (Contact side) | `role` → `contactRole` | `deploy/mappings/mapping.yaml` → `contacts.field_mappings` | **staged** (this repo) |
| `event date` (QueryEvent side) | `event_date` → `occurredAt` | ClickHouse r2g config `field_mappings` | **pending** — no committed ClickHouse mapping.yaml yet |

Only the r2g side of each pair is renamed; that is enough to clear the collision
(the arango `Document.role` / `Document.eventDate` become unique) and avoids
touching the arango reverse-CSI.

**Applying the renames** (needs r2g installed + the source live — not runnable in
CI): regenerate the affected forward CSIs, then rebuild the hash-pinned manifest:

```bash
# 1. regenerate the postgres + clickhouse CSIs with r2g (picks up field_mappings)
# 2. rebuild the manifest so its content hashes match the new CSIs + R2RML
make catalog-integrity   # or: python -m cdf.catalog.cli build --root . --output deploy/catalog/manifest.json
```

Until then the two collisions remain flagged (correctly — they are not yet
cleared). When they are, only the three allowlisted collisions remain, and CI can
turn the gate on:

```bash
python -m cdf.catalog.cli validate --root . deploy/catalog/manifest.json --fail-on-label-collisions
```
