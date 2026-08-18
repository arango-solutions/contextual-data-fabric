# Catalog label integrity — allowlist & renames

`make catalog-integrity` runs `cdf-catalog validate --fail-on-label-collisions`,
which reports (and now gates on) three cross-source label smells found only after
the four CSIs merge under one manifest (see `src/cdf/catalog/collisions.py`):

- **collisions** — one human label carried by more than one entity.
- **synonyms** — several labels for one quantity.
- **hubs** — a join key most entities carry.

A collision is cleared one of two ways: **rename** it (when the label genuinely
means two different things) or **accept** it on the allowlist (when two entities
legitimately share one quantity). The catalog is now clean apart from three
allowlisted collisions, so the gate is **on**: only *new, unintended* collisions
fail the build.

## Collisions today (3, all allowlisted)

| label | entities | disposition |
| --- | --- | --- |
| `contract id` | Contract, Opportunity (postgres) | **accepted** — PK vs FK by design |
| `product scope` | Contract, Opportunity (postgres) | **accepted** — same quantity by design |
| `renewal date` | Contract, Opportunity (postgres) | **accepted** — same quantity by design |

Two former cross-source clashes were **renamed away** (below):

| was | now | side renamed |
| --- | --- | --- |
| `role` on Document (arango) + Contact (postgres) | Contact → `contactRole` | postgres |
| `event date` on Document (arango) + QueryEvent (clickhouse) | QueryEvent → `occurredAt` | clickhouse |

## Allowlist — intentional collisions

`label-collisions-allow.json` (auto-discovered beside `manifest.json`) lists
collisions a curator has intentionally allowed, each with a reason:

```json
{ "allowed": [ { "label": "contract id", "reason": "PK on Contract, FK on Opportunity — one shared key by design" } ] }
```

Allowlisted collisions are still reported (tagged `[accepted — <reason>]`) so the
decision stays visible, but they do not trip `--fail-on-label-collisions`. To
accept a new one, add an entry with a reason; override the file location with
`--allow-collisions PATH`.

## Renames — the two real cross-source clashes (applied)

`role` and `event date` each named **two different things** across two
extractors, so they were renamed rather than accepted. The rename lives in the
**producer config**, not in the generated CSI: r2g maps
`field_mappings: {source_column: target_property}` and drives **both** the CSI
property (`csi.py`) and the R2RML predicate (`r2rml.py`) from the same target
value, so one entry moves the label and the grounding predicate in lockstep while
the physical column is unchanged. Only the r2g side of each pair was renamed;
that clears the collision (the arango `Document.role` / `Document.eventDate`
become unique) without touching the arango reverse-CSI.

| collision | rename | override home |
| --- | --- | --- |
| `role` (Contact side) | column `role` → `contactRole` | `deploy/mappings/mapping.yaml` → `contacts.field_mappings` |
| `event date` (QueryEvent side) | column `event_date` → `occurredAt` | `deploy/clickhouse/mapping.yaml` → `query_events.field_mappings` |

Because there is no wired forward-CSI regeneration in CI (r2g needs a live source
connection), the current committed artifacts were updated to match those
overrides directly and consistently — CSI conceptual + physical models, the R2RML
predicate, and the one NL golden query that referenced `c:role` on Contact
(`src/cdf/eval/corpora/nl-corpus-v1.json`). The `field_mappings` entries above are
the durable record, so the next real r2g regeneration reproduces the same names.

The native executors resolve a SPARQL predicate → column straight from the R2RML
(`parse_r2rml` in `src/cdf/adapters/clickhouse.py`), and Ontop reads
`deploy/ontop/input/mapping.ttl`, so moving the predicate (keeping `rr:column`) is
all the executors need. Verify:

```bash
make catalog-integrity   # rebuilds + gates; exit 0 when clean apart from the allowlist
python -m cdf.catalog.cli validate --root . deploy/catalog/manifest.json --fail-on-label-collisions
```
