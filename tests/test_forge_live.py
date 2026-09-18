"""Forge live mode (ADR-0006 S2/S3) — the parts that need no engine.

Engine-backed coverage lives in ``tests/test_forge_live_local.py`` (gated on
``CDF_FORGE_LIVE=1`` against the compose stacks). Here: projection, targets,
drift, onboarding over fixture CSI standing in for estate CSI, and the run's
skip/fail/onboard states through injected deployers and introspectors.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

r2g_forge = pytest.importorskip(
    "r2g.forge", reason="r2g generator core not installed (deploy/pins/r2g-arango.txt)"
)

from cdf.catalog.model import FileCatalogLoader  # noqa: E402
from cdf.connectors.secrets import ConnectorRef, EnvSecretResolver  # noqa: E402
from cdf.eval.forge.dataset import synthesize  # noqa: E402
from cdf.eval.forge.fixture_csi import fixture_csi, fk_property  # noqa: E402
from cdf.eval.forge.live import (  # noqa: E402
    LIVE_REPORT_FILE,
    SUBSTITUTION_ORDER,
    DeployedSystem,
    LiveTargets,
    SkippedSystem,
    _swap_database,
    adapt_shape,
    apply_declared_references,
    conceptual_drift,
    cross_system_references,
    database_name,
    deploy_snowflake,
    projected_artifacts,
    projected_conceptual,
    projected_forge_input,
    projected_plan,
    run_live_shape,
    snowflake_registry_fields,
    snowflake_schema_name,
    write_private,
)
from cdf.eval.forge.sampler import FAMILIES, Shape, System, sample_shape  # noqa: E402

LOCAL = ("postgres", "clickhouse", "arango")


def _local_shape(family: str = "chain") -> Shape:
    """A shape whose dialects are all locally deployable (no snowflake)."""
    for seed in range(1, 200):
        shape = sample_shape(seed, family)
        if all(s.dialect in LOCAL for s in shape.systems):
            return shape
    raise AssertionError("no local-only shape in seeds 1..199")


def _cross_system_shape() -> Shape:
    for seed in range(1, 200):
        shape = sample_shape(seed, "two_leg")
        if shape.cross_system_relationships():
            return shape
    raise AssertionError("no two_leg shape with a cross-system relationship")


# ── targets + names ─────────────────────────────────────────────────────────


def test_targets_default_to_the_compose_stacks_and_honour_overrides() -> None:
    t = LiveTargets.from_env({})
    assert t.postgres_admin_dsn == "postgresql://cdf:cdf@127.0.0.1:5433/crm"
    assert t.clickhouse_admin_dsn == "clickhouse://cdf:cdf@127.0.0.1:8123/analytics"
    assert t.arango_url == "http://127.0.0.1:8530" and t.arango_password == "cdf"
    o = LiveTargets.from_env(
        {"CDF_POSTGRES_PORT": "5444", "CDF_FORGE_CLICKHOUSE_DSN": "clickhouse://x"}
    )
    assert o.postgres_admin_dsn.endswith(":5444/crm") and o.clickhouse_admin_dsn == "clickhouse://x"
    for d in LOCAL:
        assert t.missing_reason(d) is None
    assert "snowflake" in (t.missing_reason("snowflake") or "")
    assert LiveTargets().missing_reason("postgres")


def test_database_name_is_identifier_safe_on_every_engine() -> None:
    name = database_name("two_leg-421", "pg1")
    assert name == "forge_two_leg_421_pg1"
    assert database_name("Weird Shape!", "ar1") == "forge_weird_shape__ar1"
    assert len(database_name("x" * 100, "y")) <= 63


# ── projection (ADR-0006 D-2) ───────────────────────────────────────────────


def test_projection_keeps_owned_entities_and_turns_cross_system_fks_into_columns() -> None:
    shape = _cross_system_shape()
    rel = shape.cross_system_relationships()[0]
    child, parent = rel["fromEntity"], rel["toEntity"]
    child_system = shape.owner_system(child)
    owned = {n for n, s in shape.owner.items() if s == child_system.name}

    forge_input = projected_forge_input(shape, child_system)
    assert {e["name"] for e in forge_input["entities"]} == owned and parent not in owned
    for r in forge_input["relationships"]:
        assert r["fromEntity"] in owned and r["toEntity"] in owned, "no constraint off-system"
    r2g_forge.ForgeOntology.from_conceptual(forge_input)  # r2g accepts it (PLAN F-6 holds)

    conceptual = projected_conceptual(shape, child_system)
    child_props = {
        p["name"] for e in conceptual["entities"] if e["name"] == child for p in e["properties"]
    }
    assert fk_property(parent) in child_props, "the CSI view carries the join key as a property"

    plan = projected_plan(shape, child_system)
    table = plan.table(r2g_forge.table_name(child))
    spine = next(c for c in table.columns if c.name == r2g_forge.foreign_key_column(parent))
    assert spine.role == "property" and spine.references is None, "a column, not a constraint"
    assert all(fk.references != r2g_forge.table_name(parent) for fk in table.foreign_keys)
    assert "REFERENCES " + r2g_forge.table_name(parent) not in r2g_forge.get_dialect(
        child_system.dialect
    ).render_ddl(plan)


def test_two_children_of_one_offsystem_parent_do_not_collide() -> None:
    """The case that tripped r2g's F-6 rule when the key was a declared property."""
    for seed in range(1, 300):
        shape = sample_shape(seed, "hub")
        hub = next(iter({r["toEntity"] for r in shape.relationships()}))
        by_system: dict[str, list[str]] = {}
        for r in shape.relationships():
            if shape.owner[r["fromEntity"]] != shape.owner[hub]:
                by_system.setdefault(shape.owner[r["fromEntity"]], []).append(r["fromEntity"])
        crowded = [s for s, kids in by_system.items() if len(kids) >= 2]
        if crowded:
            system = shape.system(crowded[0])
            plan = projected_plan(shape, system)
            for kid in by_system[crowded[0]]:
                names = [c.name for c in plan.table(r2g_forge.table_name(kid)).columns]
                assert names.count(r2g_forge.foreign_key_column(hub)) == 1
            return
    raise AssertionError("no hub shape with two off-system children on one system")


def test_projected_rows_are_the_single_dataset_never_resynthesised() -> None:
    shape = _cross_system_shape()
    ds = synthesize(shape, rows_per_entity=5)
    for system in shape.systems:
        artifacts = projected_artifacts(shape, system, ds, rows_per_entity=5)
        owned = {r2g_forge.table_name(n) for n, s in shape.owner.items() if s == system.name}
        assert set(artifacts.rows) == owned
        for table, rows in artifacts.rows.items():
            assert rows == ds.tables[table], "spine values must agree across systems"
        assert artifacts.dialect == system.dialect and artifacts.ddl


# ── drift ───────────────────────────────────────────────────────────────────


def test_drift_is_empty_for_identical_models_and_names_every_difference() -> None:
    shape = _local_shape()
    system = shape.systems[0]
    csi = fixture_csi(shape, system)
    assert conceptual_drift(csi, csi) == {}
    mutated = json.loads(json.dumps(csi))
    first = mutated["conceptualModel"]["entities"][0]
    first["properties"].append({"name": "surprise"})
    dropped = first["properties"].pop(0)["name"]
    mutated["conceptualModel"]["entities"].append({"name": "Ghost", "properties": []})
    drift = conceptual_drift(csi, mutated)
    assert drift["entitiesExtra"] == ["Ghost"]
    assert drift["properties"][first["name"]] == {"missing": [dropped], "extra": ["surprise"]}


# ── the run, with injected engines ──────────────────────────────────────────


def _fake_deployer(shape, system, dataset, targets, workdir, *, rows_per_entity):
    artifacts = projected_artifacts(shape, system, dataset, rows_per_entity=rows_per_entity)
    if system.kind == "arango":
        fields = {
            "url": "http://fake",
            "database": database_name(shape.name, system.name),
            "user": "root",
            "password": "x",
        }
    elif system.kind == "postgresql":
        fields = {"jdbc_url": "postgresql://fake/db"}  # the Ontop endpoint arrives in slice 4
    elif system.kind == "snowflake":
        schema = snowflake_schema_name(shape.name, system.name)
        fields = snowflake_registry_fields(targets, schema)
        return DeployedSystem(
            system,
            schema,
            fields,
            None,
            tuple(sorted(artifacts.rows)),
            sum(len(r) for r in artifacts.rows.values()),
        )
    else:
        fields = {"dsn": "fake://dsn"}
    return DeployedSystem(
        system,
        database_name(shape.name, system.name),
        fields,
        "fake://dsn",
        tuple(sorted(artifacts.rows)),
        sum(len(r) for r in artifacts.rows.values()),
    )


def _fake_sql_introspect(shape):
    def run(kind, dsn, system_name, *, schema_name):
        system = shape.system(system_name)
        return fixture_csi(shape, system), f"# r2rml for {kind}:{system_name}\n"

    return run


def _fake_graph_introspect(shape):
    def run(fields, system_name):
        return fixture_csi(shape, shape.system(system_name))

    return run


def _fake_warehouse_introspect(shape):
    def run(targets, schema_name, system_name):
        return fixture_csi(
            shape, shape.system(system_name)
        ), f"# r2rml for snowflake:{system_name}\n"

    return run


def _snowflake_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """The fabric's own SNOWFLAKE_* variables, key-pair form, as .env carries them."""
    key = tmp_path / "rsa_key.p8"
    key.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
    env = {
        "SNOWFLAKE_ACCOUNT": "org-acct",
        "SNOWFLAKE_USER": "ARTHURKEEN",
        "SNOWFLAKE_PRIVATE_KEY_FILE": str(key),
        "SNOWFLAKE_WAREHOUSE": "CDF_WH",
        "SNOWFLAKE_DATABASE": "TELEMETRY",
        "SNOWFLAKE_SCHEMA": "PUBLIC",
        "SNOWFLAKE_ROLE": "CDF_RO",
    }
    env.update(overrides)
    return env


def test_run_onboards_a_shape_and_writes_what_from_env_consumes(tmp_path: Path) -> None:
    shape = _local_shape()
    ds = synthesize(shape, rows_per_entity=4)
    report = run_live_shape(
        shape,
        ds,
        LiveTargets.from_env({}),
        tmp_path,
        rows_per_entity=4,
        deployer=_fake_deployer,
        introspect_sql=_fake_sql_introspect(shape),
        introspect_graph=_fake_graph_introspect(shape),
    )
    assert report.status == "onboarded", report.message
    assert [s.name for s in report.systems] == [s.name for s in shape.systems]
    assert all(s.drift == {} for s in report.systems), "fixture standing in for estate → no drift"
    # The manifest loads the way FederationService.from_env loads it, and the
    # shape's DECLARED capabilities rode the overlay into the registry.
    live_env = json.loads((tmp_path / shape.name / "live-env.json").read_text())
    assert live_env["CDF_CATALOG_ROOT"] == str(tmp_path / shape.name)
    loaded = FileCatalogLoader(
        Path(report.manifest), root=Path(live_env["CDF_CATALOG_ROOT"])
    ).load()
    catalog = loaded.source_catalog()
    for system in shape.systems:
        assert catalog.capabilities_for(system.source_id) == system.capabilities
        if system.kind != "arango":
            assert system.source_id in loaded.r2rml_paths
    # Registry-ready sources resolve through the real resolver; Postgres systems
    # wait for slice 4's Ontop and are listed as pending in the env file.
    registry_json = Path(report.registry).read_text(encoding="utf-8")
    resolver = EnvSecretResolver({"CDF_SECRET_REGISTRY_JSON": registry_json})
    for system in shape.systems:
        resolved = resolver.resolve(ConnectorRef(system.source_id, system.kind, system.name))
        if system.kind == "postgresql":
            assert resolved is None
            assert system.source_id in live_env["forge"]["pendingOntop"]
        else:
            assert resolved is not None and resolved.kind == system.kind
    assert (
        json.loads((tmp_path / shape.name / LIVE_REPORT_FILE).read_text())["status"] == "onboarded"
    )


def test_run_skips_a_shape_with_an_unconfigured_dialect_by_name(tmp_path: Path) -> None:
    shape = _local_shape()
    snow = replace(shape.systems[0], dialect="snowflake")
    shape = replace(shape, systems=(snow, *shape.systems[1:]))
    ds = synthesize(shape, rows_per_entity=3)
    report = run_live_shape(
        shape, ds, LiveTargets.from_env({}), tmp_path, rows_per_entity=3, deployer=_fake_deployer
    )
    assert report.status == "skipped"
    assert (
        report.skipped[0]["dialect"] == "snowflake" and "snowflake" in report.skipped[0]["reason"]
    )
    assert report.systems == [] and report.manifest is None


def test_run_reports_a_deploy_failure_instead_of_hiding_it(tmp_path: Path) -> None:
    shape = _local_shape()
    ds = synthesize(shape, rows_per_entity=3)

    def broken(shape, system, dataset, targets, workdir, *, rows_per_entity):
        return SkippedSystem(system, "engine refused the connection")

    report = run_live_shape(
        shape, ds, LiveTargets.from_env({}), tmp_path, rows_per_entity=3, deployer=broken
    )
    assert report.status == "failed" and "engine refused" in (report.message or "")


@pytest.mark.parametrize("family", FAMILIES)
def test_every_family_projects_and_plans_on_all_its_systems(family: str) -> None:
    shape = sample_shape(31, family)
    ds = synthesize(shape, rows_per_entity=3)
    for system in shape.systems:
        artifacts = projected_artifacts(shape, system, ds, rows_per_entity=3)
        assert artifacts.rows, f"{family}/{system.name}: every system owns at least one table"


def test_system_dataclass_still_hashable_for_sets() -> None:
    s = System(name="pg1", dialect="postgres")
    assert len({s, replace(s)}) == 1


# ── declared references ─────────────────────────────────────────────────────


def test_cross_system_references_are_declared_and_verified_against_the_spine() -> None:
    shape = _cross_system_shape()
    ds = synthesize(shape, rows_per_entity=5)
    rel = shape.cross_system_relationships()[0]
    child_system = shape.owner_system(rel["fromEntity"])
    refs = cross_system_references(shape, child_system)
    assert {r["type"] for r in refs} >= {rel["type"]}
    # An "estate" CSI that introspection produced: no cross-system relationship.
    csi = fixture_csi(shape, child_system)
    csi["conceptualModel"]["relationships"] = [
        r
        for r in csi["conceptualModel"]["relationships"]
        if r["type"] not in {x["type"] for x in refs}
    ]
    admitted = apply_declared_references(csi, refs, ds)
    assert admitted == [r["type"] for r in refs]
    assert {r["type"] for r in csi["conceptualModel"]["relationships"]} >= set(admitted)
    assert apply_declared_references(csi, refs, ds) == admitted, "idempotent"
    assert conceptual_drift(fixture_csi(shape, child_system), csi) == {}


def test_a_declared_reference_that_does_not_hold_fails_loudly() -> None:
    shape = _cross_system_shape()
    ds = synthesize(shape, rows_per_entity=5)
    rel = shape.cross_system_relationships()[0]
    child_system = shape.owner_system(rel["fromEntity"])
    fk = r2g_forge.foreign_key_column(rel["toEntity"])
    broken_rows = [
        dict(r, **{fk: 10_000_000}) for r in ds.tables[r2g_forge.table_name(rel["fromEntity"])]
    ]
    broken = replace(ds, tables={**ds.tables, r2g_forge.table_name(rel["fromEntity"]): broken_rows})
    with pytest.raises(ValueError, match="does not hold"):
        apply_declared_references(
            fixture_csi(shape, child_system), cross_system_references(shape, child_system), broken
        )


# ── review fixes: DSN rewrite, credential files, dialect substitution ───────


def test_swap_database_keeps_credentials_port_and_query_string() -> None:
    """Regression: the rpartition('/') rewrite turned ``…/crm?sslmode=require``
    into ``…/crm?sslmode=require/forge_x`` — the query string was mistaken for
    part of the path."""
    assert (
        _swap_database("postgresql://u:p@h:5433/crm?sslmode=require", "forge_x")
        == "postgresql://u:p@h:5433/forge_x?sslmode=require"
    )
    assert _swap_database("clickhouse://cdf:cdf@127.0.0.1:8123/analytics", "forge_y") == (
        "clickhouse://cdf:cdf@127.0.0.1:8123/forge_y"
    )
    assert _swap_database("postgresql://h/", "d") == "postgresql://h/d"


def test_write_private_is_owner_read_only(tmp_path: Path) -> None:
    path = tmp_path / "secret-registry.json"
    write_private(path, "{}")
    assert path.read_text(encoding="utf-8") == "{}"
    assert path.stat().st_mode & 0o777 == 0o600
    write_private(path, "{}\n")  # rewrite keeps the mode
    assert path.stat().st_mode & 0o777 == 0o600


def test_onboarding_writes_credential_files_owner_only(tmp_path: Path) -> None:
    """CC-7: the live directory is the one place the Forge writes credentials;
    those files must not be world-readable (the report, which is uploaded,
    carries none)."""
    shape = _local_shape()
    ds = synthesize(shape, rows_per_entity=3)
    report = run_live_shape(
        shape,
        ds,
        LiveTargets.from_env({}),
        tmp_path,
        rows_per_entity=3,
        deployer=_fake_deployer,
        introspect_sql=_fake_sql_introspect(shape),
        introspect_graph=_fake_graph_introspect(shape),
    )
    assert report.status == "onboarded", report.message
    live_dir = tmp_path / shape.name
    for name in ("secret-registry.json", "live-env.json"):
        assert (live_dir / name).stat().st_mode & 0o777 == 0o600, name
    assert (live_dir / LIVE_REPORT_FILE).stat().st_mode & 0o777 != 0o600
    assert "password" not in (live_dir / LIVE_REPORT_FILE).read_text(encoding="utf-8")


def _with_snowflake(shape: Shape, *positions: int) -> Shape:
    systems = list(shape.systems)
    for i in positions:
        systems[i] = replace(systems[i], dialect="snowflake")
    return replace(shape, systems=tuple(systems))


def test_adapt_shape_substitutes_only_unconfigured_dialects_deterministically() -> None:
    shape = _with_snowflake(_local_shape("chain"), 0, 2)
    targets = LiveTargets.from_env({})
    adapted, adaptations = adapt_shape(shape, targets)
    # names, order, ownership and declared capabilities survive; only dialects move
    assert [s.name for s in adapted.systems] == [s.name for s in shape.systems]
    assert [s.capabilities for s in adapted.systems] == [s.capabilities for s in shape.systems]
    assert adapted.owner == shape.owner and adapted.name == shape.name
    assert adapted.systems[1] == shape.systems[1]  # the configured one is untouched
    assert set(adaptations) == {shape.systems[0].name, shape.systems[2].name}
    assert adaptations[shape.systems[0].name] == {"from": "snowflake", "to": "postgres"}
    assert adaptations[shape.systems[2].name] == {"from": "snowflake", "to": "arango"}
    assert all(targets.missing_reason(s.dialect) is None for s in adapted.systems)
    assert adapt_shape(shape, targets) == (adapted, adaptations)  # deterministic
    assert SUBSTITUTION_ORDER[:3] == ("postgres", "arango", "clickhouse")


def test_adapt_shape_is_identity_when_nothing_is_missing_or_nothing_is_configured() -> None:
    local = _local_shape("hub")
    assert adapt_shape(local, LiveTargets.from_env({})) == (local, {})
    snow = _with_snowflake(local, 0)
    assert adapt_shape(snow, LiveTargets()) == (snow, {})  # no target to substitute with


def test_run_carries_adaptations_into_the_report_and_its_file(tmp_path: Path) -> None:
    shape = _with_snowflake(_local_shape("two_leg"), 0)
    targets = LiveTargets.from_env({})
    adapted, adaptations = adapt_shape(shape, targets)
    ds = synthesize(adapted, rows_per_entity=3)
    report = run_live_shape(
        adapted,
        ds,
        targets,
        tmp_path,
        rows_per_entity=3,
        deployer=_fake_deployer,
        introspect_sql=_fake_sql_introspect(adapted),
        introspect_graph=_fake_graph_introspect(adapted),
        adaptations=adaptations,
    )
    assert report.status == "onboarded", report.message
    assert report.adaptations == adaptations and report.to_dict()["adaptations"] == adaptations
    on_disk = json.loads((tmp_path / shape.name / LIVE_REPORT_FILE).read_text(encoding="utf-8"))
    assert on_disk["adaptations"] == adaptations
    # without adaptation the same shape is skipped whole, as before
    assert (
        run_live_shape(
            shape, ds, targets, tmp_path / "plain", rows_per_entity=3, deployer=_fake_deployer
        ).status
        == "skipped"
    )


# ── the Snowflake live target ───────────────────────────────────────────────


def test_snowflake_target_comes_from_the_fabrics_own_variables(tmp_path: Path) -> None:
    """One set of credentials (SNOWFLAKE_*); the Forge deploys as its own role
    into its own database and the fabric keeps the read-only role (CC-7)."""
    t = LiveTargets.from_env(_snowflake_env(tmp_path))
    assert t.missing_reason("snowflake") is None and t.snowflake_reason is None
    assert t.snowflake is not None
    assert t.snowflake["database"] == "CDF_FORGE" and t.snowflake["role"] == "CDF_FORGE"
    assert t.snowflake["authenticator"] == "SNOWFLAKE_JWT" and "schema" not in t.snowflake
    assert t.snowflake_query_role == "CDF_RO"
    o = LiveTargets.from_env(
        _snowflake_env(
            tmp_path, CDF_FORGE_SNOWFLAKE_DATABASE="SANDBOX", CDF_FORGE_SNOWFLAKE_ROLE="DEV"
        )
    )
    assert o.snowflake is not None and (o.snowflake["database"], o.snowflake["role"]) == (
        "SANDBOX",
        "DEV",
    )
    # absent → named reason pointing at the setup script; incomplete → the validator's words
    absent = LiveTargets.from_env({})
    assert absent.snowflake is None and "setup_forge.sql" in (
        absent.missing_reason("snowflake") or ""
    )
    partial = LiveTargets.from_env({"SNOWFLAKE_ACCOUNT": "x"})
    assert partial.snowflake is None and "SNOWFLAKE_USER" in (
        partial.missing_reason("snowflake") or ""
    )
    assert LiveTargets().missing_reason("snowflake")


def test_snowflake_schema_name_is_uppercase_and_engine_safe() -> None:
    assert snowflake_schema_name("two_leg-421", "sf1") == "FORGE_TWO_LEG_421_SF1"
    assert (
        snowflake_schema_name("two_leg-421", "sf1") == database_name("two_leg-421", "sf1").upper()
    )


def test_snowflake_registry_fields_carry_the_query_role_and_resolve(tmp_path: Path) -> None:
    t = LiveTargets.from_env(_snowflake_env(tmp_path))
    fields = snowflake_registry_fields(t, "FORGE_X_SF1")
    assert fields["role"] == "CDF_RO" and fields["schema"] == "FORGE_X_SF1"
    assert fields["database"] == "CDF_FORGE" and "authenticator" not in fields
    assert fields["private_key_file"].endswith("rsa_key.p8") and "password" not in fields
    registry = {
        "version": 1,
        "sources": {
            "snowflake:sf1": {
                "kind": "snowflake",
                "ref": "sf1",
                "generation": "g",
                "fields": fields,
            }
        },
    }
    resolver = EnvSecretResolver({"CDF_SECRET_REGISTRY_JSON": json.dumps(registry)})
    resolved = resolver.resolve(ConnectorRef("snowflake:sf1", "snowflake", "sf1"))
    assert resolved is not None and resolved.kind == "snowflake"


class _FakeSnowflakeConnection:
    """Mirrors what :func:`deploy_snowflake` uses of ``snowflake.connector.connect``'s
    result: ``cursor().execute(sql)`` and ``close()``."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.executed: list[str] = []
        self.closed = False

    def cursor(self) -> _FakeSnowflakeConnection:
        return self

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def close(self) -> None:
        self.closed = True


def test_deploy_snowflake_replaces_the_schema_and_replays_ddl_then_rows(tmp_path: Path) -> None:
    from r2g.forge import split_sql_statements

    shape = _with_snowflake(_local_shape("two_leg"), 0)
    system = shape.systems[0]
    ds = synthesize(shape, rows_per_entity=3)
    artifacts = projected_artifacts(shape, system, ds, rows_per_entity=3)
    assert "CREATE TABLE " in artifacts.ddl and artifacts.ddl.split("CREATE TABLE ")[1][0].isupper()
    t = LiveTargets.from_env(_snowflake_env(tmp_path))
    made: list[_FakeSnowflakeConnection] = []

    def connect(**kwargs: object) -> _FakeSnowflakeConnection:
        made.append(_FakeSnowflakeConnection(**kwargs))
        return made[-1]

    fields = deploy_snowflake(artifacts, t, "FORGE_TWO_LEG_SF1", connect=connect)
    conn = made[0]
    assert conn.kwargs["role"] == "CDF_FORGE" and conn.kwargs["login_timeout"] == 60
    assert conn.executed[:2] == [
        "CREATE OR REPLACE SCHEMA CDF_FORGE.FORGE_TWO_LEG_SF1",
        "USE SCHEMA CDF_FORGE.FORGE_TWO_LEG_SF1",
    ]
    assert conn.executed[2:] == split_sql_statements(artifacts.ddl) + split_sql_statements(
        artifacts.load_sql
    )
    assert conn.closed
    assert fields == snowflake_registry_fields(t, "FORGE_TWO_LEG_SF1")


def test_run_onboards_a_snowflake_system_registry_ready(tmp_path: Path) -> None:
    shape = _with_snowflake(_local_shape("two_leg"), 0)
    sf = shape.systems[0]
    targets = LiveTargets.from_env(_snowflake_env(tmp_path))
    ds = synthesize(shape, rows_per_entity=3)
    report = run_live_shape(
        shape,
        ds,
        targets,
        tmp_path,
        rows_per_entity=3,
        deployer=_fake_deployer,
        introspect_sql=_fake_sql_introspect(shape),
        introspect_graph=_fake_graph_introspect(shape),
        introspect_warehouse=_fake_warehouse_introspect(shape),
    )
    assert report.status == "onboarded", report.message
    result = next(r for r in report.systems if r.name == sf.name)
    assert result.kind == "snowflake" and result.database == snowflake_schema_name(
        shape.name, sf.name
    )
    assert result.drift == {} and result.r2rml_path is not None
    registry = json.loads(Path(report.registry).read_text(encoding="utf-8"))
    entry = registry["sources"][sf.source_id]
    assert entry["kind"] == "snowflake" and entry["fields"]["role"] == "CDF_RO"
    live_env = json.loads((tmp_path / shape.name / "live-env.json").read_text(encoding="utf-8"))
    assert sf.source_id not in live_env["forge"]["pendingOntop"]
    resolver = EnvSecretResolver({"CDF_SECRET_REGISTRY_JSON": json.dumps(registry)})
    assert resolver.resolve(ConnectorRef(sf.source_id, "snowflake", sf.name)) is not None
