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
    # The org repo leads (AGENTS.md) and is public, so CI resolves it without
    # credentials; the ArthurKeen mirror carries the same SHA (PR #34 review).
    assert "github.com/arango-solutions/r2g-arango.git@" in requirements[0]
    assert re.search(r"@[0-9a-f]{40}$", requirements[0])


def test_ci_and_make_install_share_the_r2g_pin() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert workflow.count(f"-r {R2G_PIN_REFERENCE}") == 3
    assert f"R2G_PIN ?= {R2G_PIN_REFERENCE}" in makefile
    assert 'pip install -e ".[forge]" -r "$(R2G_PIN)"' in makefile


def test_forge_generator_installs_regardless_of_local_siblings() -> None:
    """PR #34 review, item 4: the r2g pin must not live inside the
    CDF_USE_LOCAL_SIBLINGS branch — that switch is about arango-sparql-py.
    With it set to 1, ``make forge-suite`` died on ``import r2g`` and every
    forge test skipped."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\ninstall:\n", 1)[1].split("\n\n", 1)[0]
    lines = recipe.splitlines()
    if_index = next(i for i, line in enumerate(lines) if "CDF_USE_LOCAL_SIBLINGS" in line)
    fi_index = next(i for i in range(if_index, len(lines)) if lines[i].strip() == "fi")
    forge_index = next(i for i, line in enumerate(lines) if '".[forge]"' in line)
    assert forge_index > fi_index, "the forge install must run outside the sibling switch"
    assert not lines[forge_index].rstrip().endswith("\\"), "unconditional, not a shell continuation"
