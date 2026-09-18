"""Forge live mode, slice 4 — **execute** a shape through the real fabric.

After :mod:`.live` has deployed, introspected, declared and onboarded a shape,
this module closes the chain:

1. **An Ontop per Postgres system.** The fabric's Postgres leg is Ontop, and an
   Ontop instance serves one R2RML mapping over one JDBC database. Each pending
   Postgres system gets its own container on the compose network, fed the
   R2RML r2g exported for exactly that database; its SPARQL endpoint completes
   the secret registry.
2. **The real service.** ``FederationService.from_env`` over the live manifest,
   artifact root and registry, strict startup — every source must be wired or
   the shape fails by name.
3. **The CC-14 probe.** Each system's *declared* capabilities are probed against
   its live executor. A declaration the engine cannot honour is stripped from
   the manifest (rebuilt, service rebuilt) **and the goldens are re-derived
   from the probed capabilities**: fixture mode expects what the descriptor
   declared; live mode expects what the probe established. The report counts
   the expectations that flipped, by name.
4. **The goldens**, through ``run_golden_live`` — real planner, real legs, real
   grounding — with pass/fail per case in the report.

Containers are removed when the shape finishes unless ``keep_ontop`` is set
(PJ's system-testing mode: leave the estate up to inspect).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from cdf.catalog.builder import build_manifest
from cdf.catalog.capabilities import (
    AggregationCapability,
    capabilities_document,
    probe_capabilities,
)
from cdf.eval.forge.dataset import Dataset
from cdf.eval.forge.live import LiveShapeReport, _json, write_private
from cdf.eval.forge.oracle import compose_goldens
from cdf.eval.forge.sampler import Shape
from cdf.eval.golden import GoldenOutcome, run_golden_live
from cdf.query.types import SourceRef

Runner = Callable[[list[str]], str]
"""Runs a ``docker …`` command and returns its stdout (injectable for tests)."""


def _docker(args: list[str]) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"docker {shlex.join(args[:2])} failed ({result.returncode}): "
            f"{result.stderr.strip()[-600:]}"
        )
    return result.stdout


# ── Ontop per shape ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OntopConfig:
    """How to run a per-shape Ontop. Defaults match ``deploy/ontop``."""

    image: str = "ontop/ontop:5.5.0"
    network: str = "cdf-ontop_default"
    jdbc_host: str = "postgres"
    jdbc_port: int = 5432
    jdbc_dir: Path = Path("deploy/ontop/jdbc")
    bind_host: str = "127.0.0.1"
    startup_timeout_s: float = 150.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> OntopConfig:
        return cls(
            image=env.get("CDF_FORGE_ONTOP_IMAGE", cls.image),
            network=env.get("CDF_FORGE_ONTOP_NETWORK", cls.network),
            jdbc_host=env.get("CDF_FORGE_PG_JDBC_HOST", cls.jdbc_host),
            jdbc_port=int(env.get("CDF_FORGE_PG_JDBC_PORT", str(cls.jdbc_port))),
            jdbc_dir=Path(env.get("CDF_FORGE_ONTOP_JDBC_DIR", str(cls.jdbc_dir))),
            startup_timeout_s=float(env.get("CDF_FORGE_ONTOP_TIMEOUT", str(cls.startup_timeout_s))),
        )


def render_ontop_properties(jdbc_url: str, user: str, password: str) -> str:
    return (
        f"jdbc.url={jdbc_url}\n"
        f"jdbc.user={user}\n"
        f"jdbc.password={password}\n"
        "jdbc.driver=org.postgresql.Driver\n"
        "ontop.inferDefaultDatatype=true\n"
    )


def jdbc_from_dsn(dsn: str, cfg: OntopConfig) -> tuple[str, str, str]:
    """``postgresql://user:pw@host:port/db`` (host view) → JDBC URL on the
    compose network plus the credentials. Returns ``(jdbc_url, user, password)``."""
    parts = urllib.parse.urlsplit(dsn)
    database = parts.path.lstrip("/")
    if not database:
        raise ValueError(f"no database in DSN {dsn!r}")
    return (
        f"jdbc:postgresql://{cfg.jdbc_host}:{cfg.jdbc_port}/{database}",
        urllib.parse.unquote(parts.username or ""),
        urllib.parse.unquote(parts.password or ""),
    )


@dataclass(frozen=True)
class OntopInstance:
    name: str
    endpoint: str
    reformulate_endpoint: str


def parse_docker_port(output: str) -> int:
    """``docker port <name> 8080`` → the host port (first mapping)."""
    for line in output.splitlines():
        line = line.strip()
        if line:
            return int(line.rsplit(":", 1)[1])
    raise ValueError(f"no port mapping in {output!r}")


def launch_ontop(
    name: str, input_dir: Path, cfg: OntopConfig, *, runner: Runner = _docker
) -> OntopInstance:
    """Start one Ontop over ``input_dir/{mapping.ttl, ontop.properties}``."""
    jar_dir = cfg.jdbc_dir.resolve()
    if not any(jar_dir.glob("*.jar")):
        raise FileNotFoundError(
            f"no JDBC driver under {jar_dir} — run `make jdbc` (deploy/ontop/jdbc/postgresql.jar)"
        )
    runner(["rm", "-f", name]) if _exists(name, runner) else None
    runner(
        [
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            cfg.network,
            "-p",
            f"{cfg.bind_host}::8080",
            "-v",
            f"{input_dir.resolve()}:/opt/ontop/input:ro",
            "-v",
            f"{jar_dir}:/opt/ontop/jdbc:ro",
            "-e",
            "ONTOP_MAPPING_FILE=/opt/ontop/input/mapping.ttl",
            "-e",
            "ONTOP_PROPERTIES_FILE=/opt/ontop/input/ontop.properties",
            "-e",
            "ONTOP_DEV_MODE=true",
            "-e",
            "ONTOP_CORS_ALLOWED_ORIGINS=*",
            cfg.image,
        ]
    )
    port = parse_docker_port(runner(["port", name, "8080"]))
    base = f"http://{cfg.bind_host}:{port}"
    return OntopInstance(
        name=name, endpoint=f"{base}/sparql", reformulate_endpoint=f"{base}/ontop/reformulate"
    )


def _exists(name: str, runner: Runner) -> bool:
    try:
        return bool(runner(["ps", "-aq", "--filter", f"name=^{name}$"]).strip())
    except RuntimeError:
        return False


def wait_ready(
    instance: OntopInstance,
    timeout_s: float,
    *,
    runner: Runner = _docker,
    sleep: Callable[[float], None] = time.sleep,
    probe: Callable[[str], int] | None = None,
) -> float:
    """Poll the SPARQL endpoint until it answers a trivial query; returns the
    seconds it took. On timeout, raises with the container's last log lines."""
    query = urllib.parse.urlencode({"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"}).encode()

    def _default_probe(endpoint: str) -> int:
        request = urllib.request.Request(
            endpoint, data=query, headers={"Accept": "application/sparql-results+json"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 — local
            return int(response.status)

    check = probe or _default_probe
    started = time.monotonic()
    last: str = "not yet contacted"
    while time.monotonic() - started < timeout_s:
        try:
            if check(instance.endpoint) == 200:
                return time.monotonic() - started
            last = "non-200 response"
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last = f"{type(exc).__name__}: {exc}"
        sleep(1.0)
    logs = ""
    try:
        logs = runner(["logs", "--tail", "40", instance.name])
    except RuntimeError as exc:
        logs = f"(docker logs failed: {exc})"
    raise TimeoutError(
        f"Ontop {instance.name} not ready after {timeout_s:.0f}s ({last}); "
        f"last logs:\n{logs[-2000:]}"
    )


def stop_ontop(instance: OntopInstance, *, runner: Runner = _docker) -> None:
    try:
        runner(["rm", "-f", instance.name])
    except RuntimeError:
        pass  # --rm already reaped it; nothing to report


# ── registry, service, probe ────────────────────────────────────────────────


def complete_registry(
    live_dir: Path, shape: Shape, endpoints: Mapping[str, OntopInstance]
) -> dict[str, Any]:
    """Add the Postgres legs (now served by Ontop) to the secret registry and
    the env file written by :func:`~cdf.eval.forge.live.onboard`."""
    registry = json.loads((live_dir / "secret-registry.json").read_text(encoding="utf-8"))
    env_doc = json.loads((live_dir / "live-env.json").read_text(encoding="utf-8"))
    pending: dict[str, str] = env_doc.get("forge", {}).get("pendingOntop", {})
    for source_id, instance in endpoints.items():
        system = shape.system(source_id.split(":", 1)[1])
        registry["sources"][source_id] = {
            "kind": "postgresql",
            "ref": system.name,
            "generation": f"forge-live-{shape.seed}",
            "fields": {
                "endpoint": instance.endpoint,
                "reformulate_endpoint": instance.reformulate_endpoint,
            },
        }
        pending.pop(source_id, None)
    env_doc["CDF_SECRET_REGISTRY_JSON"] = json.dumps(registry, sort_keys=True)
    env_doc["forge"] = {
        "pendingOntop": pending,
        "ontop": {k: v.endpoint for k, v in endpoints.items()},
    }
    write_private(live_dir / "secret-registry.json", _json(registry))
    write_private(live_dir / "live-env.json", _json(env_doc))
    return registry


def service_env(
    live_dir: Path, registry: Mapping[str, Any], base: Mapping[str, str]
) -> dict[str, str]:
    """The environment ``FederationService.from_env`` needs for this shape."""
    return {
        **base,
        "CDF_CATALOG_MANIFEST": str(live_dir / "manifest.json"),
        "CDF_CATALOG_ROOT": str(live_dir),
        "CDF_SECRET_REGISTRY_JSON": json.dumps(registry, sort_keys=True),
        "CDF_NL_DISABLED": "1",
        "CDF_STRICT_STARTUP": "1",
    }


def build_service(env: Mapping[str, str]) -> Any:
    from cdf.service.app import FederationService  # service imports this package's neighbours

    return FederationService.from_env(env)


def probe_shape(shape: Shape, service: Any) -> tuple[Shape, dict[str, list[str]]]:
    """CC-14 over the live executors: a declared capability whose probe fails
    does not exist. Returns the shape as the probe established it, plus what
    was stripped, by source."""
    stripped: dict[str, list[str]] = {}
    systems = []
    for system in shape.systems:
        executor = service.executors.get(system.source_id)
        if executor is None:
            raise RuntimeError(f"{system.source_id}: no executor after strict startup")
        owned = next(e["name"] for e in shape.entities() if shape.owner[e["name"]] == system.name)
        failures = probe_capabilities(
            SourceRef(source_id=system.source_id, kind=system.kind, ref=system.name),
            system.capabilities,
            executor,
            service.catalog.iri(owned),
        )
        if failures:
            stripped[system.source_id] = failures
            systems.append(
                replace(
                    system,
                    capabilities=replace(system.capabilities, aggregation=AggregationCapability()),
                )
            )
        else:
            systems.append(system)
    return replace(shape, systems=tuple(systems)), stripped


def rewrite_manifest_capabilities(live_dir: Path, shape: Shape) -> None:
    """Rebuild the manifest with the (probed) capabilities on the overlay."""
    overlay_path = live_dir / "overlay.json"
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    for system in shape.systems:
        overlay["sources"].setdefault(system.source_id, {})["capabilities"] = capabilities_document(
            system.capabilities
        )
    overlay_path.write_text(_json(overlay), encoding="utf-8")
    build_manifest(
        csi_dirs=[live_dir / "csi"],
        r2rml_dirs=[live_dir / "r2rml"],
        output=live_dir / "manifest.json",
        overlay_path=overlay_path,
        root=live_dir,
    )


def flipped_expectations(declared: list[dict[str, Any]], probed: list[dict[str, Any]]) -> list[str]:
    """Golden names whose ``expect`` changed between the declared and probed derivations."""
    before = {c["name"]: c["expect"] for c in declared}
    return sorted(
        name
        for name, expect in ((c["name"], c["expect"]) for c in probed)
        if before.get(name) != expect
    )


def run_goldens_live(cases: list[dict[str, Any]], service: Any) -> list[GoldenOutcome]:
    """Forge goldens carry conceptual SPARQL under ``question``; the live runner
    must take it as SPARQL, not as natural language."""
    return [run_golden_live({**case, "sparql": case["question"]}, service) for case in cases]


# ── the execute step ────────────────────────────────────────────────────────


def execute_shape(
    shape: Shape,
    dataset: Dataset,
    live_dir: Path,
    report: LiveShapeReport,
    *,
    base_env: Mapping[str, str],
    ontop_cfg: OntopConfig,
    keep_ontop: bool = False,
    runner: Runner = _docker,
    service_factory: Callable[[Mapping[str, str]], Any] = build_service,
    ready: Callable[[OntopInstance, float], float] | None = None,
) -> LiveShapeReport:
    """Ontop per Postgres leg → registry → service → probe → re-derived goldens → run."""
    if report.status != "onboarded":
        return report
    env_doc = json.loads((live_dir / "live-env.json").read_text(encoding="utf-8"))
    pending: dict[str, str] = env_doc.get("forge", {}).get("pendingOntop", {})
    instances: dict[str, OntopInstance] = {}
    service: Any = None
    wait = ready or (lambda inst, t: wait_ready(inst, t, runner=runner))
    try:
        for source_id, dsn in sorted(pending.items()):
            system_name = source_id.split(":", 1)[1]
            input_dir = live_dir / "ontop" / system_name
            input_dir.mkdir(parents=True, exist_ok=True)
            r2rml = (live_dir / "r2rml" / f"postgresql_{system_name}.ttl").read_text(
                encoding="utf-8"
            )
            (input_dir / "mapping.ttl").write_text(r2rml, encoding="utf-8")
            jdbc_url, user, password = jdbc_from_dsn(dsn, ontop_cfg)
            # NOT write_private: the container runs as uid 999 (`ontop`) and reads
            # this file through a bind mount, so on Linux a 0600 file owned by the
            # host user is unreadable to it and Ontop never comes up (CI, 2026-09-18).
            # It holds the compose stack's dev password exactly as the committed
            # deploy/ontop/input/ontop.properties does; the live dir is gitignored.
            (input_dir / "ontop.properties").write_text(
                render_ontop_properties(jdbc_url, user, password), encoding="utf-8"
            )
            instance = launch_ontop(
                f"forge-ontop-{shape.name}-{system_name}".lower(),
                input_dir,
                ontop_cfg,
                runner=runner,
            )
            instances[source_id] = instance
        startup: dict[str, float] = {
            sid: round(wait(inst, ontop_cfg.startup_timeout_s), 1)
            for sid, inst in instances.items()
        }
        registry = complete_registry(live_dir, shape, instances)
        service = service_factory(service_env(live_dir, registry, base_env))

        declared_goldens = compose_goldens(shape, dataset)
        probed_shape, stripped = probe_shape(shape, service)
        if stripped:
            # The manifest changed under the service; rebuild it from the
            # rewritten manifest — after draining the first one's source clients.
            rewrite_manifest_capabilities(live_dir, probed_shape)
            service.close()
            service = None
            service = service_factory(service_env(live_dir, registry, base_env))
        goldens = compose_goldens(probed_shape, dataset)
        outcomes = run_goldens_live(goldens, service)
        failed = [o for o in outcomes if not o.passed]
        report.ontop = {
            sid: {"endpoint": i.endpoint, "startupSeconds": startup[sid]}
            for sid, i in instances.items()
        }
        report.probe = {
            "stripped": stripped,
            "flipped": flipped_expectations(declared_goldens, goldens),
        }
        report.goldens = {
            "total": len(outcomes),
            "passed": len(outcomes) - len(failed),
            "failed": [{"name": o.name, "mismatches": list(o.mismatches)} for o in failed],
        }
        report.status = "executed"
        report.message = None if not failed else f"{len(failed)} golden(s) failed"
    except Exception as exc:  # named in the report; the CLI exits non-zero
        report.status = "failed"
        report.message = f"{type(exc).__name__}: {exc}"
    finally:
        if service is not None:
            service.close()
        if not keep_ontop:
            for instance in instances.values():
                stop_ontop(instance, runner=runner)
        (live_dir / "live-report.json").write_text(_json(report.to_dict()), encoding="utf-8")
    return report
