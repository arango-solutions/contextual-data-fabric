"""Golden seed-question regression gate (F1).

Discovers every JSON golden under ``cdf/eval/goldens`` and runs it through the
real M5 pipeline. A failing case prints the exact field-level diff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cdf.eval import golden, load_goldens, run_golden

GOLDENS_DIR = Path(golden.__file__).parent / "goldens"
CASES = load_goldens(GOLDENS_DIR)


def test_golden_set_is_non_empty():
    # A regression gate that silently runs zero cases is a liar.
    assert CASES, f"no golden cases found under {GOLDENS_DIR}"


@pytest.mark.parametrize("case", CASES, ids=[c.get("name", "?") for c in CASES])
def test_golden_case(case):
    outcome = run_golden(case)
    assert outcome.passed, (
        f"golden {outcome.name!r} failed:\n  " + "\n  ".join(outcome.mismatches)
    )


def test_partial_failure_is_declared_never_silent() -> None:
    """The CC-5 case the old harness couldn't express: one leg down, strict
    mode -> refusal that *names* the failed leg; concierge mode -> a partial
    answer with the shortfall declared."""
    from cdf.eval.golden import run_golden

    case = {
        "name": "partial-failure",
        "question": (
            "PREFIX c: <urn:arango-sparql:concept#> SELECT ?name ?subject WHERE { "
            "?a a c:Account ; c:accountName ?name ; c:accountId ?acct . "
            "?t a c:Ticket ; c:subject ?subject ; c:accountId ?acct . }"
        ),
        "sources": [
            {
                "csi": {
                    "csiVersion": "1",
                    "conceptualModel": {"entities": [{"name": "Account", "properties": [
                        {"name": "accountId"}, {"name": "accountName"}]}]},
                    "physicalMapping": {"entities": {"Account": {"tableName": "accounts"}}},
                    "provenance": {"producer": "r2g", "direction": "forward",
                                   "source": {"kind": "postgresql", "ref": "crm"}},
                },
                "data": {"rows": [{"a": "urn:1", "name": "Meridian", "acct": "001"}]},
            },
            {
                "csi": {
                    "csiVersion": "1",
                    "conceptualModel": {"entities": [{"name": "Ticket", "properties": [
                        {"name": "subject"}, {"name": "accountId"}]}]},
                    "arangoPhysicalMapping": {"entities": {"Ticket": {
                        "style": "COLLECTION", "collectionName": "tickets"}},
                        "relationships": {}},
                    "provenance": {"producer": "analyzer", "direction": "reverse",
                                   "source": {"kind": "arango", "ref": "tickets"}},
                },
                "data": {"fail": True, "error": "container stopped"},
            },
        ],
        "expect": {
            "status": "refused",
            "failed_sources": ["arango:tickets"],
            "refusal_contains": ["arango:tickets"],
        },
    }
    outcome = run_golden(case)
    assert outcome.passed, outcome.mismatches


def test_expected_refusal_but_accepted_query_fails_the_case() -> None:
    """The inverse guard: a case pinning a planner refusal must go RED the day
    the construct is (deliberately or accidentally) admitted — never silently
    keep passing."""
    case = {
        "name": "guard",
        "question": (
            "PREFIX c: <urn:arango-sparql:concept#> "
            "SELECT ?n WHERE { ?a a c:Account ; c:name ?n }"
        ),
        "sources": [{
            "csi": {
                "csiVersion": "1",
                "conceptualModel": {"entities": [
                    {"name": "Account", "properties": [{"name": "name"}]}]},
                "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
                "provenance": {"producer": "r2g", "direction": "forward",
                               "source": {"kind": "postgresql", "ref": "crm"}},
            },
            "data": {"rows": [{"n": "Acme"}]},
        }],
        "expect": {"unsupported_contains": ["aggregation"]},
    }
    outcome = run_golden(case)
    assert not outcome.passed
    assert any("was accepted" in m for m in outcome.mismatches)


def test_unexpected_planner_refusal_fails_the_case() -> None:
    """A case with ordinary envelope expectations that hits a planner refusal
    fails loudly — refusal is never an accidental way to go green."""
    case = {
        "name": "guard2",
        "question": (
            "PREFIX c: <urn:arango-sparql:concept#> "
            "SELECT ?a WHERE { { ?a a c:Account } UNION { ?a a c:Account } }"
        ),
        "sources": [{
            "csi": {
                "csiVersion": "1",
                "conceptualModel": {"entities": [
                    {"name": "Account", "properties": [{"name": "name"}]}]},
                "arangoPhysicalMapping": {"entities": {}, "relationships": {}},
                "provenance": {"producer": "r2g", "direction": "forward",
                               "source": {"kind": "postgresql", "ref": "crm"}},
            },
        }],
        "expect": {"status": "grounded"},
    }
    outcome = run_golden(case)
    assert not outcome.passed
    assert any("unexpected planner refusal" in m for m in outcome.mismatches)
