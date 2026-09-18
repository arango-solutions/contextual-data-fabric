"""Forge **live mode** (ADR-0006; roadmap S2/S3): a shape driven through the ESTATE.

Fixture mode (:mod:`.suite`) proves partition → execute → ground with fixture
executors and CDF-derived CSI. Live mode proves the rest of the chain with the
real analyzers and real engines:

``deploy``
    r2g's dialect emits DDL and a loader for **the entities each system owns**;
    they run against a fresh database per system in the local compose stacks
    (Postgres, ClickHouse, ArangoDB) or the configured Snowflake account.
``introspect`` / ``export``
    the forward pipeline the estate ships — r2g's connectors → Auto-Map →
    ``mapping_to_csi`` / ``mapping_to_r2rml`` for relational systems, ASA's
    ``analyze`` → ``to_csi`` for ArangoDB — producing the CSI the catalog builds
    from, in place of the fixture CSI.
``declare``
    a relationship whose parent lives on another system is invisible to that
    system's introspection — no constraint can exist there — so live mode
    applies the descriptor's cross-system relationships as **declared
    references**, verified against the one dataset's join spine, the same
    mechanism the demo estate uses (the CRM key overlay; ``cmf-refs``) and
    RD-3's curated mapping formalises. They are reported by name, apart from
    anything introspection found on its own.
``drift``
    fixture CSI vs estate CSI (after declaration), conceptual model only,
    normalized through the CC-12 namers: the drift signal
    ``deploy/forge/README.md`` promises.
``onboard``
    the catalog builder over the estate-produced artifacts, with the shape's
    **declared** capabilities riding the governance overlay (fixture mode
    declares; live mode's CC-14 probe, slice 4, is what strips them), plus the
    secret-registry JSON ``FederationService.from_env`` consumes.

Partitioning rule (D-2): data is synthesised **once** for the whole ontology;
each system's tables are a *projection* — only owned entities, with the join
key to a parent on another system kept as a plain column (no foreign-key
constraint can point across systems) and rows taken verbatim from the single
dataset, so the spine values agree by construction.

A system whose dialect has no configured target is **skipped by name**, never
faked; the shape's report says so. Slice 4 (execute: an Ontop per shape and
``run_golden_live``) follows in its own change.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from dataclasses import replace as _replace
from pathlib import Path
from typing import Any

from r2g.csi import owl_property_name
from r2g.forge import (
    ColumnPlan,
    ForgeArtifacts,
    ForgeOntology,
    SchemaPlan,
    foreign_key_column,
    get_dialect,
    plan_schema,
)
from r2g.forge.core import ROLE_PROPERTY

from cdf.adapters.snowflake import build_snowflake_connect_args
from cdf.catalog.builder import build_manifest
from cdf.catalog.capabilities import capabilities_document
from cdf.catalog.model import FileCatalogLoader
from cdf.eval.forge.dataset import Dataset
from cdf.eval.forge.fixture_csi import fixture_csi, fk_property
from cdf.eval.forge.sampler import Shape, System

DEFAULT_LIVE_DIR = Path("deploy/forge/live")
LIVE_REPORT_FILE = "live-report.json"
_IDENT = re.compile(r"[^a-z0-9_]")


# ── targets ─────────────────────────────────────────────────────────────────


#: The Forge's Snowflake footprint (``deploy/snowflake/setup_forge.sql``): its
#: own database and a deployer role that may create schemas there and nowhere
#: else. The fabric keeps querying as the read-only ``SNOWFLAKE_ROLE`` (CC-7).
DEFAULT_SNOWFLAKE_FORGE_DATABASE = "CDF_FORGE"
DEFAULT_SNOWFLAKE_FORGE_ROLE = "CDF_FORGE"

#: Connector kwargs that are also secret-registry fields for kind ``snowflake``
#: (``authenticator`` is derived by the fabric at connect time, never stored).
_SNOWFLAKE_REGISTRY_FIELDS = frozenset(
    {
        "account",
        "user",
        "password",
        "private_key_file",
        "private_key_file_pwd",
        "warehouse",
        "database",
        "role",
    }
)


def _snowflake_targets(
    e: Mapping[str, str],
) -> tuple[dict[str, str] | None, str | None, str | None]:
    """``(deployer kwargs, query role, reason-if-missing)`` from ``SNOWFLAKE_*``
    (the fabric's own variables — one set of credentials) plus the Forge's
    ``CDF_FORGE_SNOWFLAKE_DATABASE`` / ``CDF_FORGE_SNOWFLAKE_ROLE`` overrides."""
    if not e.get("SNOWFLAKE_ACCOUNT"):
        return (
            None,
            None,
            "no Snowflake live target on this host (SNOWFLAKE_ACCOUNT unset; snowflake "
            "systems are skipped, or substituted with --substitute-unavailable) — "
            "see deploy/snowflake/setup_forge.sql",
        )
    try:
        args = build_snowflake_connect_args(e)
    except ValueError as exc:
        return None, None, f"Snowflake credentials incomplete for the snowflake live target: {exc}"
    args["database"] = e.get("CDF_FORGE_SNOWFLAKE_DATABASE") or DEFAULT_SNOWFLAKE_FORGE_DATABASE
    args["role"] = e.get("CDF_FORGE_SNOWFLAKE_ROLE") or DEFAULT_SNOWFLAKE_FORGE_ROLE
    args.pop("schema", None)  # one schema per deployed system, never the fabric's default
    return args, e.get("SNOWFLAKE_ROLE") or None, None


@dataclass(frozen=True)
class LiveTargets:
    """Where live mode may create databases. Defaults follow the compose stacks
    and the Makefile's port variables; every value is overridable by env.
    Snowflake is a real account: configured through the fabric's own
    ``SNOWFLAKE_*`` variables (``.env``; the ``live-full`` job's secrets)."""

    postgres_admin_dsn: str | None = None
    clickhouse_admin_dsn: str | None = None
    arango_url: str | None = None
    arango_user: str = "root"
    arango_password: str = ""
    snowflake: Mapping[str, str] | None = None
    """Validated connector kwargs for the Forge's DEPLOYER role — creates one
    schema per system in :data:`DEFAULT_SNOWFLAKE_FORGE_DATABASE`; ``None`` (with
    :attr:`snowflake_reason`) when this host has no Snowflake configuration."""
    snowflake_query_role: str | None = None
    """The role the FABRIC connects with (``SNOWFLAKE_ROLE``, read-only): what
    the secret registry carries for a deployed Snowflake system."""
    snowflake_reason: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LiveTargets:
        e = dict(os.environ if env is None else env)
        pg_port = e.get("CDF_POSTGRES_PORT", "5433")
        ch_port = e.get("CDF_CLICKHOUSE_HTTP_PORT", "8123")
        ar_port = e.get("CDF_ARANGO_PORT", "8530")
        snowflake, query_role, reason = _snowflake_targets(e)
        return cls(
            postgres_admin_dsn=e.get(
                "CDF_FORGE_PG_DSN", f"postgresql://cdf:cdf@127.0.0.1:{pg_port}/crm"
            ),
            clickhouse_admin_dsn=e.get(
                "CDF_FORGE_CLICKHOUSE_DSN", f"clickhouse://cdf:cdf@127.0.0.1:{ch_port}/analytics"
            ),
            arango_url=e.get("ARANGO_URL", f"http://127.0.0.1:{ar_port}"),
            arango_user=e.get("ARANGO_USER", "root"),
            arango_password=e.get("ARANGO_PASSWORD", "cdf"),
            snowflake=snowflake,
            snowflake_query_role=query_role,
            snowflake_reason=reason,
        )

    def missing_reason(self, dialect: str) -> str | None:
        """``None`` when the dialect can be deployed; else why not, by name."""
        if dialect == "postgres" and self.postgres_admin_dsn:
            return None
        if dialect == "clickhouse" and self.clickhouse_admin_dsn:
            return None
        if dialect == "arango" and self.arango_url:
            return None
        if dialect == "snowflake":
            if self.snowflake:
                return None
            return self.snowflake_reason or "no Snowflake live target configured (snowflake)"
        return f"no live target configured for dialect {dialect!r}"


#: Order in which a configured dialect stands in for an unconfigured one.
SUBSTITUTION_ORDER: tuple[str, ...] = ("postgres", "arango", "clickhouse", "snowflake")


def adapt_shape(shape: Shape, targets: LiveTargets) -> tuple[Shape, dict[str, dict[str, str]]]:
    """Substitute a configured dialect for every system whose dialect has no
    live target, so the shape's *topology* (systems, ownership, joins, declared
    capabilities) still runs through the estate. Deterministic (cycles through
    :data:`SUBSTITUTION_ORDER`), names and capabilities preserved, the committed
    descriptor untouched; the adaptations are reported by name so a reader
    never mistakes the run for the original shape. Without this, every chain
    and hub shape — the multi-hop cases — was skipped whole for Snowflake."""
    available = [d for d in SUBSTITUTION_ORDER if targets.missing_reason(d) is None]
    if not available:
        return shape, {}
    adaptations: dict[str, dict[str, str]] = {}
    systems = []
    cycle = 0
    for system in shape.systems:
        if targets.missing_reason(system.dialect) is None:
            systems.append(system)
            continue
        replacement = available[cycle % len(available)]
        cycle += 1
        adaptations[system.name] = {"from": system.dialect, "to": replacement}
        systems.append(
            System(name=system.name, dialect=replacement, capabilities=system.capabilities)
        )
    return _replace(shape, systems=tuple(systems)), adaptations


def database_name(shape_name: str, system_name: str) -> str:
    """``forge_<shape>_<system>`` — lowercase, identifier-safe on every engine."""
    raw = f"forge_{shape_name}_{system_name}".lower().replace("-", "_")
    return _IDENT.sub("_", raw)[:63]


# ── projection: what one system holds ───────────────────────────────────────


def _owned(shape: Shape, system: System) -> set[str]:
    return {e["name"] for e in shape.entities() if shape.owner[e["name"]] == system.name}


def projected_forge_input(shape: Shape, system: System) -> dict[str, Any]:
    """The skeleton ontology handed to r2g for **this system**: owned entities
    with their declared properties, and only the relationships whose both
    endpoints are owned (r2g then emits the FK column *and* the constraint).
    Cross-system relationships are deliberately absent here — their join key is
    added to the plan by :func:`projected_plan`, because declaring it as a
    property would trip r2g's collision rule (PLAN F-6) whenever two children
    point at the same off-system parent."""
    owned = _owned(shape, system)
    return {
        "entities": [
            {"name": e["name"], "properties": [dict(p) for p in e["properties"]]}
            for e in shape.entities()
            if e["name"] in owned
        ],
        "relationships": [
            dict(r)
            for r in shape.relationships()
            if r["fromEntity"] in owned and r["toEntity"] in owned
        ],
    }


def projected_conceptual(shape: Shape, system: System) -> dict[str, Any]:
    """What this system's CSI should say (ADR-0006 D-2): the forge input plus,
    on each child whose parent lives elsewhere, the join key as a property
    (``accountId`` ↔ column ``account_id``) — the same shape
    :func:`~cdf.eval.forge.fixture_csi.fixture_csi` declares."""
    owned = _owned(shape, system)
    model = projected_forge_input(shape, system)
    for e in model["entities"]:
        for rel in shape.relationships():
            if rel["fromEntity"] == e["name"] and rel["toEntity"] not in owned:
                e["properties"].append({"name": fk_property(rel["toEntity"]), "type": "integer"})
        e["properties"].sort(key=lambda p: p["name"])
    return model


def projected_plan(shape: Shape, system: System) -> SchemaPlan:
    """r2g's schema plan for the projection, with one plumbing column per
    cross-system relationship on the child table: ``<parent>_id INTEGER``,
    role *property* — so no FOREIGN KEY constraint is rendered (none could be
    satisfied across systems) while the spine column is present and the
    single dataset's values load into it verbatim."""
    owned = _owned(shape, system)
    plan = plan_schema(ForgeOntology.from_conceptual(projected_forge_input(shape, system)))
    spine: dict[str, dict[str, ColumnPlan]] = {}
    for rel in shape.relationships():
        if rel["fromEntity"] in owned and rel["toEntity"] not in owned:
            column = foreign_key_column(rel["toEntity"])
            spine.setdefault(rel["fromEntity"], {})[column] = ColumnPlan(
                name=column,
                json_type="integer",
                role=ROLE_PROPERTY,
                prop=fk_property(rel["toEntity"]),
            )
    tables = []
    for table in plan.tables:
        extra = spine.get(table.entity, {})
        present = {c.name for c in table.columns}
        added = tuple(extra[name] for name in sorted(extra) if name not in present)
        tables.append(_replace(table, columns=table.columns + added) if added else table)
    return _replace(plan, tables=tuple(tables))


def projected_artifacts(
    shape: Shape, system: System, dataset: Dataset, *, rows_per_entity: int
) -> ForgeArtifacts:
    """Dialect DDL + loader for the system's projection, over the ONE dataset's
    rows (never re-synthesised — the spine must agree across systems)."""
    plan = projected_plan(shape, system)
    dialect = get_dialect(system.dialect)
    rows = {t.table: dataset.tables[t.table] for t in plan.tables}
    return ForgeArtifacts(
        dialect=dialect.name,
        seed=shape.seed,
        ddl=dialect.render_ddl(plan),
        load_sql=dialect.render_loader(plan, rows, shape.seed),
        rows=rows,
    )


# ── deploy ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeployedSystem:
    system: System
    database: str
    fields: dict[str, str]
    """Connection fields in the shape ``FederationService.from_env``'s secret
    registry expects for this kind (the Postgres leg's Ontop endpoint is filled
    by slice 4 — until then it is recorded as ``jdbc_url`` only)."""
    dsn: str | None
    tables: tuple[str, ...]
    rows_loaded: int

    @property
    def registry_ready(self) -> bool:
        """Whether ``fields`` are what the secret registry accepts for this kind
        today. The Postgres leg runs through Ontop and needs an ``endpoint``,
        which only exists once slice 4 has launched an Ontop for the shape — so
        Postgres systems stay *pending* and are listed in ``live-env.json``
        under ``forge.pendingOntop`` with their JDBC URL."""
        return self.system.kind != "postgresql"


@dataclass(frozen=True)
class SkippedSystem:
    system: System
    reason: str


def _swap_database(dsn: str, database: str) -> str:
    """Point a DSN at another database, keeping scheme, credentials, host and any
    query string (``postgresql://u:p@h:5433/crm?sslmode=require`` → ``…/x?…``)."""
    parts = urllib.parse.urlsplit(dsn)
    return urllib.parse.urlunsplit(parts._replace(path=f"/{database}"))


def write_private(path: Path, text: str) -> None:
    """Write a credential-bearing file readable by the owner only (CC-7: secrets
    never travel in artifacts; these live files hold dev-stack credentials and
    are gitignored, never uploaded, and now not world-readable either)."""
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


def deploy_postgres(artifacts: ForgeArtifacts, admin_dsn: str, database: str) -> str:
    import psycopg

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {database}")
    dsn = _swap_database(admin_dsn, database)
    with psycopg.connect(dsn) as conn:
        conn.execute(artifacts.ddl)
        conn.execute(artifacts.load_sql)
        conn.commit()
    return dsn


def deploy_clickhouse(artifacts: ForgeArtifacts, admin_dsn: str, database: str) -> str:
    import clickhouse_connect
    from r2g.forge import split_sql_statements

    admin = clickhouse_connect.get_client(dsn=admin_dsn)
    admin.command(f"DROP DATABASE IF EXISTS {database}")
    admin.command(f"CREATE DATABASE {database}")
    dsn = _swap_database(admin_dsn, database)
    client = clickhouse_connect.get_client(dsn=dsn)
    for statement in split_sql_statements(artifacts.ddl) + split_sql_statements(artifacts.load_sql):
        client.command(statement)
    return dsn


def snowflake_schema_name(shape_name: str, system_name: str) -> str:
    """The Forge's schema for one system: the engine-safe name in Snowflake's
    unquoted UPPERCASE spelling (``FORGE_TWO_LEG_421_SF1``)."""
    return database_name(shape_name, system_name).upper()


def snowflake_registry_fields(targets: LiveTargets, schema: str) -> dict[str, str]:
    """What the secret registry carries for a deployed Snowflake system: the
    same account and authentication the Forge deployed with, the system's
    schema, and the fabric's read-only query role rather than the deployer role
    (CC-7: the query path never holds CREATE SCHEMA)."""
    assert targets.snowflake is not None
    fields = {k: v for k, v in targets.snowflake.items() if k in _SNOWFLAKE_REGISTRY_FIELDS}
    fields["schema"] = schema
    if targets.snowflake_query_role:
        fields["role"] = targets.snowflake_query_role
    return fields


def deploy_snowflake(
    artifacts: ForgeArtifacts,
    targets: LiveTargets,
    schema: str,
    *,
    connect: Callable[..., Any] | None = None,
) -> dict[str, str]:
    """One schema per system in the Forge's own database (``CREATE OR REPLACE``
    — a re-run replaces the previous deployment), then r2g's DDL and loader
    statement by statement, as its own Snowflake roundtrip does. Returns the
    registry fields the fabric connects with. ``connect`` is injectable for
    tests (mirrors ``snowflake.connector.connect``)."""
    from r2g.forge import split_sql_statements

    assert targets.snowflake is not None
    if connect is None:
        import snowflake.connector

        connect = snowflake.connector.connect
    database = targets.snowflake["database"]
    conn = connect(**targets.snowflake, login_timeout=60)
    try:
        cur = conn.cursor()
        cur.execute(f"CREATE OR REPLACE SCHEMA {database}.{schema}")
        cur.execute(f"USE SCHEMA {database}.{schema}")
        for statement in split_sql_statements(artifacts.ddl) + split_sql_statements(
            artifacts.load_sql
        ):
            cur.execute(statement)
    finally:
        conn.close()
    return snowflake_registry_fields(targets, schema)


def deploy_arango(
    artifacts: ForgeArtifacts, targets: LiveTargets, database: str, workdir: Path
) -> dict[str, str]:
    import arango

    assert targets.arango_url is not None
    client = arango.ArangoClient(hosts=targets.arango_url)
    sys_db = client.db("_system", username=targets.arango_user, password=targets.arango_password)
    if sys_db.has_database(database):
        sys_db.delete_database(database)
    sys_db.create_database(database)
    workdir.mkdir(parents=True, exist_ok=True)
    paths = artifacts.write_to(str(workdir))
    loader = next(p for p in paths if p.endswith(".py"))
    env = {
        **os.environ,
        "ARANGO_ENDPOINT": targets.arango_url,
        "ARANGO_DB": database,
        "ARANGO_USER": targets.arango_user,
        "ARANGO_PASSWORD": targets.arango_password,
    }
    result = subprocess.run(
        [sys.executable, loader, "--dir", str(workdir)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"arango loader failed for {database}: {result.stderr.strip()[-800:]}")
    return {
        "url": targets.arango_url,
        "database": database,
        "user": targets.arango_user,
        "password": targets.arango_password,
    }


def deploy_system(
    shape: Shape,
    system: System,
    dataset: Dataset,
    targets: LiveTargets,
    workdir: Path,
    *,
    rows_per_entity: int,
) -> DeployedSystem | SkippedSystem:
    reason = targets.missing_reason(system.dialect)
    if reason:
        return SkippedSystem(system=system, reason=reason)
    artifacts = projected_artifacts(shape, system, dataset, rows_per_entity=rows_per_entity)
    database = database_name(shape.name, system.name)
    tables = tuple(sorted(artifacts.rows))
    rows_loaded = sum(len(r) for r in artifacts.rows.values())
    system_dir = workdir / system.name
    system_dir.mkdir(parents=True, exist_ok=True)
    if system.dialect == "postgres":
        assert targets.postgres_admin_dsn is not None
        dsn = deploy_postgres(artifacts, targets.postgres_admin_dsn, database)
        artifacts.write_to(str(system_dir))
        return DeployedSystem(system, database, {"jdbc_url": dsn}, dsn, tables, rows_loaded)
    if system.dialect == "clickhouse":
        assert targets.clickhouse_admin_dsn is not None
        dsn = deploy_clickhouse(artifacts, targets.clickhouse_admin_dsn, database)
        artifacts.write_to(str(system_dir))
        return DeployedSystem(system, database, {"dsn": dsn}, dsn, tables, rows_loaded)
    if system.dialect == "arango":
        fields = deploy_arango(artifacts, targets, database, system_dir)
        return DeployedSystem(system, database, fields, None, tables, rows_loaded)
    if system.dialect == "snowflake":
        schema = snowflake_schema_name(shape.name, system.name)
        fields = deploy_snowflake(artifacts, targets, schema)
        artifacts.write_to(str(system_dir))
        # ``database`` is the schema here: Snowflake's unit of deployment for us.
        return DeployedSystem(system, schema, fields, None, tables, rows_loaded)
    return SkippedSystem(system=system, reason=f"no deployer for dialect {system.dialect!r}")


# ── introspect + export (the estate's forward pipeline) ─────────────────────


def introspect_relational(
    kind: str, dsn: str, system_name: str, *, schema_name: str
) -> tuple[dict[str, Any], str]:
    """r2g connector → Auto-Map → CSI + R2RML, exactly as ``r2g export-csi`` /
    ``export-r2rml`` do. ``source_ref`` is the system name so the source id the
    catalog derives (``<kind>:<name>``) matches the goldens' expectations."""
    from r2g.connectors.base import create_source_connector

    schema = create_source_connector(kind, dsn, schema_name=schema_name).get_schema()
    return export_estate(kind, schema, system_name)


def export_estate(kind: str, schema: Any, system_name: str) -> tuple[dict[str, Any], str]:
    """Auto-Map → CSI + R2RML from an introspected r2g ``Schema``, as ``r2g
    export-csi`` / ``export-r2rml`` do."""
    from r2g.config import ConfigManager
    from r2g.csi import mapping_to_csi, validate_csi
    from r2g.r2rml import mapping_to_r2rml

    mapping = ConfigManager.generate_default_config(schema)
    csi = mapping_to_csi(
        mapping, schema, source_type=kind, source_ref=system_name, label_policy="warn"
    )
    errors = validate_csi(csi)
    if errors:
        raise ValueError(f"{kind}:{system_name}: estate CSI failed validation: {errors}")
    return csi, mapping_to_r2rml(mapping, schema, source_type=kind)


def introspect_snowflake(
    targets: LiveTargets, schema_name: str, system_name: str
) -> tuple[dict[str, Any], str]:
    """r2g's ``SnowflakeConnector`` (``INFORMATION_SCHEMA`` + ``SHOW PRIMARY /
    IMPORTED KEYS``) → Auto-Map → CSI + R2RML for one Forge schema. The
    connector is built from a URL, which cannot carry key-pair authentication,
    so its connect parameters are then set from the validated kwargs — the same
    merge r2g's own live roundtrip performs."""
    from r2g.connectors.snowflake import SnowflakeConnector

    assert targets.snowflake is not None
    args = dict(targets.snowflake)
    query = urllib.parse.urlencode({k: args[k] for k in ("warehouse", "role") if args.get(k)})
    url = (
        f"snowflake://{urllib.parse.quote(args['user'])}@{args['account']}"
        f"/{args['database']}/{schema_name}" + (f"?{query}" if query else "")
    )
    connector = SnowflakeConnector(url, schema_name=schema_name)
    connector._connect_params = {**args, "schema": schema_name}  # key pair has no URL form
    return export_estate("snowflake", connector.get_schema(), system_name)


def introspect_arango(fields: Mapping[str, str], system_name: str) -> dict[str, Any]:
    """ASA ``analyze`` → reverse CSI, as ``deploy/arango/export_csi.py`` does
    for the demo graph (entity strategy pinned to one class per collection)."""
    from schema_analyzer.csi import to_csi, validate_csi
    from schema_analyzer.tool import run_tool

    resp = run_tool(
        {
            "contractVersion": "1",
            "operation": "analyze",
            "connection": {
                "url": fields["url"],
                "database": fields["database"],
                "username": fields["user"],
                "password": fields["password"],
            },
            "analysisOptions": {"entityStrategy": "collection"},
        }
    )
    if not resp.get("ok"):
        raise RuntimeError(f"arango:{system_name}: analyzer failed: {resp.get('error')}")
    csi = to_csi(
        resp["result"]["analysis"],
        direction="reverse",
        source={"kind": "arango", "ref": system_name},
    )
    errors = validate_csi(csi)
    if errors:
        raise ValueError(f"arango:{system_name}: estate CSI failed validation: {errors}")
    return csi


# ── declared references: what introspection cannot see ──────────────────────


def cross_system_references(shape: Shape, system: System) -> list[dict[str, str]]:
    """Relationships whose child this system owns and whose parent it does not."""
    owned = _owned(shape, system)
    return [
        {"type": r["type"], "fromEntity": r["fromEntity"], "toEntity": r["toEntity"]}
        for r in shape.relationships()
        if r["fromEntity"] in owned and r["toEntity"] not in owned
    ]


def apply_declared_references(
    csi: dict[str, Any], references: list[dict[str, str]], dataset: Dataset
) -> list[str]:
    """Add ``references`` to the CSI's relationships after checking each holds
    over the dataset (every child FK value resolves to a parent id — the D-2
    spine property; a declaration that stops being true fails loudly, as the
    demo's overlay exports do). Returns the relationship types admitted."""
    from r2g.forge import table_name

    relationships = csi["conceptualModel"].setdefault("relationships", [])
    present = {(r["type"], r["fromEntity"], r["toEntity"]) for r in relationships}
    admitted: list[str] = []
    for ref in references:
        parent_ids = {row["id"] for row in dataset.tables[table_name(ref["toEntity"])]}
        fk = foreign_key_column(ref["toEntity"])
        child_rows = dataset.tables[table_name(ref["fromEntity"])]
        dangling = [
            row[fk] for row in child_rows if row.get(fk) is not None and row[fk] not in parent_ids
        ]
        if dangling:
            raise ValueError(
                f"declared reference {ref['fromEntity']}.{fk} -> {ref['toEntity']}.id does not "
                f"hold: {len(dangling)} of {len(child_rows)} values dangle"
            )
        key = (ref["type"], ref["fromEntity"], ref["toEntity"])
        if key not in present:
            relationships.append(dict(ref))
            present.add(key)
        admitted.append(ref["type"])
    return admitted


# ── drift: fixture CSI vs estate CSI ────────────────────────────────────────


def _conceptual_view(
    csi: Mapping[str, Any],
) -> tuple[dict[str, set[str]], set[tuple[str, str, str]]]:
    model = csi.get("conceptualModel") or {}
    entities = {
        e["name"]: {owl_property_name(p["name"]) for p in e.get("properties", [])}
        for e in model.get("entities", [])
    }
    relationships = {
        (owl_property_name(r["type"]), r["fromEntity"], r["toEntity"])
        for r in model.get("relationships", [])
    }
    return entities, relationships


def conceptual_drift(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    """Differences between two CSI conceptual models, normalized through the
    CC-12 namers (the ``≡`` of ADR-0006 D-3). Empty dict = no drift."""
    exp_e, exp_r = _conceptual_view(expected)
    act_e, act_r = _conceptual_view(actual)
    drift: dict[str, Any] = {}
    if set(exp_e) != set(act_e):
        drift["entitiesMissing"] = sorted(set(exp_e) - set(act_e))
        drift["entitiesExtra"] = sorted(set(act_e) - set(exp_e))
    props: dict[str, dict[str, list[str]]] = {}
    for name in sorted(set(exp_e) & set(act_e)):
        missing, extra = sorted(exp_e[name] - act_e[name]), sorted(act_e[name] - exp_e[name])
        if missing or extra:
            props[name] = {"missing": missing, "extra": extra}
    if props:
        drift["properties"] = props
    if exp_r != act_r:
        drift["relationshipsMissing"] = sorted(exp_r - act_r)
        drift["relationshipsExtra"] = sorted(act_r - exp_r)
    return drift


# ── onboard: manifest + secret registry over estate artifacts ───────────────


@dataclass
class LiveSystemResult:
    name: str
    kind: str
    dialect: str
    database: str
    tables: tuple[str, ...]
    rows_loaded: int
    csi_path: str
    r2rml_path: str | None
    declared_references: list[str] = field(default_factory=list)
    """Cross-system relationship types applied as declared references — the
    descriptor's knowledge, not introspection's; listed so a reader can tell
    the two apart."""
    drift: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveShapeReport:
    shape: str
    status: str
    """``onboarded`` | ``executed`` | ``skipped`` | ``failed``"""
    systems: list[LiveSystemResult] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    manifest: str | None = None
    registry: str | None = None
    message: str | None = None
    adaptations: dict[str, dict[str, str]] = field(default_factory=dict)
    """Systems whose dialect was substituted because it had no live target
    (:func:`adapt_shape`) — ``{system: {from, to}}``; empty for a faithful run."""
    ontop: dict[str, Any] = field(default_factory=dict)
    """Per Postgres source: the Ontop endpoint launched for it and its startup time."""
    probe: dict[str, Any] = field(default_factory=dict)
    """CC-14 result: ``stripped`` declarations by source, and the golden
    expectations that ``flipped`` when re-derived from the probed capabilities."""
    goldens: dict[str, Any] = field(default_factory=dict)
    """``total`` / ``passed`` / ``failed`` (name + mismatches) through the real fabric."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "status": self.status,
            "systems": [vars(s) for s in self.systems],
            "skipped": self.skipped,
            "manifest": self.manifest,
            "registry": self.registry,
            "message": self.message,
            "adaptations": self.adaptations,
            "ontop": self.ontop,
            "probe": self.probe,
            "goldens": self.goldens,
        }


def _json(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def onboard(
    shape: Shape,
    deployed: list[tuple[DeployedSystem, dict[str, Any], str | None]],
    live_dir: Path,
) -> tuple[Path, Path]:
    """Write estate CSI/R2RML, build the manifest with the shape's DECLARED
    capabilities on the overlay, validate it loads, and write the secret
    registry ``from_env`` needs. Returns ``(manifest_path, registry_path)``."""
    csi_dir, r2rml_dir = live_dir / "csi", live_dir / "r2rml"
    csi_dir.mkdir(parents=True, exist_ok=True)
    r2rml_dir.mkdir(parents=True, exist_ok=True)
    overlay: dict[str, Any] = {"sources": {}}
    registry: dict[str, Any] = {"version": 1, "sources": {}}
    pending_ontop: dict[str, str] = {}
    for system_result, csi, r2rml in deployed:
        system = system_result.system
        (csi_dir / f"{system.name}.csi.json").write_text(_json(csi), encoding="utf-8")
        if r2rml is not None:
            (r2rml_dir / f"{system.kind}_{system.name}.ttl").write_text(r2rml, encoding="utf-8")
        overlay["sources"][system.source_id] = {
            "capabilities": capabilities_document(system.capabilities)
        }
        if system_result.registry_ready:
            registry["sources"][system.source_id] = {
                "kind": system.kind,
                "ref": system.name,
                "generation": f"forge-live-{shape.seed}",
                "fields": dict(system_result.fields),
            }
        else:
            pending_ontop[system.source_id] = system_result.fields.get("jdbc_url", "")
    overlay_path = live_dir / "overlay.json"
    overlay_path.write_text(_json(overlay), encoding="utf-8")
    manifest_path = live_dir / "manifest.json"
    # The artifact root is the shape's live directory, stated explicitly: the
    # builder's default walks up to pyproject.toml, which is wrong for a live
    # dir outside the repo (tests, CI scratch). from_env honours the same root
    # through CDF_CATALOG_ROOT, which the env file below sets.
    build_manifest(
        csi_dirs=[csi_dir],
        r2rml_dirs=[r2rml_dir],
        output=manifest_path,
        overlay_path=overlay_path,
        root=live_dir,
    )
    FileCatalogLoader(manifest_path, root=live_dir).load().source_catalog()
    registry_path = live_dir / "secret-registry.json"
    write_private(registry_path, _json(registry))
    write_private(
        live_dir / "live-env.json",
        _json(
            {
                "CDF_CATALOG_MANIFEST": str(manifest_path),
                "CDF_CATALOG_ROOT": str(live_dir),
                "CDF_SECRET_REGISTRY_JSON": json.dumps(registry, sort_keys=True),
                # Slice 4 launches one Ontop per shape over these JDBC URLs and
                # adds each source to the registry with its SPARQL endpoint.
                "forge": {"pendingOntop": pending_ontop},
            }
        ),
    )
    return manifest_path, registry_path


# ── the run ─────────────────────────────────────────────────────────────────

Deployer = Callable[..., DeployedSystem | SkippedSystem]


def run_live_shape(
    shape: Shape,
    dataset: Dataset,
    targets: LiveTargets,
    live_root: Path,
    *,
    rows_per_entity: int,
    deployer: Deployer = deploy_system,
    introspect_sql: Callable[..., tuple[dict[str, Any], str]] = introspect_relational,
    introspect_graph: Callable[..., dict[str, Any]] = introspect_arango,
    introspect_warehouse: Callable[..., tuple[dict[str, Any], str]] = introspect_snowflake,
    adaptations: Mapping[str, Mapping[str, str]] | None = None,
) -> LiveShapeReport:
    """deploy → introspect → drift → onboard for one shape. A shape with any
    unconfigured dialect is skipped whole (its goldens assume every leg) —
    unless the caller first ran it through :func:`adapt_shape` and passes the
    ``adaptations`` it returned, which the report then carries so no reader
    mistakes an adapted run for the committed shape."""
    report = LiveShapeReport(
        shape=shape.name,
        status="failed",
        adaptations={k: dict(v) for k, v in (adaptations or {}).items()},
    )
    live_dir = live_root / shape.name
    for system in shape.systems:
        reason = targets.missing_reason(system.dialect)
        if reason:
            report.skipped.append(
                {"system": system.name, "dialect": system.dialect, "reason": reason}
            )
    if report.skipped:
        report.status = "skipped"
        report.message = "one or more systems have no live target; goldens assume every leg"
        _write_report(live_dir, report)
        return report
    try:
        deployed: list[tuple[DeployedSystem, dict[str, Any], str | None]] = []
        for system in shape.systems:
            outcome = deployer(
                shape,
                system,
                dataset,
                targets,
                live_dir / "deploy",
                rows_per_entity=rows_per_entity,
            )
            if isinstance(outcome, SkippedSystem):
                raise RuntimeError(f"{system.name}: {outcome.reason}")
            if system.kind == "arango":
                csi, r2rml = introspect_graph(outcome.fields, system.name), None
            elif system.kind == "snowflake":
                csi, r2rml = introspect_warehouse(targets, outcome.database, system.name)
            else:
                schema_name = "public" if system.kind == "postgresql" else outcome.database
                csi, r2rml = introspect_sql(
                    system.kind, outcome.dsn, system.name, schema_name=schema_name
                )
            declared = apply_declared_references(
                csi, cross_system_references(shape, system), dataset
            )
            deployed.append((outcome, csi, r2rml))
            report.systems.append(
                LiveSystemResult(
                    name=system.name,
                    kind=system.kind,
                    dialect=system.dialect,
                    database=outcome.database,
                    tables=outcome.tables,
                    rows_loaded=outcome.rows_loaded,
                    csi_path=str(live_dir / "csi" / f"{system.name}.csi.json"),
                    r2rml_path=(
                        None
                        if r2rml is None
                        else str(live_dir / "r2rml" / f"{system.kind}_{system.name}.ttl")
                    ),
                    declared_references=declared,
                    drift=conceptual_drift(fixture_csi(shape, system), csi),
                )
            )
        manifest_path, registry_path = onboard(shape, deployed, live_dir)
        report.manifest, report.registry = str(manifest_path), str(registry_path)
        report.status = "onboarded"
    except Exception as exc:  # the report must name the failure; the CLI exits non-zero
        report.message = f"{type(exc).__name__}: {exc}"
    _write_report(live_dir, report)
    return report


def _write_report(live_dir: Path, report: LiveShapeReport) -> None:
    live_dir.mkdir(parents=True, exist_ok=True)
    (live_dir / LIVE_REPORT_FILE).write_text(_json(report.to_dict()), encoding="utf-8")
