"""The Forge CLI (``python -m cdf.eval.forge``): ``emit``, ``suite`` and ``live``
end to end through :func:`cdf.eval.forge.suite.main`. Live mode runs against
the fake deployer/introspectors from ``test_forge_live`` — no databases — so
the flags, the listing marks, the summary file and the exit codes are asserted
here rather than only in the gated live job."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

r2g_forge = pytest.importorskip("r2g.forge")

from tests.test_forge_live import (  # noqa: E402
    _fake_deployer,
    _fake_graph_introspect,
    _fake_sql_introspect,
)

from cdf.eval.forge import live, suite  # noqa: E402
from cdf.eval.forge.descriptor import DESCRIPTOR_FILE  # noqa: E402
from cdf.eval.forge.live import LiveTargets  # noqa: E402
from cdf.eval.forge.suite import main, sample_suite  # noqa: E402


def _fake_run_live_shape(shape, dataset, targets, live_root, *, rows_per_entity, adaptations=None):
    """The real orchestration with fakes for the parts that need an engine —
    same signature as :func:`cdf.eval.forge.live.run_live_shape`."""
    return live.run_live_shape(
        shape,
        dataset,
        targets,
        live_root,
        rows_per_entity=rows_per_entity,
        deployer=_fake_deployer,
        introspect_sql=_fake_sql_introspect(shape),
        introspect_graph=_fake_graph_introspect(shape),
        adaptations=adaptations,
    )


@pytest.fixture
def local_targets(monkeypatch: pytest.MonkeyPatch) -> LiveTargets:
    targets = LiveTargets.from_env({})
    monkeypatch.setattr(suite.LiveTargets, "from_env", classmethod(lambda cls, env=None: targets))
    monkeypatch.setattr(suite, "run_live_shape", _fake_run_live_shape)
    return targets


def _mixed_suite() -> tuple[int, int, list[str]]:
    """A (shapes, seed) whose suite has both all-local and snowflake shapes."""
    for seed in range(400, 600):
        shapes = sample_suite(shapes=5, seed=seed)
        snow = [s.name for s in shapes if any(x.dialect == "snowflake" for x in s.systems)]
        if 0 < len(snow) < len(shapes):
            return 5, seed, snow
    raise AssertionError("no mixed suite in seeds 400..599")


def test_live_skips_snowflake_shapes_by_name_without_the_flag(
    local_targets: LiveTargets, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    n, seed, snow = _mixed_suite()
    rc = main(["live", "--shapes", str(n), "--seed", str(seed), "--live-out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0  # a skip is a configuration fact, not a fabric regression
    summary = json.loads((tmp_path / "live-summary.json").read_text(encoding="utf-8"))
    by_name = {s["shape"]: s for s in summary["shapes"]}
    assert len(by_name) == n and summary["mode"] == "live" and summary["seed"] == seed
    for name in snow:
        assert by_name[name]["status"] == "skipped" and f"SKIP  {name}" in out
        assert by_name[name]["skipped"][0]["dialect"] == "snowflake"
    for name in set(by_name) - set(snow):
        assert by_name[name]["status"] == "onboarded" and f"LIVE  {name}" in out
        assert by_name[name]["adaptations"] == {}
    assert f"{n - len(snow)} onboarded (0 executed, 0/0 goldens), {len(snow)} skipped" in out


def test_live_substitute_unavailable_runs_the_topology_and_marks_the_run(
    local_targets: LiveTargets, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    n, seed, snow = _mixed_suite()
    rc = main(
        [
            "live",
            "--shapes",
            str(n),
            "--seed",
            str(seed),
            "--live-out",
            str(tmp_path),
            "--substitute-unavailable",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0
    summary = json.loads((tmp_path / "live-summary.json").read_text(encoding="utf-8"))
    by_name = {s["shape"]: s for s in summary["shapes"]}
    assert all(s["status"] == "onboarded" for s in by_name.values()), by_name
    for name in snow:
        assert f"LIVE~ {name}" in out  # never mistaken for the committed shape
        assert by_name[name]["adaptations"]
        assert all(a["from"] == "snowflake" for a in by_name[name]["adaptations"].values())
        assert all(
            local_targets.missing_reason(a["to"]) is None
            for a in by_name[name]["adaptations"].values()
        )
        # the on-disk report says the same
        report = json.loads((tmp_path / name / live.LIVE_REPORT_FILE).read_text(encoding="utf-8"))
        assert report["adaptations"] == by_name[name]["adaptations"]
    for name in set(by_name) - set(snow):
        assert f"LIVE  {name}" in out and by_name[name]["adaptations"] == {}
    assert f"{n} onboarded (0 executed, 0/0 goldens), 0 skipped, 0 failed" in out


def test_live_only_filters_by_shape_name_and_a_failure_is_non_zero(
    local_targets: LiveTargets,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n, seed, snow = _mixed_suite()
    local = next(s.name for s in sample_suite(shapes=n, seed=seed) if s.name not in snow)
    rc = main(
        [
            "live",
            "--shapes",
            str(n),
            "--seed",
            str(seed),
            "--live-out",
            str(tmp_path),
            "--only",
            local,
        ]
    )
    summary = json.loads((tmp_path / "live-summary.json").read_text(encoding="utf-8"))
    assert rc == 0 and [s["shape"] for s in summary["shapes"]] == [local]

    def boom(shape, system, dataset, targets, workdir, *, rows_per_entity):
        raise RuntimeError(f"deploy exploded for {system.name}")

    def failing(shape, dataset, targets, live_root, *, rows_per_entity, adaptations=None):
        return live.run_live_shape(
            shape, dataset, targets, live_root, rows_per_entity=rows_per_entity, deployer=boom
        )

    monkeypatch.setattr(suite, "run_live_shape", failing)
    rc = main(
        [
            "live",
            "--shapes",
            str(n),
            "--seed",
            str(seed),
            "--live-out",
            str(tmp_path),
            "--only",
            local,
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1 and f"FAIL  {local}" in out and "deploy exploded" in out
    summary = json.loads((tmp_path / "live-summary.json").read_text(encoding="utf-8"))
    assert summary["shapes"][0]["status"] == "failed"


def test_emit_writes_one_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["emit", "--family", "two_leg", "--seed", "7", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("emitted ")
    dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(dirs) == 1 and (dirs[0] / DESCRIPTOR_FILE).exists()


def test_suite_emits_checks_determinism_and_reads_the_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "signoff.yaml"
    ledger.write_text("forgeSignoffVersion: 1\ngoldens: {}\n", encoding="utf-8")
    out_dir = tmp_path / "shapes"
    rc = main(
        [
            "suite",
            "--shapes",
            "2",
            "--seed",
            "421",
            "--out",
            str(out_dir),
            "--check-determinism",
            "--signoff",
            str(ledger),
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "forge-suite: 2 shapes emitted" in out
    report = json.loads((out_dir / "suite-report.json").read_text(encoding="utf-8"))
    assert [s["deterministic"] for s in report["shapes"]] == [True, True]
    assert report["signoff"]["signed"] == 0 and report["signoff"]["unknown"] == []
    assert report["mode"] == "fixture"
    # the ledger is read, never written
    assert ledger.read_text(encoding="utf-8") == "forgeSignoffVersion: 1\ngoldens: {}\n"
