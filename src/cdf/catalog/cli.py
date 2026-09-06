"""Command-line interface for catalog manifest build/load/export/validate."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .builder import build_manifest, export_catalog, validate_manifest
from .collisions import label_report, load_allowlist, render_report
from .model import load_manifest

#: Allowlist auto-discovered next to the manifest when --allow-collisions is omitted.
_ALLOWLIST_FILENAME = "label-collisions-allow.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cdf-catalog")
    subcommands = parser.add_subparsers(dest="command", required=True)

    build = subcommands.add_parser("build", help="build a deterministic manifest")
    build.add_argument("--csi-dir", action="append", type=Path)
    build.add_argument("--r2rml-dir", action="append", type=Path)
    build.add_argument("--overlay", type=Path)
    build.add_argument("--output", type=Path, default=Path("deploy/catalog/manifest.json"))
    build.add_argument("--root", type=Path)

    load = subcommands.add_parser("load", help="load and summarize a manifest")
    load.add_argument("manifest", type=Path)
    load.add_argument("--root", type=Path)

    validate = subcommands.add_parser("validate", help="validate manifest and artifacts")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--root", type=Path)
    validate.add_argument(
        "--fail-on-label-collisions",
        action="store_true",
        help="exit non-zero when the catalog carries UNEXPECTED (non-allowlisted) "
        "label collisions (default: report as a warning). Promote to a gate once the "
        "catalog is clean apart from allowlisted collisions.",
    )
    validate.add_argument(
        "--allow-collisions",
        type=Path,
        help=f"path to an intentional-collision allowlist (default: {_ALLOWLIST_FILENAME} "
        "beside the manifest, if present). Allowlisted collisions are reported but do "
        "not trip --fail-on-label-collisions.",
    )

    export = subcommands.add_parser("export", help="export validated artifacts")
    export.add_argument("manifest", type=Path)
    export.add_argument("target", type=Path)
    export.add_argument("--root", type=Path)

    probe = subcommands.add_parser(
        "probe",
        help="CC-14 onboarding check: run declared capabilities against the LIVE "
        "executors (env-configured, as the engine wires them); a declared "
        "capability whose probe fails does not exist",
    )
    probe.add_argument("manifest", type=Path)
    probe.add_argument("--root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            csi_dirs = args.csi_dir or [Path("deploy/csi")]
            r2rml_dirs = args.r2rml_dir or [
                Path("deploy/r2rml"),
                Path("deploy/ontop/input"),
            ]
            document = build_manifest(
                csi_dirs=csi_dirs,
                r2rml_dirs=r2rml_dirs,
                output=args.output,
                overlay_path=args.overlay,
                root=args.root,
            )
            print(
                json.dumps(
                    {
                        "manifest": str(args.output),
                        "generation": document["generation"],
                        "sources": len(document["sources"]),
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "load":
            loaded = load_manifest(args.manifest, root=args.root)
            print(
                json.dumps(
                    {
                        "generation": loaded.manifest.generation,
                        "contentHash": loaded.manifest.content_hash,
                        "sources": [item.source_id for item in loaded.manifest.sources],
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "validate":
            loaded = validate_manifest(args.manifest, root=args.root)
            print(f"valid: {args.manifest}")
            allow_path = args.allow_collisions
            if allow_path is None:
                candidate = args.manifest.parent / _ALLOWLIST_FILENAME
                if candidate.is_file():
                    allow_path = candidate
            allowed: dict[str, str] = {}
            if allow_path is not None:
                allowed = load_allowlist(allow_path)
                print(
                    f"allowlist: {allow_path} ({len(allowed)} intentional collision(s))",
                    file=sys.stderr,
                )
            report = label_report(loaded, allowed=allowed)
            if not report.is_empty():
                print("warning: " + render_report(report), file=sys.stderr)
            if args.fail_on_label_collisions and report.unexpected_collisions:
                print(
                    f"cdf-catalog: {len(report.unexpected_collisions)} unexpected label "
                    "collision(s) (--fail-on-label-collisions; allowlisted collisions "
                    "excluded)",
                    file=sys.stderr,
                )
                return 1
        elif args.command == "probe":
            loaded = load_manifest(args.manifest, root=args.root)
            failures = _probe_manifest(loaded)
            for line in failures:
                print(f"PROBE FAIL  {line}", file=sys.stderr)
            if failures:
                print(
                    f"cdf-catalog: {len(failures)} declared capability(ies) failed "
                    "their live probe (CC-14: a declared capability whose probe "
                    "fails does not exist — fix the manifest or the source)",
                    file=sys.stderr,
                )
                return 1
            print(f"probed: every consulted capability verified live ({args.manifest})")
        elif args.command == "export":
            outputs = export_catalog(args.manifest, args.target, root=args.root)
            print(json.dumps([str(path) for path in outputs], sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"cdf-catalog: {exc}", file=sys.stderr)
        return 2
    return 0


def _probe_manifest(loaded) -> list[str]:
    """Probe each source's consulted declarations through the real executors.

    Builds the engine exactly as production does (``FederationService.from_env``
    — credentials stay in the engine env per CC-7); imports are function-local
    because the service layer imports this package at module level.
    """
    from cdf.query.types import SourceRef
    from cdf.service.app import FederationService

    from .capabilities import default_capabilities_for_kind, probe_capabilities

    service = FederationService.from_env()
    catalog = service.catalog
    failures: list[str] = []
    for source in loaded.manifest.sources:
        declared = source.capabilities or default_capabilities_for_kind(source.kind)
        executor = service.executors.get(source.source_id)
        if executor is None:
            failures.append(
                f"{source.source_id}: no executor configured in this environment "
                "(cannot verify its declared capabilities)"
            )
            continue
        ref = SourceRef(source_id=source.source_id, kind=source.kind, ref=source.ref)
        concept = catalog.iri(source.concepts[0])
        failures.extend(probe_capabilities(ref, declared, executor, concept))
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
