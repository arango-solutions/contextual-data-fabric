"""Forge live mode, slice 4 (execute) — engine-free: fakes for docker, the
service and the probe; the real planner/executor/grounding run underneath the
fake service so the goldens are genuinely evaluated."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

r2g_forge = pytest.importorskip("r2g.forge")

from tests.test_forge_live import (  # noqa: E402
    _fake_deployer,
    _fake_graph_introspect,
    _fake_sql_introspect,
    _local_shape,
)

from cdf.connectors.secrets import ConnectorRef, EnvSecretResolver  # noqa: E402
from cdf.eval.forge.dataset import synthesize  # noqa: E402
from cdf.eval.forge.live import LiveTargets, run_live_shape  # noqa: E402
from cdf.eval.forge.live_execute import (  # noqa: E402
    OntopConfig,
    OntopInstance,
    complete_registry,
    execute_shape,
    flipped_expectations,
    jdbc_from_dsn,
    launch_ontop,
    parse_docker_port,
    probe_shape,
    render_ontop_properties,
    wait_ready,
)
from cdf.eval.forge.oracle import compose_goldens  # noqa: E402
from cdf.query import execute_plan, ground, partition_query  # noqa: E402
from cdf.query.catalog import SourceCatalog, source_ref_from_csi  # noqa: E402
from cdf.query.executor import SourceResult  # noqa: E402

CFG = OntopConfig()  # rendering-only tests; anything that launches uses the `cfg` fixture


@pytest.fixture
def cfg(tmp_path: Path) -> OntopConfig:
    """An OntopConfig whose JDBC directory holds a driver jar. The real one is
    gitignored and fetched by `make jdbc`, so it is absent on the offline CI
    runner; these tests never start a container and must not depend on it."""
    jdbc = tmp_path / "jdbc"
    jdbc.mkdir()
    (jdbc / "postgresql.jar").write_bytes(b"presence is what launch checks")
    return OntopConfig(jdbc_dir=jdbc)


# ── pieces ──────────────────────────────────────────────────────────────────


def test_jdbc_and_properties_rendering() -> None:
    url, user, pw = jdbc_from_dsn("postgresql://cdf:s3cret@127.0.0.1:5433/forge_x_pg1", CFG)
    assert url == "jdbc:postgresql://postgres:5432/forge_x_pg1" and (user, pw) == ("cdf", "s3cret")
    props = render_ontop_properties(url, user, pw)
    assert "jdbc.url=jdbc:postgresql://postgres:5432/forge_x_pg1" in props
    assert "jdbc.driver=org.postgresql.Driver" in props and "inferDefaultDatatype=true" in props
    with pytest.raises(ValueError, match="no database"):
        jdbc_from_dsn("postgresql://cdf:cdf@127.0.0.1:5433/", CFG)


def test_parse_docker_port_takes_the_host_port() -> None:
    assert parse_docker_port("127.0.0.1:55012\n") == 55012
    assert parse_docker_port("0.0.0.0:49153\n[::]:49153\n") == 49153
    with pytest.raises(ValueError):
        parse_docker_port("")


class _Docker:
    """Records docker invocations; answers `port` and `ps`."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[0] == "port":
            return "127.0.0.1:55001\n"
        if args[0] == "ps":
            return ""
        return ""


def test_launch_ontop_runs_the_container_on_the_compose_network(
    tmp_path: Path, cfg: OntopConfig
) -> None:
    docker = _Docker()
    inst = launch_ontop("forge-ontop-x-pg1", tmp_path, cfg, runner=docker)
    run = next(c for c in docker.calls if c[0] == "run")
    assert "--network" in run and run[run.index("--network") + 1] == cfg.network
    assert f"{tmp_path.resolve()}:/opt/ontop/input:ro" in run and run[-1] == cfg.image
    assert f"{cfg.jdbc_dir.resolve()}:/opt/ontop/jdbc:ro" in run
    assert inst.endpoint == "http://127.0.0.1:55001/sparql"
    assert inst.reformulate_endpoint.endswith("/ontop/reformulate")


def test_launch_ontop_refuses_without_the_jdbc_driver(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="make jdbc"):
        launch_ontop("x", tmp_path, OntopConfig(jdbc_dir=tmp_path / "nope"), runner=_Docker())


def test_wait_ready_polls_until_200_and_names_the_timeout() -> None:
    inst = OntopInstance("n", "http://x/sparql", "http://x/ontop/reformulate")
    attempts = {"n": 0}

    def flaky(endpoint: str) -> int:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("refused")
        return 200

    assert wait_ready(inst, 10, runner=_Docker(), sleep=lambda s: None, probe=flaky) >= 0
    assert attempts["n"] == 3
    with pytest.raises(TimeoutError, match="not ready"):
        wait_ready(inst, 0.01, runner=_Docker(), sleep=lambda s: None, probe=lambda e: 503)


# ── registry + probe + re-derivation ────────────────────────────────────────


def _onboarded(tmp_path: Path):
    shape = _local_shape("chain")  # chain: distinct dialects, so it has a postgres leg
    assert any(s.kind == "postgresql" for s in shape.systems)
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
    return shape, ds, report


def test_complete_registry_adds_the_postgres_legs_with_valid_fields(tmp_path: Path) -> None:
    shape, _ds, report = _onboarded(tmp_path)
    live_dir = tmp_path / shape.name
    pg = [s for s in shape.systems if s.kind == "postgresql"]
    instances = {
        s.source_id: OntopInstance(f"o-{s.name}", f"http://127.0.0.1:5{i}000/sparql", "http://r")
        for i, s in enumerate(pg)
    }
    registry = complete_registry(live_dir, shape, instances)
    resolver = EnvSecretResolver({"CDF_SECRET_REGISTRY_JSON": json.dumps(registry)})
    for s in shape.systems:
        resolved = resolver.resolve(ConnectorRef(s.source_id, s.kind, s.name))
        assert resolved is not None, s.source_id
    env_doc = json.loads((live_dir / "live-env.json").read_text())
    assert env_doc["forge"]["pendingOntop"] == {}
    assert set(env_doc["forge"]["ontop"]) == set(instances)


class _Executor:
    def __init__(self, ok: bool) -> None:
        self.ok = ok

    def execute(self, subquery):
        if not self.ok:
            raise RuntimeError("GROUP BY not supported here")
        return SourceResult(rows=({"n": 1},), native_query="probe")


class _Service:
    """Just enough of FederationService for probe_shape."""

    def __init__(self, shape, failing: set[str]):
        self.catalog = SourceCatalog.from_csi_documents(
            [
                c
                for c in (
                    __import__("cdf.eval.forge.fixture_csi", fromlist=["fixture_csi"]).fixture_csi(
                        shape, s
                    )
                    for s in shape.systems
                )
            ]
        )
        self.executors = {
            s.source_id: _Executor(ok=s.source_id not in failing) for s in shape.systems
        }


def test_probe_strips_a_failing_declaration_and_rederives_the_goldens() -> None:
    shape = _local_shape("chain")
    ds = synthesize(shape, rows_per_entity=4)
    # Make one system DECLARE GROUP BY, then have its live executor refuse it.
    from dataclasses import replace

    from cdf.catalog.capabilities import AggregationCapability, SourceCapabilities

    victim = shape.systems[0]
    declared = replace(
        victim,
        capabilities=SourceCapabilities(
            aggregation=AggregationCapability(group_by=True, having=True, count_distinct=True)
        ),
    )
    shape = replace(shape, systems=(declared, *shape.systems[1:]))
    probed, stripped = probe_shape(shape, _Service(shape, failing={victim.source_id}))
    assert set(stripped) == {victim.source_id} and stripped[victim.source_id]
    assert probed.system(victim.name).capabilities.aggregation.group_by is False
    for other in shape.systems[1:]:
        assert probed.system(other.name).capabilities == other.capabilities
    flipped = flipped_expectations(compose_goldens(shape, ds), compose_goldens(probed, ds))
    victim_entities = {n for n, s in shape.owner.items() if s == victim.name}
    aggregating = [
        c
        for c in compose_goldens(probed, ds)
        if c["family"] == "single_leg_aggregation"
        and c["name"].rsplit("--", 1)[1] in victim_entities
    ]
    assert set(flipped) >= {c["name"] for c in aggregating}, "the victim's aggregation goldens flip"
    for c in aggregating:
        assert "unsupported_contains" in c["expect"]


# ── the whole execute step, with fakes ──────────────────────────────────────


class _FixtureService:
    """A service whose legs are the fixture executors the goldens carry, so the
    REAL planner → executor → grounding decides every answer."""

    def __init__(self, shape, dataset, catalog_root: Path):
        from cdf.catalog.model import FileCatalogLoader
        from cdf.eval.golden import _FixtureExecutor

        loaded = FileCatalogLoader(catalog_root / "manifest.json", root=catalog_root).load()
        self.catalog = loaded.source_catalog()
        self.executors = {s.source_id: _Executor(ok=True) for s in shape.systems}
        self._fixtures = {
            c["question"]: {
                source_ref_from_csi(src["csi"]).source_id: _FixtureExecutor(src["data"])
                for src in c["sources"]
            }
            for c in compose_goldens(shape, dataset)
        }

    def close(self) -> None:
        """Mirrors :meth:`cdf.service.app.FederationService.close`."""
        self.closed = getattr(self, "closed", 0) + 1

    def federate_sparql(self, sparql: str, *, allow_partial: bool = False):
        plan = partition_query(sparql, self.catalog)  # may raise UnsupportedQueryError
        return ground(execute_plan(plan, self._fixtures[sparql]), allow_partial=allow_partial)


def test_execute_shape_launches_ontop_probes_and_runs_the_goldens(
    tmp_path: Path, cfg: OntopConfig
) -> None:
    shape, ds, report = _onboarded(tmp_path)
    live_dir = tmp_path / shape.name
    docker = _Docker()
    pg_sources = [s.source_id for s in shape.systems if s.kind == "postgresql"]
    services: list[_FixtureService] = []

    def factory(env):
        services.append(_FixtureService(shape, ds, live_dir))
        return services[-1]

    report = execute_shape(
        shape,
        ds,
        live_dir,
        report,
        base_env={},
        ontop_cfg=cfg,
        runner=docker,
        service_factory=factory,
        ready=lambda inst, t: 0.5,
    )
    assert report.status == "executed", report.message
    assert [s.closed for s in services] == [1], "every service built is drained exactly once"
    assert set(report.ontop) == set(pg_sources)
    runs = [c for c in docker.calls if c[0] == "run"]
    assert len(runs) == len(pg_sources), "one Ontop per Postgres leg"
    removes = [c for c in docker.calls if c[0] == "rm"]
    assert len(removes) >= len(pg_sources), "containers are removed unless kept"
    assert report.probe == {"stripped": {}, "flipped": []}
    assert report.goldens["total"] == len(compose_goldens(shape, ds))
    assert report.goldens["failed"] == [], report.goldens["failed"]
    written = json.loads((live_dir / "live-report.json").read_text())
    assert (
        written["status"] == "executed"
        and written["goldens"]["passed"] == written["goldens"]["total"]
    )
    # The mapping Ontop was fed is the R2RML r2g exported for that database.
    for sid in pg_sources:
        name = sid.split(":", 1)[1]
        assert (live_dir / "ontop" / name / "mapping.ttl").read_text() == (
            live_dir / "r2rml" / f"postgresql_{name}.ttl"
        ).read_text()
        props = live_dir / "ontop" / name / "ontop.properties"
        assert "jdbc.url=jdbc:postgresql://postgres:5432/" in props.read_text()
        # Read by uid 999 inside the container through a bind mount: an
        # owner-only file stalls Ontop on Linux (CI, 2026-09-18). Must stay
        # readable to others, unlike the registry files our own process reads.
        assert props.stat().st_mode & 0o044 == 0o044, oct(props.stat().st_mode)
    for name in ("secret-registry.json", "live-env.json"):
        assert (live_dir / name).stat().st_mode & 0o777 == 0o600, name


def test_execute_shape_keep_ontop_leaves_containers_and_a_failure_is_named(
    tmp_path: Path, cfg: OntopConfig
) -> None:
    shape, ds, report = _onboarded(tmp_path)
    live_dir = tmp_path / shape.name
    docker = _Docker()

    def exploding_factory(env):
        raise RuntimeError("strict startup requires connector configuration")

    out = execute_shape(
        shape,
        ds,
        live_dir,
        report,
        base_env={},
        ontop_cfg=cfg,
        runner=docker,
        service_factory=exploding_factory,
        ready=lambda inst, t: 0.0,
        keep_ontop=True,
    )
    assert out.status == "failed" and "strict startup" in (out.message or "")
    assert not [
        c
        for c in docker.calls
        if c[0] == "rm" and c[1] == "-f" and len(c) == 3 and c[2].startswith("forge-ontop")
    ]
