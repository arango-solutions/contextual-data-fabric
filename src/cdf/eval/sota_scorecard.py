"""Unified, versioned evidence runner for the project SOTA scorecard.

The runner executes the existing deterministic gates and emits one compact JSON
artifact. Raw command output is deliberately not retained: reports contain
summaries and SHA-256 digests so CI can compare evidence without copying source
errors, credentials, or provider responses into another artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from cdf.connectors.redaction import redact

SCHEMA_VERSION = 2
REPORT_KIND = "cdf-sota-baseline"
SCORING_MODEL = Path(__file__).parent / "corpora" / "sota-dimensions-v1.json"
PARITY_CORPUS = Path(__file__).parent / "corpora" / "interface-parity-v1.json"
_SUMMARY_COUNT = re.compile(r"(?P<count>\d+)\s+(?P<label>passed|failed|skipped)")
_FAILED_TEST = re.compile(r"^FAILED\s+(?P<test>\S+)", re.MULTILINE)
_LIVE_ENV_KEYS = (
    "ARANGO_URL",
    "ARANGO_DB",
    "ARANGO_USER",
    "ARANGO_PASSWORD",
    "ONTOP_SPARQL_ENDPOINT",
    "ONTOP_REFORMULATE_ENDPOINT",
    "CLICKHOUSE_DSN",
    "SNOWFLAKE_ACCOUNT",
    "SNOWFLAKE_USER",
    "SNOWFLAKE_PASSWORD",
    "SNOWFLAKE_PRIVATE_KEY_FILE",
    "SNOWFLAKE_PRIVATE_KEY_FILE_PWD",
)


@dataclass(frozen=True)
class CheckSpec:
    """One executable scorecard check."""

    name: str
    command: tuple[str, ...]
    output_kind: str = "text"
    required: bool = True


@dataclass(frozen=True)
class CommandOutcome:
    """Secret-free command result used by the report builder."""

    returncode: int
    stdout: str
    stderr: str
    duration_ms: float


@dataclass(frozen=True)
class CheckResult:
    """Serializable evidence for one scorecard check."""

    name: str
    status: str
    required: bool
    command: tuple[str, ...]
    exit_code: int | None
    duration_ms: float
    summary: Mapping[str, Any]
    output_sha256: str | None


CommandRunner = Callable[[tuple[str, ...], Path, Mapping[str, str]], CommandOutcome]


def _specs(python: str, *, live: bool) -> tuple[CheckSpec, ...]:
    specs = [
        CheckSpec(
            "catalog_integrity",
            ("make", "catalog-integrity", f"PY={python}"),
        ),
        CheckSpec(
            "authorization",
            (python, "-m", "pytest", "tests/test_governance.py", "-q"),
        ),
        CheckSpec("ruff", (python, "-m", "ruff", "check", "src", "tests", "deploy")),
        CheckSpec("mypy", (python, "-m", "mypy", "src")),
        CheckSpec("unit_contracts", (python, "-m", "pytest", "tests", "-q")),
        CheckSpec(
            "nl_decomposition",
            (python, "-m", "cdf.eval.nl_eval", "--json"),
            output_kind="nl-json",
        ),
        CheckSpec(
            "resolution_safety",
            (python, "-m", "cdf.eval.resolution_eval", "--json"),
            output_kind="resolution-json",
        ),
        CheckSpec(
            "optimizer_oracle",
            (python, "-m", "cdf.eval.optimizer_oracle", "--json"),
            output_kind="optimizer-json",
        ),
        CheckSpec(
            "performance_baseline",
            (python, "-m", "cdf.eval.performance_baseline", "--json"),
            output_kind="performance-json",
        ),
        CheckSpec(
            "http_mcp_parity",
            (python, "-m", "pytest", "tests/test_interface_parity.py", "-q"),
            output_kind="parity-text",
        ),
        CheckSpec(
            "ck25_evidence_integrity",
            (
                python,
                "-m",
                "cdf.eval.ck25_eval",
                "--validate-evidence",
                "docs/evidence/ck25-gpt-4o-mini-3x.json",
            ),
            output_kind="ck25-json",
        ),
        CheckSpec(
            "ck25_live_evidence",
            (
                python,
                "-m",
                "cdf.eval.ck25_eval",
                "--validate-evidence",
                "docs/evidence/ck25-gpt-4o-mini-3x.json",
            ),
            output_kind="ck25-json",
        ),
    ]
    if live:
        specs.append(CheckSpec("live_golden", ("make", "gate", f"PY={python}")))
    else:
        specs.append(
            CheckSpec(
                "live_golden",
                (),
                output_kind="not-run",
                required=False,
            )
        )
    return tuple(specs)


def _subprocess_runner(
    command: tuple[str, ...],
    root: Path,
    environment: Mapping[str, str],
) -> CommandOutcome:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=root,
        env=dict(environment),
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandOutcome(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def _output_hash(stdout: str, stderr: str) -> str:
    digest = hashlib.sha256()
    digest.update(stdout.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(stderr.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _display_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Remove machine-specific interpreter paths from published evidence."""
    return tuple(
        "python"
        if item == sys.executable
        else "PY=python"
        if item == f"PY={sys.executable}"
        else item
        for item in command
    )


def _text_summary(stdout: str, stderr: str) -> dict[str, Any]:
    combined = "\n".join((stdout, stderr))
    counts: dict[str, int] = {}
    for match in _SUMMARY_COUNT.finditer(combined):
        counts[match.group("label")] = int(match.group("count"))
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    summary: dict[str, Any] = {"counts": counts}
    failed_tests = [match.group("test") for match in _FAILED_TEST.finditer(combined)]
    if failed_tests:
        summary["failed_tests"] = failed_tests[:50]
    if lines:
        summary["last_line"] = (redact(lines[-1]) or "")[:500]
    return summary


def _json_summary(output_kind: str, stdout: str) -> dict[str, Any]:
    document = json.loads(stdout)
    if output_kind == "nl-json":
        cases = document.get("cases") or []
        passed = sum(bool(case.get("passed")) for case in cases)
        return {
            "corpus_version": document.get("corpus_version"),
            "passed": passed,
            "total": len(cases),
            "all_passed": passed == len(cases),
        }
    if output_kind == "resolution-json":
        return {
            key: document.get(key)
            for key in (
                "corpus_version",
                "precision",
                "recall",
                "abstention_rate",
                "cross_scope_violations",
                "evidence_completeness",
                "passed",
            )
        }
    if output_kind == "optimizer-json":
        return {
            key: document.get(key)
            for key in (
                "corpus_version",
                "passed",
                "total",
                "all_passed",
                "total_feasible_plans",
                "max_objective_ratio",
            )
        }
    if output_kind == "performance-json":
        return {
            "workload_version": document.get("workload_version"),
            "evidence_class": document.get("evidence_class"),
            "query_source_count": document.get("query_source_count"),
            "all_passed": document.get("all_passed"),
            "profiles": [
                {
                    "profile": profile.get("profile"),
                    "source_delay_ms": profile.get("source_delay_ms"),
                    "planning_p95_ms": (profile.get("planning_latency") or {}).get(
                        "p95_ms"
                    ),
                    "request_p50_ms": (profile.get("request_latency") or {}).get(
                        "p50_ms"
                    ),
                    "request_p95_ms": (profile.get("request_latency") or {}).get(
                        "p95_ms"
                    ),
                    "throughput_qps": profile.get("throughput_qps"),
                    "bytes_processed": profile.get("bytes_processed"),
                    "cost_usd": profile.get("cost_usd"),
                    "passed": profile.get("passed"),
                }
                for profile in document.get("profiles") or []
            ],
        }
    if output_kind == "ck25-json":
        return {
            key: document.get(key)
            for key in (
                "valid",
                "model",
                "completed_repetitions",
                "total_case_evaluations",
                "passed",
                "pass_rate",
                "latency_p50_ms",
                "latency_p95_ms",
                "llm_calls",
                "prompt_tokens",
                "completion_tokens",
                "cost_usd",
                "report_sha256",
            )
        }
    raise ValueError(f"unsupported JSON output kind: {output_kind}")


def _run_check(
    spec: CheckSpec,
    *,
    root: Path,
    environment: Mapping[str, str],
    command_runner: CommandRunner,
) -> CheckResult:
    if spec.output_kind == "not-run":
        return CheckResult(
            name=spec.name,
            status="not_run",
            required=spec.required,
            command=(),
            exit_code=None,
            duration_ms=0.0,
            summary={"reason": "run with --live to execute the external-source golden gate"},
            output_sha256=None,
        )
    outcome = command_runner(spec.command, root, environment)
    try:
        if spec.output_kind.endswith("-json"):
            summary = _json_summary(spec.output_kind, outcome.stdout)
        else:
            summary = _text_summary(outcome.stdout, outcome.stderr)
            if spec.output_kind == "parity-text":
                corpus = json.loads(PARITY_CORPUS.read_text(encoding="utf-8"))
                summary.update(
                    corpus_version=corpus.get("corpus_version"),
                    total_cases=len(corpus.get("cases") or []),
                    all_passed=outcome.returncode == 0,
                )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        summary = {"parse_error": redact(str(exc))}
    required_summary_flags = {
        "nl-json": "all_passed",
        "resolution-json": "passed",
        "optimizer-json": "all_passed",
        "performance-json": "all_passed",
        "ck25-json": "valid",
        "parity-text": "all_passed",
    }
    summary_flag = required_summary_flags.get(spec.output_kind)
    summary_passed = "parse_error" not in summary and (
        summary_flag is None or summary.get(summary_flag) is True
    )
    return CheckResult(
        name=spec.name,
        status="passed" if outcome.returncode == 0 and summary_passed else "failed",
        required=spec.required,
        command=_display_command(spec.command),
        exit_code=outcome.returncode,
        duration_ms=round(outcome.duration_ms, 3),
        summary=summary,
        output_sha256=_output_hash(outcome.stdout, outcome.stderr),
    )


def _package_versions() -> dict[str, str | None]:
    packages = (
        "contextual-data-fabric",
        "rdflib",
        "fastapi",
        "mcp",
        "PyJWT",
        "arango-sparql-py",
        "arango-query-core",
        "arangodb-schema-analyzer",
        "clickhouse-connect",
        "snowflake-connector-python",
    )
    result: dict[str, str | None] = {}
    for package in packages:
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


def _git_metadata(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ("git", *args),
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    return {
        "commit": run("rev-parse", "HEAD") or None,
        "branch": run("branch", "--show-current") or None,
        "dirty": bool(run("status", "--porcelain")),
    }


def _offline_environment(environment: Mapping[str, str]) -> dict[str, str]:
    result = dict(environment)
    for key in _LIVE_ENV_KEYS:
        result.pop(key, None)
    return result


def _report_hash(report: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        report,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_scoring_model(path: Path = SCORING_MODEL) -> dict[str, Any]:
    """Load and validate the machine-readable scorecard dimension registry."""

    document = json.loads(path.read_text(encoding="utf-8"))
    dimensions = document.get("dimensions")
    if not isinstance(dimensions, list) or len(dimensions) != 12:
        raise ValueError("SOTA scoring model must define exactly 12 dimensions")

    seen: set[str] = set()
    scored: list[dict[str, Any]] = []
    for item in dimensions:
        dimension_id = item.get("id")
        label = item.get("label")
        weight = item.get("weight")
        level = item.get("level")
        if not isinstance(dimension_id, str) or not dimension_id or dimension_id in seen:
            raise ValueError("SOTA dimension ids must be unique non-empty strings")
        if not isinstance(label, str) or not label:
            raise ValueError(f"SOTA dimension {dimension_id!r} must have a label")
        if not isinstance(weight, int) or weight <= 0:
            raise ValueError(f"SOTA dimension {dimension_id!r} has an invalid weight")
        if not isinstance(level, int) or not 0 <= level <= 5:
            raise ValueError(f"SOTA dimension {dimension_id!r} has an invalid level")
        seen.add(dimension_id)
        scored.append(
            {
                "id": dimension_id,
                "label": label,
                "weight": weight,
                "level": level,
                "contribution": round(weight * level / 5, 3),
            }
        )

    total_weight = sum(item["weight"] for item in scored)
    if total_weight != 100:
        raise ValueError(f"SOTA dimension weights must sum to 100, got {total_weight}")
    return {
        "schema_version": document.get("schema_version"),
        "rubric_version": document.get("rubric_version"),
        "effective_date": document.get("effective_date"),
        "score": round(sum(item["contribution"] for item in scored), 3),
        "max_score": 100,
        "dimensions": scored,
    }


def build_scorecard(
    *,
    root: Path,
    live: bool = False,
    command_runner: CommandRunner = _subprocess_runner,
    generated_at: datetime | None = None,
    environment: Mapping[str, str] | None = None,
    git_metadata: Mapping[str, Any] | None = None,
    package_versions: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Run all baseline checks and return a versioned JSON-safe report."""

    timestamp = generated_at or datetime.now(timezone.utc)
    inherited = environment if environment is not None else os.environ
    offline_environment = _offline_environment(inherited)
    specs = _specs(sys.executable, live=live)
    checks = tuple(
        _run_check(
            spec,
            root=root,
            environment=(
                dict(inherited)
                if live and spec.name == "live_golden"
                else offline_environment
            ),
            command_runner=command_runner,
        )
        for spec in specs
    )
    required_passed = all(
        check.status == "passed" for check in checks if check.required
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "generated_at": timestamp.astimezone(timezone.utc).isoformat(),
        "mode": "live" if live else "offline",
        "passed": required_passed,
        "pass_semantics": (
            "all required evidence checks completed successfully; "
            "this is not a SOTA promotion result"
        ),
        "scoring": load_scoring_model(),
        "git": dict(git_metadata) if git_metadata is not None else _git_metadata(root),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": (
                dict(package_versions)
                if package_versions is not None
                else _package_versions()
            ),
        },
        "checks": [asdict(check) for check in checks],
    }
    payload["report_sha256"] = _report_hash(payload)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the unified CDF SOTA evidence baseline",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="repository root (default: current directory)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="also run make gate against configured live sources",
    )
    parser.add_argument("--output", help="optional JSON output path")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit compact rather than indented JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    report = build_scorecard(root=root, live=args.live)
    rendered = json.dumps(
        report,
        indent=None if args.compact else 2,
        sort_keys=True,
    )
    if args.output:
        output = Path(args.output)
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
