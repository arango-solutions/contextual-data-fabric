"""Behavioral tests for the demo editor's SPARQL formatter.

``formatSparql`` lives in ``deploy/demo/editor.js`` (browser code with a node
export seam), so these tests execute it under node — the same way CI's ubuntu
runners can. The invariant that matters: formatting moves ONLY whitespace;
the token stream is byte-identical after collapsing runs of whitespace.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

EDITOR = Path(__file__).parent.parent / "deploy" / "demo" / "editor.js"

Q2 = (
    'PREFIX c: <urn:arango-sparql:concept#> SELECT ?renewalDate ?trend ?url WHERE { '
    '?a a c:Account ; c:accountName "Meridian Logistics, LLC" ; c:accountId ?acct . '
    "?k a c:Contract ; c:renewalDate ?renewalDate ; c:accountId ?acct . "
    "?u a c:UsageMetric ; c:isPeakPeriod true ; c:volumeTrend ?trend ; c:accountId ?acct . "
    '?d a c:Document ; c:role "signal" ; c:citableUrl ?url ; c:accountId ?acct . '
    'FILTER(?renewalDate > "2025-06-01") }'
)
FILTER_OPTIONAL = (
    "PREFIX c: <urn:arango-sparql:concept#> SELECT ?f ?lat WHERE { "
    "?e a c:QueryEvent ; c:feature ?f ; c:avgLatencyMs ?lat ; c:accountId ?acct . "
    "FILTER(?lat < 25.5) OPTIONAL { ?d c:filename ?fn } }"
)


def format_sparql(query: str) -> str:
    script = (
        f"const {{formatSparql}} = require({json.dumps(str(EDITOR))});"
        "let d = '';"
        "process.stdin.on('data', (c) => d += c);"
        "process.stdin.on('end', () => process.stdout.write(formatSparql(d)));"
    )
    result = subprocess.run(
        [str(NODE), "-e", script], input=query, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@pytest.mark.parametrize("query", [Q2, FILTER_OPTIONAL], ids=["q2", "filter-optional"])
def test_formatting_moves_only_whitespace(query: str) -> None:
    assert collapse(format_sparql(query)) == collapse(query)


def test_one_line_query_becomes_structured_lines() -> None:
    lines = format_sparql(Q2).splitlines()
    assert lines[0].startswith("PREFIX c: <urn:arango-sparql:concept#>")
    assert lines[1].startswith("SELECT ") and lines[1].endswith("{")
    assert lines[-1] == "}"
    # every subject block ends with ' .', continuations end with ' ;' and are
    # indented one step deeper than their subject line
    subject = next(line for line in lines if line.lstrip().startswith("?a a c:Account"))
    continuation = next(line for line in lines if "c:accountName" in line)
    assert subject.endswith(";")
    assert len(continuation) - len(continuation.lstrip()) > len(subject) - len(subject.lstrip())


def test_string_and_iri_content_is_never_touched() -> None:
    out = format_sparql(Q2)
    assert '"Meridian Logistics, LLC"' in out
    assert "<urn:arango-sparql:concept#>" in out
    assert '"2025-06-01"' in out


def test_decimal_literals_survive_dot_splitting() -> None:
    out = format_sparql(FILTER_OPTIONAL)
    assert "25.5" in out


def test_idempotent() -> None:
    once = format_sparql(Q2)
    assert format_sparql(once) == once
