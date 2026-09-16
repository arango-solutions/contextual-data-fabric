"""Federation Forge orchestration (ADR-0006, M15) — fixture mode.

These tests need r2g's forge (the generator core) importable; they skip when
the CC-9 pin in ``deploy/pins/r2g-arango.txt`` is not installed.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

r2g_forge = pytest.importorskip(
    "r2g.forge", reason="r2g generator core not installed (deploy/pins/r2g-arango.txt)"
)

from cdf.catalog.capabilities import (  # noqa: E402
    AggregationCapability,
    SourceCapabilities,
    capabilities_document,
    default_capabilities_for_kind,
)
from cdf.eval.forge.dataset import synthesize  # noqa: E402
from cdf.eval.forge.descriptor import (  # noqa: E402
    DESCRIPTOR_FILE,
    load_descriptor,
    validate_descriptor,
)
from cdf.eval.forge.fixture_csi import fixture_csi  # noqa: E402
from cdf.eval.forge.oracle import compose_goldens, expected_catalog  # noqa: E402
from cdf.eval.forge.sampler import FAMILIES, sample_shape  # noqa: E402
from cdf.eval.forge.signoff import load_signoff, signoff_status  # noqa: E402
from cdf.eval.forge.suite import (  # noqa: E402
    check_determinism,
    committed_suite_drift,
    emit_shape,
    run_shape,
    sample_suite,
)
from cdf.eval.golden import run_golden  # noqa: E402

# ── sampler ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("family", FAMILIES)
def test_sampler_is_deterministic_and_single_owner(family: str) -> None:
    a = sample_shape(7, family)
    b = sample_shape(7, family)
    assert a == b
    names = {e["name"] for e in a.entities()}
    assert set(a.owner) == names, "every concept has exactly one owner"
    assert set(a.owner.values()) == {s.name for s in a.systems}, "every system owns a concept"
    for rel in a.relationships():
        assert rel["fromEntity"] in names and rel["toEntity"] in names
        assert rel["type"] == r2g_forge.expected_relationship_type(
            rel["fromEntity"], rel["toEntity"]
        )


def test_sampler_families_have_their_defining_shape() -> None:
    two = sample_shape(1, "two_leg")
    assert len(two.systems) == 2 and two.cross_system_relationships()
    chain = sample_shape(1, "chain")
    assert (
        len(chain.systems) == len(chain.entities())
        and len(chain.relationships()) == len(chain.entities()) - 1
    )
    hub = sample_shape(1, "hub")
    targets = {r["toEntity"] for r in hub.relationships()}
    assert len(targets) == 1, "hub: every relationship points at the hub"
    wide = sample_shape(1, "wide_narrow")
    sizes = sorted(len(e["properties"]) for e in wide.entities())
    assert sizes[-1] >= 10 and sizes[0] <= 2
    six = sample_shape(1, "six_leg")
    assert len(six.systems) == 6


def test_different_seeds_differ() -> None:
    assert sample_shape(1, "two_leg").ontology != sample_shape(2, "two_leg").ontology


def test_ontology_is_accepted_by_r2g_forge() -> None:
    for family in FAMILIES:
        shape = sample_shape(3, family)
        r2g_forge.ForgeOntology.from_conceptual(
            shape.ontology
        )  # raises ForgeError if not roundtrippable


# ── dataset + fixture CSI ───────────────────────────────────────────────────


def test_dataset_is_synthesised_once_and_spine_safe() -> None:
    shape = sample_shape(5, "two_leg")
    ds = synthesize(shape, rows_per_entity=6)
    for rel in shape.relationships():
        parent_ids = {r["id"] for r in ds.rows(rel["toEntity"])}
        fk = r2g_forge.foreign_key_column(rel["toEntity"])
        for row in ds.rows(rel["fromEntity"]):
            assert row[fk] in parent_ids, "every FK value points at an existing parent row"


def test_fixture_csi_is_labelled_and_routes_by_system() -> None:
    shape = sample_shape(5, "hub")
    for system in shape.systems:
        doc = fixture_csi(shape, system)
        assert doc["provenance"]["producer"] == "cdf-forge-fixture"
        assert doc["provenance"]["source"] == {"kind": system.kind, "ref": system.name}
        owned = {e["name"] for e in doc["conceptualModel"]["entities"]}
        assert owned == {n for n, s in shape.owner.items() if s == system.name}
        for rel in doc["conceptualModel"]["relationships"]:
            assert rel["fromEntity"] in owned, "relationships are declared where their FK lives"


# ── oracle ──────────────────────────────────────────────────────────────────


def test_expected_catalog_marks_cross_system_join_keys() -> None:
    shape = sample_shape(11, "chain")
    cat = expected_catalog(shape)
    assert cat["ownership"] == shape.owner
    assert all(k["crossSystem"] for k in cat["joinKeys"]), "a chain crosses systems at every hop"
    assert cat["collisions"] == []


def test_join_golden_bindings_follow_the_spine() -> None:
    shape = sample_shape(2, "two_leg")
    ds = synthesize(shape, rows_per_entity=5)
    joins = [c for c in compose_goldens(shape, ds) if c["family"] == "join"]
    assert joins
    case = joins[0]
    assert len(case["sources"]) == 2
    assert len(case["expect"]["bindings"]) == 5, "one binding per child row (every FK resolves)"
    assert "signedOff" not in case, "sign-off lives in deploy/forge/signoff.yaml, never in goldens"


@pytest.mark.parametrize("family", FAMILIES)
def test_generated_goldens_pass_through_the_real_planner(family: str) -> None:
    """The fixture-mode contract: partition → execute → ground agrees with the oracle."""
    shape = sample_shape(21, family)
    ds = synthesize(shape, rows_per_entity=4)
    outcomes = [run_golden(case) for case in compose_goldens(shape, ds)]
    failed = [(o.name, o.mismatches) for o in outcomes if not o.passed]
    assert not failed, failed
    families = {c["family"] for c in compose_goldens(shape, ds)}
    assert {"lookup", "join"} <= families


# ── emit + determinism ──────────────────────────────────────────────────────


def test_emit_is_byte_identical_on_regeneration(tmp_path: Path) -> None:
    shape = sample_shape(8, "wide_narrow")
    emitted = emit_shape(shape, tmp_path, rows_per_entity=3)
    assert check_determinism(shape, emitted.directory, rows_per_entity=3) == []
    doc = load_descriptor(emitted.directory / DESCRIPTOR_FILE)
    assert doc["name"] == shape.name and doc["partitionMap"] == shape.partition_map()
    for rel in doc["expected"]["goldens"]:
        assert (emitted.directory / rel).exists()
    assert all(not o.mismatches for o in run_shape(emitted))


def test_descriptor_validation_rejects_bad_partition_targets(tmp_path: Path) -> None:
    shape = sample_shape(8, "two_leg")
    emitted = emit_shape(shape, tmp_path, rows_per_entity=2)
    doc = json.loads(json.dumps(load_descriptor(emitted.directory / DESCRIPTOR_FILE)))
    concept = next(iter(doc["partitionMap"]))
    doc["partitionMap"][concept]["system"] = "nowhere"
    with pytest.raises(ValueError, match="unknown system"):
        validate_descriptor(doc)


def test_sample_suite_cycles_families() -> None:
    shapes = sample_suite(shapes=7, seed=100)
    assert [s.family for s in shapes] == [FAMILIES[i % len(FAMILIES)] for i in range(7)]
    assert len({s.name for s in shapes}) == 7


# ── the committed suite ─────────────────────────────────────────────────────

COMMITTED_SHAPES = Path(__file__).resolve().parents[1] / "deploy" / "forge" / "shapes"


def test_committed_suite_matches_a_fresh_emit() -> None:
    """PR #34 review, item 1: the suite under deploy/forge/shapes is a versioned
    artifact of generator + seed. Re-derive every committed shape from its own
    descriptor and byte-compare — the git-free twin of CI's drift step, so a
    pin bump that renames generated classes fails here before it reaches CI."""
    drift = committed_suite_drift(COMMITTED_SHAPES)
    assert drift == {}, drift


def test_committed_suite_drift_detects_a_changed_and_a_missing_file(tmp_path: Path) -> None:
    """The negative half of the check above: a single edited golden and a single
    deleted file in a copy of one committed shape must both be reported."""
    src = next(p for p in sorted(COMMITTED_SHAPES.iterdir()) if p.is_dir())
    copy = tmp_path / src.name
    shutil.copytree(src, copy)
    golden = sorted((copy / "goldens").glob("*.json"))[0]
    edited = golden.read_text(encoding="utf-8").replace("grounded", "GROUNDED", 1)
    golden.write_text(edited, encoding="utf-8")
    (copy / "ontology.json").unlink()
    drift = committed_suite_drift(tmp_path)
    assert set(drift) == {src.name}
    assert f"goldens/{golden.name}" in drift[src.name]
    assert "ontology.json" in drift[src.name]


# ── sign-off survives regeneration ──────────────────────────────────────────


def test_regeneration_never_touches_the_signoff_ledger(tmp_path: Path) -> None:
    """PR #34 review, item 2: sign-off lives in a hand-maintained ledger outside
    the generated tree. Emit twice (the second run rmtree's the shape directory);
    the ledger bytes are unchanged, the sign-off still applies, and no generated
    golden carries a signedOff key."""
    shape = sample_shape(2, "two_leg")
    first = emit_shape(shape, tmp_path / "shapes", rows_per_entity=3)
    signed_name = first.goldens[0]["name"]
    ledger_path = tmp_path / "signoff.yaml"
    ledger_text = (
        "forgeSignoffVersion: 1\n"
        "goldens:\n"
        f"  {signed_name}:\n"
        "    signedOff: true\n"
        "    signedBy: se\n"
        "    signedOn: 2026-09-16\n"
    )
    ledger_path.write_text(ledger_text, encoding="utf-8")

    second = emit_shape(shape, tmp_path / "shapes", rows_per_entity=3)

    assert ledger_path.read_text(encoding="utf-8") == ledger_text
    status = signoff_status(load_signoff(ledger_path), [c["name"] for c in second.goldens])
    assert status.signed == (signed_name,) and status.unknown == ()
    for golden in (second.directory / "goldens").glob("*.json"):
        assert "signedOff" not in json.loads(golden.read_text(encoding="utf-8"))


# ── capabilities: declared per system, applied through the registry ─────────

_SPARQL_KINDS = {"postgresql", "arango"}  # kinds whose legacy default admits GROUP BY


def _declaring(shape, system_name: str, group_by: bool):
    """A copy of ``shape`` where one system declares (or disclaims) GROUP BY."""
    declared = SourceCapabilities(
        aggregation=AggregationCapability(
            group_by=group_by, having=group_by, count_distinct=group_by
        )
    )
    systems = tuple(
        replace(s, capabilities=declared) if s.name == system_name else s for s in shape.systems
    )
    return replace(shape, systems=systems)


def _aggregation_case(shape, ds, entity: str):
    return next(
        c
        for c in compose_goldens(shape, ds)
        if c["family"] == "single_leg_aggregation" and c["name"].endswith(f"--{entity}")
    )


def _shape_with_boolean_on_both_kind_classes():
    """A chain shape owning a boolean-carrying entity on a native-kind system
    (snowflake/clickhouse) AND on a SPARQL-kind system (postgresql/arango)."""
    for seed in range(1, 80):
        shape = sample_shape(seed, "chain")
        native = sparql = None
        for e in shape.entities():
            if not any(p["type"] == "boolean" for p in e["properties"]):
                continue
            system = shape.owner_system(e["name"])
            if system.kind in _SPARQL_KINDS:
                sparql = sparql or (e["name"], system)
            else:
                native = native or (e["name"], system)
        if native and sparql:
            return shape, native, sparql
    raise AssertionError("no chain seed in 1..79 has booleans on both kind classes")


def test_capabilities_are_declared_per_system_independent_of_kind() -> None:
    """PR #34 review, item 3: the declaration is a per-system coin, not the
    engine kind — over a suite every kind appears with both values."""
    seen: dict[str, set[bool]] = {}
    for seed in range(1, 41):
        for system in sample_shape(seed, "six_leg").systems:
            seen.setdefault(system.kind, set()).add(system.capabilities.aggregation.group_by)
    for kind in ("postgresql", "arango", "snowflake", "clickhouse"):
        assert seen[kind] == {True, False}, f"{kind}: declarations must not follow the kind"


def test_declared_capability_decides_the_aggregation_golden_through_the_registry() -> None:
    """The oracle reads the declaration; the planner reads the same declaration
    through the registry. Kind defaults say the OPPOSITE for both systems here,
    so a pass proves the override path — and stripping the declaration from the
    golden makes it fail, so the golden can fail."""
    shape, (native_entity, native), (sparql_entity, sparql) = (
        _shape_with_boolean_on_both_kind_classes()
    )
    assert default_capabilities_for_kind(native.kind).aggregation.group_by is False
    assert default_capabilities_for_kind(sparql.kind).aggregation.group_by is True

    # A ClickHouse/Snowflake system that DECLARES GROUP BY grounds.
    declared = _declaring(shape, native.name, True)
    ds = synthesize(declared, rows_per_entity=4)
    case = _aggregation_case(declared, ds, native_entity)
    assert case["expect"]["status"] == "grounded"
    assert case["sources"][0]["capabilities"]["aggregation"]["groupBy"] is True
    assert run_golden(case).passed

    # …and the same golden with the declaration stripped falls back to the kind
    # default and FAILS: expected and actual share only the declaration.
    stripped = json.loads(json.dumps(case))
    for source in stripped["sources"]:
        del source["capabilities"]
    outcome = run_golden(stripped)
    assert not outcome.passed and any("GROUP BY" in m for m in outcome.mismatches)

    # A Postgres/Arango system that declares NONE is refused by capability name.
    disclaimed = _declaring(shape, sparql.name, False)
    case = _aggregation_case(disclaimed, synthesize(disclaimed, rows_per_entity=4), sparql_entity)
    assert case["expect"]["unsupported_contains"][0] == "declares no GROUP BY capability"
    assert run_golden(case).passed


def test_descriptor_requires_declared_capabilities(tmp_path: Path) -> None:
    shape = sample_shape(8, "two_leg")
    emitted = emit_shape(shape, tmp_path, rows_per_entity=2)
    doc = json.loads(json.dumps(load_descriptor(emitted.directory / DESCRIPTOR_FILE)))
    name = next(iter(doc["systems"]))
    assert doc["systems"][name]["capabilities"] == capabilities_document(
        shape.system(name).capabilities
    )
    del doc["systems"][name]["capabilities"]
    with pytest.raises(ValueError, match="missing 'capabilities'"):
        validate_descriptor(doc)
    doc["systems"][name]["capabilities"] = {"aggregation": {"groupBy": "yes"}}
    with pytest.raises(ValueError, match="groupBy"):
        validate_descriptor(doc)


def test_expected_catalog_reports_declared_capabilities() -> None:
    shape = sample_shape(11, "hub")
    cat = expected_catalog(shape)
    for system in shape.systems:
        assert cat["systems"][system.name]["capabilities"] == capabilities_document(
            system.capabilities
        )
