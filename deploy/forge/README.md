# Federation Forge — orchestration (ADR-0006, M15)

CDF's half of the Forge. r2g owns *generation* (`r2g.forge`: ontology → DDL /
loader / rows behind one dialect seam); this directory and `cdf.eval.forge`
own *orchestration*: shape sampling, the descriptor, the oracle, the suite.

```
make forge-suite          # fixture mode: sample → emit → byte-compare → run
python -m cdf.eval.forge emit --family chain --seed 7   # one shape
```

## What a shape directory holds (ADR-0006 D-1)

```
deploy/forge/shapes/<family>-<seed>/
  shape.yaml                the descriptor — the contract and the oracle
  ontology.json             CSI v1 conceptual model r2g's forge accepts
  expected-catalog.json     ownership, join keys (cross-system flagged), collisions
  goldens/<name>.json       mechanical template questions + expected outcome
  systems/<sys>.csi.json    fixture-mode CSI per system (producer: cdf-forge-fixture)
```

Descriptor + seed reproduce every file byte-identically; `--check-determinism`
regenerates into a temp dir and diffs. `suite-report.json` is the run record.

The committed suite is a **versioned artifact of generator + seed**: CI
regenerates into `deploy/forge/shapes` with the same flags as `make forge-suite`
and fails if anything under it differs (`git status --porcelain`), and
`tests/test_forge_orchestrator.py::test_committed_suite_matches_a_fresh_emit`
re-derives every shape from its own descriptor. A pin bump that changes
generated names — r2g 0.4.2 carries the singularizer fix — therefore requires
`make forge-suite` and a commit of the result in the same PR.

## Fixture mode vs. live mode

**Fixture mode** (per PR, in CI's offline job) has no databases. The dataset is
still synthesised once by r2g's generator (D-2), then projected into per-source
fixture rows; each golden runs through the real planner, executor and grounding
via `cdf.eval.golden.run_golden`. The per-system CSI is derived by CDF from the
descriptor with r2g's CC-12 naming and is labelled `cdf-forge-fixture` — it is
not analyzer output, and fixture mode therefore tests the fabric's
partition → execute → ground pipeline, not the estate's introspection.

**Live mode** (nightly; S3) drives the same descriptor through the estate:
r2g dialects deploy the schemas, RSA/ASA introspect them, r2g `export-csi` /
`export-r2rml` produce the CSI the catalog builds from, and the goldens run
against the gate. Nothing in the descriptor changes between modes; the fixture
CSI becomes a drift check against the estate-produced one.

## Question families the oracle emits

| family | shape | expectation |
|---|---|---|
| `lookup` | one entity, ≤2 properties | grounded, bindings from the dataset |
| `join` | child ⋈ parent across two systems | grounded, bindings joined on the FK spine |
| `chain` | A → B → C across three systems | grounded |
| `single_leg_aggregation` | `COUNT` grouped by a boolean | grounded where the kind declares GROUP BY (postgresql, arango); **named refusal** elsewhere (ADR-0005 D4) |
| `cross_leg_aggregation` | `COUNT` over a cross-system join | **named refusal** until S2's fold-combine lands; then rewritten as grounded with the counts already computed here |

## Sign-off (roadmap §4)

Which shape families are generated, in what order, and whether a descriptor may
enter CI as a golden is the Solutions Engineer's lane. Every emitted golden
carries `signedOff: false`; the CI step runs them as a **fabric regression
check**, not as policy. Flipping `signedOff` is the SE's commit.

## Pins (CC-9)

The generator core is installed from `deploy/pins/r2g-arango.txt` (a git pin
until r2g 0.4.2 ships `forge.py`; then a version band). The sampler asks r2g's
own naming normaliser whether a class name round-trips rather than assuming —
`Warehouse` does not (r2g hardening list #6), so it is skipped.
