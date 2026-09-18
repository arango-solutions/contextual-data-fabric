"""Forge live mode against the LOCAL compose stacks (Postgres/Ontop, ArangoDB,
ClickHouse) — the same engines CI's ``live-local`` job stands up.

Gated on ``CDF_FORGE_LIVE=1`` so the offline suite never needs Docker. Proves,
for one locally deployable shape: deploy → the estate's own introspection →
CSI/R2RML export → manifest that loads like ``from_env`` → **zero conceptual
drift** between the fixture CSI and what RSA/r2g/ASA saw in the databases.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CDF_FORGE_LIVE") != "1",
    reason="set CDF_FORGE_LIVE=1 with the compose stacks up (make up)",
)
r2g_forge = pytest.importorskip("r2g.forge")

from cdf.eval.forge.dataset import synthesize  # noqa: E402
from cdf.eval.forge.live import LiveTargets, run_live_shape  # noqa: E402
from cdf.eval.forge.sampler import FAMILIES, sample_shape  # noqa: E402

LOCAL = {"postgres", "clickhouse", "arango"}


def _local_shapes():
    """Every family once, first seed whose dialects are all local."""
    out = []
    for family in FAMILIES:
        for seed in range(1, 300):
            shape = sample_shape(seed, family)
            if {s.dialect for s in shape.systems} <= LOCAL:
                out.append(shape)
                break
    return out


@pytest.mark.parametrize("shape", _local_shapes(), ids=lambda s: s.name)
def test_shape_onboards_through_the_estate_with_no_drift(shape, tmp_path: Path) -> None:
    ds = synthesize(shape, rows_per_entity=6)
    report = run_live_shape(shape, ds, LiveTargets.from_env(), tmp_path, rows_per_entity=6)
    assert report.status == "onboarded", report.message
    assert {s.name for s in report.systems} == {s.name for s in shape.systems}
    for system in report.systems:
        assert system.rows_loaded > 0
        assert system.drift == {}, f"{system.name} ({system.kind}) drift: {system.drift}"
        owner = shape.system(system.name)
        expected_declared = [
            r["type"]
            for r in shape.relationships()
            if shape.owner[r["fromEntity"]] == owner.name
            and shape.owner[r["toEntity"]] != owner.name
        ]
        assert system.declared_references == expected_declared
    manifest = json.loads(Path(report.manifest).read_text(encoding="utf-8"))
    assert {s["sourceId"] for s in manifest["sources"]} == {s.source_id for s in shape.systems}


# ── slice 4: execute through the real fabric (needs docker for the Ontop leg) ─


def _docker_available() -> bool:
    import shutil
    import subprocess

    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="docker not available for the per-shape Ontop")
def test_a_shape_with_a_postgres_leg_executes_its_goldens_through_the_fabric(
    tmp_path: Path,
) -> None:
    """Ontop launched per Postgres leg, registry completed, strict-startup
    service, CC-14 probe (a ClickHouse system that DECLARED GROUP BY is
    stripped — its executor cannot aggregate — and its goldens flip to
    refusals), then every re-derived golden passes on the real fabric."""
    from cdf.eval.forge.live_execute import OntopConfig, execute_shape

    shape = next(s for s in _local_shapes() if any(x.dialect == "postgres" for x in s.systems))
    ds = synthesize(shape, rows_per_entity=6)
    report = run_live_shape(shape, ds, LiveTargets.from_env(), tmp_path, rows_per_entity=6)
    assert report.status == "onboarded", report.message
    report = execute_shape(
        shape,
        ds,
        tmp_path / shape.name,
        report,
        base_env=dict(os.environ),
        ontop_cfg=OntopConfig.from_env(os.environ),
    )
    assert report.status == "executed", report.message
    assert set(report.ontop) == {s.source_id for s in shape.systems if s.kind == "postgresql"}
    assert report.goldens["failed"] == [], report.goldens["failed"]
    assert report.goldens["passed"] == report.goldens["total"] > 0
    for source_id in report.probe["stripped"]:
        kind = source_id.split(":", 1)[0]
        assert kind in {"clickhouse", "snowflake"}, (
            f"probe stripped {source_id}: only native legs may"
        )


# ── the real Snowflake account (needs SNOWFLAKE_* and setup_forge.sql) ─────────

SNOWFLAKE = bool(os.getenv("SNOWFLAKE_ACCOUNT"))


def _snowflake_shape():
    """First shape whose systems are all deployable here and include Snowflake."""
    for family in ("two_leg", "chain", "hub"):
        for seed in range(1, 300):
            shape = sample_shape(seed, family)
            dialects = {s.dialect for s in shape.systems}
            if "snowflake" in dialects and dialects <= LOCAL | {"snowflake"}:
                return shape
    raise AssertionError("no shape with a snowflake system in seeds 1..299")


@pytest.mark.skipif(not SNOWFLAKE, reason="set SNOWFLAKE_* (.env) for the Snowflake live target")
@pytest.mark.skipif(not _docker_available(), reason="docker not available for the per-shape Ontop")
def test_a_shape_with_a_snowflake_system_deploys_and_executes_through_the_real_account(
    tmp_path: Path,
) -> None:
    """The Forge deploys one schema into CDF_FORGE as the deployer role; r2g's
    SnowflakeConnector introspects it back; the fabric queries it as the
    read-only role through the native Snowflake leg — no substitution."""
    from cdf.eval.forge.live_execute import OntopConfig, execute_shape

    targets = LiveTargets.from_env()
    assert targets.missing_reason("snowflake") is None, targets.snowflake_reason
    shape = _snowflake_shape()
    ds = synthesize(shape, rows_per_entity=6)
    report = run_live_shape(shape, ds, targets, tmp_path, rows_per_entity=6)
    assert report.status == "onboarded", report.message
    assert report.adaptations == {}
    sf = [r for r in report.systems if r.kind == "snowflake"]
    assert sf and all(r.rows_loaded > 0 and r.drift == {} for r in sf), [r.drift for r in sf]
    report = execute_shape(
        shape,
        ds,
        tmp_path / shape.name,
        report,
        base_env=dict(os.environ),
        ontop_cfg=OntopConfig.from_env(os.environ),
    )
    assert report.status == "executed", report.message
    assert report.goldens["failed"] == [], report.goldens["failed"]
    assert report.goldens["passed"] == report.goldens["total"] > 0
