"""CC-9 owned dependency pinning contracts."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = ROOT / "deploy" / "pins" / "arango-sparql-py.txt"
PIN_REFERENCE = "deploy/pins/arango-sparql-py.txt"


def test_arango_sparql_runtime_uses_one_full_commit_pin() -> None:
    requirements = [
        line.strip()
        for line in PIN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert len(requirements) == 1
    # The public mirror, not the private arango-solutions repo: the pin must
    # resolve for an unauthenticated fresh clone (CI has no org credentials).
    # The reviewed SHA is identical on both remotes.
    assert "github.com/ArthurKeen/arango-sparql-py.git@" in requirements[0]
    assert re.search(r"@[0-9a-f]{40}$", requirements[0])


def test_ci_and_make_install_share_the_reviewed_pin() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert workflow.count(f"-r {PIN_REFERENCE}") == 3
    assert "git+https://github.com" not in workflow  # only the pin file may carry the URL
    assert f"ARANGO_SPARQL_PIN ?= {PIN_REFERENCE}" in makefile
    assert 'pip install -r "$(ARANGO_SPARQL_PIN)"' in makefile


R2G_PIN_PATH = ROOT / "deploy" / "pins" / "r2g-arango.txt"
R2G_PIN_REFERENCE = "deploy/pins/r2g-arango.txt"


def test_r2g_forge_generator_uses_one_full_commit_pin() -> None:
    """ADR-0006 D-4: CDF orchestrates, r2g generates — consumed under CC-9.
    A git pin until r2g 0.4.2 ships the forge; then a version band."""
    requirements = [
        line.strip()
        for line in R2G_PIN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(requirements) == 1
    assert "github.com/ArthurKeen/r2g-arango.git@" in requirements[0]
    assert re.search(r"@[0-9a-f]{40}$", requirements[0])


def test_ci_and_make_install_share_the_r2g_pin() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert workflow.count(f"-r {R2G_PIN_REFERENCE}") == 3
    assert f"R2G_PIN ?= {R2G_PIN_REFERENCE}" in makefile
    assert 'pip install -e ".[forge]" -r "$(R2G_PIN)"' in makefile
