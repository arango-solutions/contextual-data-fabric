"""``make forge-suite``: sample → emit → (re-emit and byte-compare) → run.

Fixture mode only in this slice. Exit status is non-zero when any golden
fails, when determinism is violated, or when a descriptor does not validate.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdf.catalog.capabilities import capabilities_document
from cdf.eval.forge.dataset import Dataset, synthesize
from cdf.eval.forge.descriptor import (
    CATALOG_FILE,
    DESCRIPTOR_FILE,
    GOLDENS_DIR,
    ONTOLOGY_FILE,
    SYSTEMS_DIR,
    descriptor_document,
    dump_descriptor,
    load_descriptor,
)
from cdf.eval.forge.fixture_csi import fixture_csi
from cdf.eval.forge.live import DEFAULT_LIVE_DIR, LiveTargets, adapt_shape, run_live_shape
from cdf.eval.forge.live_execute import OntopConfig, execute_shape
from cdf.eval.forge.oracle import compose_goldens, expected_catalog
from cdf.eval.forge.sampler import FAMILIES, Shape, sample_shape
from cdf.eval.forge.signoff import DEFAULT_SIGNOFF_FILE, load_signoff, signoff_status
from cdf.eval.golden import GoldenOutcome, run_golden

DEFAULT_ROWS_PER_ENTITY = 12


def _json(doc: Any) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@dataclass(frozen=True)
class EmittedShape:
    shape: Shape
    directory: Path
    goldens: list[dict[str, Any]]
    dataset: Dataset


def emit_shape(
    shape: Shape, out_dir: Path, *, rows_per_entity: int = DEFAULT_ROWS_PER_ENTITY
) -> EmittedShape:
    """Write one shape directory: descriptor, ontology, expected catalog, goldens, fixture CSI."""
    dataset = synthesize(shape, rows_per_entity=rows_per_entity)
    goldens = compose_goldens(shape, dataset)
    d = out_dir / shape.name
    if d.exists():
        shutil.rmtree(d)
    (d / GOLDENS_DIR).mkdir(parents=True)
    (d / SYSTEMS_DIR).mkdir()

    (d / ONTOLOGY_FILE).write_text(_json(shape.ontology), encoding="utf-8")
    (d / CATALOG_FILE).write_text(_json(expected_catalog(shape)), encoding="utf-8")
    for case in goldens:
        (d / GOLDENS_DIR / f"{case['name']}.json").write_text(_json(case), encoding="utf-8")
    for system in shape.systems:
        (d / SYSTEMS_DIR / f"{system.name}.csi.json").write_text(
            _json(fixture_csi(shape, system)), encoding="utf-8"
        )
    descriptor = descriptor_document(
        name=shape.name,
        family=shape.family,
        seed=shape.seed,
        rows_per_entity=rows_per_entity,
        partition_map=shape.partition_map(),
        systems={
            s.name: {
                "dialect": s.dialect,
                "kind": s.kind,
                "capabilities": capabilities_document(s.capabilities),
            }
            for s in shape.systems
        },
        generator=dataset.generator,
        golden_names=[c["name"] for c in goldens],
    )
    (d / DESCRIPTOR_FILE).write_text(dump_descriptor(descriptor), encoding="utf-8")
    return EmittedShape(shape=shape, directory=d, goldens=goldens, dataset=dataset)


def sample_suite(*, shapes: int, seed: int) -> list[Shape]:
    """``shapes`` shapes cycling through the families, seeds derived from ``seed``."""
    out = []
    for i in range(shapes):
        family = FAMILIES[i % len(FAMILIES)]
        out.append(sample_shape(seed + i, family))
    return out


def _tree_differs(a: Path, b: Path) -> list[str]:
    """Paths whose bytes differ (or exist on one side only)."""
    diffs: list[str] = []
    cmp = filecmp.dircmp(a, b)

    def walk(c: filecmp.dircmp, prefix: str) -> None:
        for name in c.left_only + c.right_only + c.diff_files:
            diffs.append(f"{prefix}{name}")
        for name in c.funny_files:
            diffs.append(f"{prefix}{name} (unreadable)")
        for sub, subcmp in c.subdirs.items():
            walk(subcmp, f"{prefix}{sub}/")

    walk(cmp, "")
    return diffs


def check_determinism(shape: Shape, reference: Path, *, rows_per_entity: int) -> list[str]:
    """Re-emit into a temp dir and byte-compare with ``reference`` (D-5)."""
    with tempfile.TemporaryDirectory() as tmp:
        again = emit_shape(shape, Path(tmp), rows_per_entity=rows_per_entity)
        return _tree_differs(reference, again.directory)


def committed_suite_drift(shapes_dir: Path) -> dict[str, list[str]]:
    """Paths that differ between each committed shape and a fresh emit from its
    own descriptor (family, seed, rowsPerEntity) — ``{}`` when the committed
    suite is exactly what the current code produces.

    The git-free twin of CI's drift step (PR #34 review, item 1): the suite is
    a versioned artifact of generator + seed, so a pin bump that changes
    generated names must fail here, not drift silently.
    """
    drift: dict[str, list[str]] = {}
    for descriptor_path in sorted(shapes_dir.glob(f"*/{DESCRIPTOR_FILE}")):
        doc = load_descriptor(descriptor_path)
        shape = sample_shape(doc["seed"], doc["family"], name=doc["name"])
        diffs = check_determinism(
            shape, descriptor_path.parent, rows_per_entity=doc["scale"]["rowsPerEntity"]
        )
        if diffs:
            drift[doc["name"]] = diffs
    return drift


def run_shape(emitted: EmittedShape) -> list[GoldenOutcome]:
    return [run_golden(case) for case in emitted.goldens]


def _cmd_suite(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # The ledger is read, never written: sign-off lives outside the generated
    # tree (PR #34 review, item 2). A missing ledger is a configuration error.
    ledger_path = Path(args.signoff)
    ledger = load_signoff(ledger_path)
    shapes = sample_suite(shapes=args.shapes, seed=args.seed)
    failures = 0
    total_goldens = 0
    emitted_names: list[str] = []
    report: dict[str, Any] = {"seed": args.seed, "shapes": [], "mode": "fixture"}
    for shape in shapes:
        emitted = emit_shape(shape, out, rows_per_entity=args.rows_per_entity)
        load_descriptor(emitted.directory / DESCRIPTOR_FILE)  # validates
        names = [c["name"] for c in emitted.goldens]
        emitted_names.extend(names)
        entry: dict[str, Any] = {
            "name": shape.name,
            "family": shape.family,
            "systems": len(shape.systems),
            "signedOff": sorted(n for n in names if n in ledger and ledger[n].signed_off),
        }
        if args.check_determinism:
            diffs = check_determinism(
                shape, emitted.directory, rows_per_entity=args.rows_per_entity
            )
            entry["deterministic"] = not diffs
            if diffs:
                failures += 1
                print(f"NONDETERMINISTIC {shape.name}: {diffs}")
        if args.run:
            outcomes = run_shape(emitted)
            total_goldens += len(outcomes)
            failed = [o for o in outcomes if not o.passed]
            failures += len(failed)
            entry["goldens"] = len(outcomes)
            entry["failed"] = [{"name": o.name, "mismatches": list(o.mismatches)} for o in failed]
            for o in outcomes:
                mark = "PASS" if o.passed else "FAIL"
                print(
                    f"{mark}  {o.name}" + ("" if o.passed else f"  {'; '.join(o.mismatches)[:300]}")
                )
        report["shapes"].append(entry)
    status = signoff_status(ledger, emitted_names)
    report["signoff"] = {
        "ledger": str(ledger_path),
        "signed": len(status.signed),
        "unsigned": len(status.unsigned),
        "unknown": list(status.unknown),
    }
    if status.unknown:
        failures += 1
        print(
            "SIGNOFF-UNKNOWN: the ledger names goldens this run did not emit — "
            "a signed-off golden vanished or was renamed by regeneration: "
            + ", ".join(status.unknown)
        )
    (out / "suite-report.json").write_text(_json(report), encoding="utf-8")
    verb = "checked" if args.run else "emitted"
    print(
        f"\nforge-suite: {len(shapes)} shapes {verb}, {total_goldens} goldens, "
        f"{failures} failure(s) — fixture mode; sign-off: {len(status.signed)} signed, "
        f"{len(status.unsigned)} unsigned (ledger {ledger_path})"
    )
    return 1 if failures else 0


def _cmd_live(args: argparse.Namespace) -> int:
    """Live mode (S2/S3): deploy → introspect → drift → onboard each sampled
    shape through the estate. Shapes with an unconfigured dialect are skipped
    by name — or, with ``--substitute-unavailable``, run on a configured
    dialect with the substitution named in the report and marked ``~`` in the
    listing; a failed shape makes the exit status non-zero, a skipped one does
    not (skips are a configuration fact, not a fabric regression)."""
    import os

    targets = LiveTargets.from_env()
    ontop_cfg = OntopConfig.from_env(os.environ)
    live_root = Path(args.live_out)
    shapes = sample_suite(shapes=args.shapes, seed=args.seed)
    if args.only:
        shapes = [s for s in shapes if s.name in set(args.only)]
    failures = 0
    summary: dict[str, Any] = {"seed": args.seed, "mode": "live", "shapes": []}
    for shape in shapes:
        adaptations: dict[str, dict[str, str]] = {}
        if args.substitute_unavailable:
            shape, adaptations = adapt_shape(shape, targets)
        dataset = synthesize(shape, rows_per_entity=args.rows_per_entity)
        report = run_live_shape(
            shape,
            dataset,
            targets,
            live_root,
            rows_per_entity=args.rows_per_entity,
            adaptations=adaptations,
        )
        if args.execute and report.status == "onboarded":
            report = execute_shape(
                shape,
                dataset,
                live_root / shape.name,
                report,
                base_env=os.environ,
                ontop_cfg=ontop_cfg,
                keep_ontop=args.keep_ontop,
            )
        summary["shapes"].append(report.to_dict())
        drift = {r.name: r.drift for r in report.systems if r.drift}
        golden_failures = len(report.goldens.get("failed", [])) if report.goldens else 0
        if report.status == "failed" or golden_failures:
            failures += 1
        mark = {"onboarded": "LIVE ", "executed": "RUN  ", "skipped": "SKIP ", "failed": "FAIL "}[
            report.status
        ]
        if report.adaptations:
            mark = mark[:-1] + "~"  # adapted: a substituted dialect, never the original shape
        if report.status == "executed":
            g = report.goldens
            stripped = ", ".join(report.probe.get("stripped", {})) or "none"
            adapted = "".join(
                f"; {n}: {a['from']}→{a['to']}" for n, a in sorted(report.adaptations.items())
            )
            detail = (
                f"{g['passed']}/{g['total']} goldens passed; probe stripped: {stripped}; "
                f"flipped: {len(report.probe.get('flipped', []))}{adapted}"
            )
        else:
            detail = report.message or (
                f"{len(report.systems)} systems onboarded"
                + (f"; drift: {drift}" if drift else "; no drift")
            )
        print(f"{mark} {shape.name}  {detail}")
    live_root.mkdir(parents=True, exist_ok=True)
    (live_root / "live-summary.json").write_text(_json(summary), encoding="utf-8")
    onboarded = sum(1 for r in summary["shapes"] if r["status"] in ("onboarded", "executed"))
    executed = sum(1 for r in summary["shapes"] if r["status"] == "executed")
    skipped = sum(1 for r in summary["shapes"] if r["status"] == "skipped")
    passed = sum(r["goldens"].get("passed", 0) for r in summary["shapes"] if r.get("goldens"))
    total = sum(r["goldens"].get("total", 0) for r in summary["shapes"] if r.get("goldens"))
    print(
        f"\nforge-live: {onboarded} onboarded ({executed} executed, {passed}/{total} goldens), "
        f"{skipped} skipped, {failures} failed (reports under {live_root})"
    )
    return 1 if failures else 0


def _cmd_emit(args: argparse.Namespace) -> int:
    shape = sample_shape(args.seed, args.family, name=args.name)
    emitted = emit_shape(shape, Path(args.out), rows_per_entity=args.rows_per_entity)
    print(f"emitted {emitted.directory} ({len(emitted.goldens)} goldens)")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cdf.eval.forge", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("suite", help="sample N shapes, emit, check determinism, run goldens")
    s.add_argument("--shapes", type=int, default=10)
    s.add_argument("--seed", type=int, default=421)
    s.add_argument("--out", default="deploy/forge/shapes")
    s.add_argument("--rows-per-entity", type=int, default=DEFAULT_ROWS_PER_ENTITY)
    s.add_argument("--check-determinism", action="store_true")
    s.add_argument("--run", action="store_true")
    s.add_argument(
        "--signoff",
        default=str(DEFAULT_SIGNOFF_FILE),
        help="hand-maintained sign-off ledger (read only; never regenerated)",
    )
    s.set_defaults(func=_cmd_suite)
    e = sub.add_parser("emit", help="emit one shape")
    e.add_argument("--family", choices=FAMILIES, required=True)
    e.add_argument("--seed", type=int, required=True)
    e.add_argument("--name")
    e.add_argument("--out", default="deploy/forge/shapes")
    e.add_argument("--rows-per-entity", type=int, default=DEFAULT_ROWS_PER_ENTITY)
    e.set_defaults(func=_cmd_emit)
    live = sub.add_parser(
        "live", help="deploy → introspect → onboard sampled shapes through the estate"
    )
    live.add_argument("--shapes", type=int, default=10)
    live.add_argument("--seed", type=int, default=421)
    live.add_argument("--rows-per-entity", type=int, default=DEFAULT_ROWS_PER_ENTITY)
    live.add_argument("--live-out", default=str(DEFAULT_LIVE_DIR))
    live.add_argument("--only", action="append", help="run only these shape names")
    live.add_argument(
        "--execute",
        action="store_true",
        help="after onboarding: an Ontop per Postgres leg, the CC-14 probe, and the "
        "goldens through the real fabric (needs docker + the compose stacks)",
    )
    live.add_argument(
        "--substitute-unavailable",
        action="store_true",
        help="run shapes whose dialects lack a live target by substituting a configured "
        "dialect (reported by name) instead of skipping them — so chain/hub topologies run",
    )
    live.add_argument(
        "--keep-ontop",
        action="store_true",
        help="leave the per-shape Ontop containers running for inspection",
    )
    live.set_defaults(func=_cmd_live)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
