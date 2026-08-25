"""Golden seed-question regression harness (M10 / F1).

A golden case is a declarative JSON document describing one federated question,
the sources that answer it (each a ``CSI`` document + fixture rows), and the
expected outcome. :func:`run_golden` drives the real M5 pipeline
(:func:`~cdf.query.partition_query` → :func:`~cdf.query.execute_plan` →
:func:`~cdf.query.ground`) with fixture-backed executors and diffs the produced
:class:`~cdf.query.grounding.AnswerEnvelope` against the expectations.

Case schema (only the keys you assert on are checked)::

    {
      "name": "...",
      "question": "PREFIX ... SELECT ...",
      "allow_partial": false,                       # optional, default false
      "sources": [
        { "csi": { ...CSI v1 document... },
          "data": { "rows": [ {"var": value, ...}, ... ],
                    "native_query": "SELECT ...",   # optional
                    "source_objects": ["public.orders"],  # optional
                    "as_of": "2026-07-15T...",      # optional
                    "fail": false } }               # optional, simulate a downed leg
      ],
      "expect": {
        "status": "grounded",                       # optional
        "bindings": [ {"name": "Acme"} ],           # optional, compared as a bag
        "sources_touched": ["postgresql:crm"],      # optional, the OK legs
        "failed_sources": ["arango:tickets"],       # optional
        "citations": [ {"source_id": "...", "source_objects": ["..."]} ],  # optional
        "reconciliation": true,                     # optional — the locked dual-graph
                                                    #   contract: >=1 arango AND >=1
                                                    #   non-arango citation
        "anchor_kind": "postgresql",                # optional — the locked anchor
                                                    #   contract (Q7/Q15): EVERY
                                                    #   citation is of this kind
        "min_rows": 1,                              # optional — fail a grounded-
                                                    #   but-empty result (rows vary
                                                    #   by environment; shape doesn't)
        "refusal_contains": ["name"],               # optional substrings
        "unsupported_contains": ["aggregation"]     # optional — expects a PLANNER
                                                    #   refusal (UnsupportedQueryError,
                                                    #   the HTTP 422 class); substrings
                                                    #   matched against the error text.
                                                    #   When set, envelope keys are
                                                    #   ignored (there is no envelope);
                                                    #   an ACCEPTED query fails the case.
      }
    }

The ``reconciliation`` / ``anchor_kind`` expectations encode the customer-context
eval-lock contracts (see the P1 close-out plan WP-P1.6): the gate asserts a
*contract* — grounded, reconciled, cited, refusing where refusing is correct —
never exact answer text.

For live gating (the pre-demo ``make gate``), :func:`run_golden_live` runs a
case through a wired :class:`~cdf.service.FederationService` instead of fixture
executors — same ``expect`` block, real stacks.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdf.query import execute_plan, ground, partition_query
from cdf.query.catalog import SourceCatalog, source_ref_from_csi
from cdf.query.executor import SourceResult
from cdf.query.grounding import AnswerEnvelope
from cdf.query.planner import UnsupportedQueryError


class _FixtureExecutor:
    """A source executor that replays fixture rows (or simulates a failure)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def execute(self, subquery: Any) -> SourceResult:
        if self._data.get("fail"):
            raise RuntimeError(self._data.get("error", "source unavailable"))
        return SourceResult(
            rows=tuple(dict(r) for r in self._data.get("rows", [])),
            native_query=self._data.get("native_query"),
            as_of=self._data.get("as_of"),
            source_objects=tuple(self._data.get("source_objects", [])),
        )


@dataclass(frozen=True)
class GoldenOutcome:
    """Result of running one golden case."""

    name: str
    passed: bool
    mismatches: tuple[str, ...] = ()
    envelope: AnswerEnvelope | None = None


def load_goldens(directory: str | Path) -> list[dict[str, Any]]:
    """Load every ``*.json`` golden case in *directory*, sorted by filename."""
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(Path(directory).glob("*.json"))
    ]


def filter_goldens(
    cases: Iterable[dict[str, Any]],
    excluded_sources: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split out cases requiring sources absent from a partial live stack."""

    excluded = set(excluded_sources)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for case in cases:
        expected = set((case.get("expect") or {}).get("sources_touched") or [])
        (skipped if expected & excluded else selected).append(case)
    return selected, skipped


def _bag(rows: Iterable[dict[str, Any]]) -> list[tuple]:
    """Order-insensitive canonical form for a bag of bindings."""
    return sorted(tuple(sorted(r.items())) for r in rows)


def _unsupported_outcome(
    name: str, expect: dict[str, Any], exc: Exception
) -> GoldenOutcome:
    """Diff a planner refusal (``UnsupportedQueryError``) against the case.

    A case that sets ``expect.unsupported_contains`` PASSES when the planner
    refused and every substring appears in the refusal message; other expect
    keys are ignored for such cases (there is no envelope to diff). A case
    without that key that hits a planner refusal FAILS loudly — refusal must
    never be an accidental way to go green.
    """
    wants = expect.get("unsupported_contains")
    mismatches: list[str] = []
    if wants is None:
        mismatches.append(f"unexpected planner refusal: {exc}")
    else:
        for sub in wants:
            if sub not in str(exc):
                mismatches.append(
                    f"unsupported_contains: {sub!r} not in planner refusal ({exc})"
                )
    return GoldenOutcome(name=name, passed=not mismatches, mismatches=tuple(mismatches))


def _expected_unsupported_but_accepted(expect: dict[str, Any]) -> list[str]:
    """The inverse guard: the case expected a planner refusal, but the query
    was accepted — the construct the gate pins has been (perhaps accidentally)
    admitted. Surface it as a mismatch so admission changes are deliberate."""
    if expect.get("unsupported_contains") is not None:
        return [
            "expected a planner refusal (unsupported_contains set), but the "
            "query was accepted — if admission was intentional, rewrite this "
            "golden as a grounded case"
        ]
    return []


def run_golden(case: dict[str, Any]) -> GoldenOutcome:
    """Execute one golden case through the M5 pipeline and diff vs. expectations."""
    name = case.get("name", "<unnamed>")
    expect = case.get("expect", {})
    sources = case.get("sources", [])
    csi_docs = [s["csi"] for s in sources]

    catalog = SourceCatalog.from_csi_documents(csi_docs)
    executors = {
        source_ref_from_csi(s["csi"]).source_id: _FixtureExecutor(s.get("data", {}))
        for s in sources
    }

    try:
        plan = partition_query(case["question"], catalog)
    except UnsupportedQueryError as exc:
        return _unsupported_outcome(name, expect, exc)
    result = execute_plan(plan, executors)
    envelope = ground(result, allow_partial=bool(case.get("allow_partial", False)))

    mismatches = _expected_unsupported_but_accepted(expect)
    mismatches += _diff(expect, envelope)
    return GoldenOutcome(
        name=name, passed=not mismatches, mismatches=tuple(mismatches), envelope=envelope
    )


def run_golden_live(case: dict[str, Any], service: Any) -> GoldenOutcome:
    """Run one golden case through a live :class:`~cdf.service.FederationService`.

    The case supplies ``question`` (NL, resolved via the prepared-question
    registry) or ``sparql``; ``sources``/fixtures are ignored — the service's
    real executors answer. Same ``expect`` contract as :func:`run_golden`.
    """
    name = case.get("name", "<unnamed>")
    expect = case.get("expect", {})
    allow_partial = bool(case.get("allow_partial", False))
    try:
        if case.get("sparql"):
            envelope = service.federate_sparql(case["sparql"], allow_partial=allow_partial)
        else:
            envelope = service.federate_question(
                case["question"], allow_partial=allow_partial
            )
    except UnsupportedQueryError as exc:
        return _unsupported_outcome(name, expect, exc)
    mismatches = _expected_unsupported_but_accepted(expect)
    mismatches += _diff(expect, envelope)
    return GoldenOutcome(
        name=name, passed=not mismatches, mismatches=tuple(mismatches), envelope=envelope
    )


def _diff(expect: dict[str, Any], env: AnswerEnvelope) -> list[str]:
    out: list[str] = []

    if "status" in expect and env.status != expect["status"]:
        out.append(f"status: expected {expect['status']!r}, got {env.status!r}")

    if expect.get("reconciliation"):
        kinds = {c.kind for c in env.citations}
        if "arango" not in kinds or not (kinds - {"arango"}):
            out.append(
                "reconciliation: expected >=1 arango AND >=1 non-arango citation, "
                f"got kinds {sorted(kinds)}"
            )

    if expect.get("citations_empty") and env.citations:
        out.append(
            "citations_empty: expected zero citations (a refusal must not carry "
            f"fabricated evidence), got {[c.source_id for c in env.citations]}"
        )

    if "anchor_kind" in expect:
        wrong = sorted({c.kind for c in env.citations} - {expect["anchor_kind"]})
        if wrong or not env.citations:
            out.append(
                f"anchor_kind: expected every citation kind == {expect['anchor_kind']!r}, "
                f"got {wrong or 'no citations'}"
            )

    if "min_rows" in expect and len(env.bindings) < expect["min_rows"]:
        out.append(
            f"min_rows: expected >= {expect['min_rows']} binding rows, "
            f"got {len(env.bindings)} — a grounded-but-EMPTY result would "
            "otherwise pass contract-level expectations silently"
        )

    if "bindings" in expect:
        want = _bag(expect["bindings"])
        got = _bag(dict(b) for b in env.bindings)
        if want != got:
            out.append(f"bindings: expected {want}, got {got}")

    if "sources_touched" in expect:
        touched = sorted(s.source_id for s in env.retrieval_path if s.status == "ok")
        if touched != sorted(expect["sources_touched"]):
            out.append(
                f"sources_touched: expected {sorted(expect['sources_touched'])}, got {touched}"
            )

    if "failed_sources" in expect:
        if sorted(env.failed_sources) != sorted(expect["failed_sources"]):
            out.append(
                f"failed_sources: expected {sorted(expect['failed_sources'])}, "
                f"got {sorted(env.failed_sources)}"
            )

    if "citations" in expect:
        by_id = {c.source_id: c for c in env.citations}
        for want_c in expect["citations"]:
            sid = want_c["source_id"]
            cite = by_id.get(sid)
            if cite is None:
                out.append(f"citation: expected a citation for {sid!r}, none found")
                continue
            if "source_objects" in want_c and sorted(cite.source_objects) != sorted(
                want_c["source_objects"]
            ):
                out.append(
                    f"citation[{sid}].source_objects: expected "
                    f"{sorted(want_c['source_objects'])}, got {sorted(cite.source_objects)}"
                )

    for substr in expect.get("refusal_contains", []):
        if not env.refusal_reason or substr not in env.refusal_reason:
            out.append(f"refusal_reason missing {substr!r} (got {env.refusal_reason!r})")

    return out
