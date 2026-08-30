"""Import-order regression tests (issue #20).

cdf.governance's facade imports cdf.query (via composition), while modules in
cdf.query need governance *contracts*. If those inner imports go through the
facade instead of the leaf modules (governance.contracts / governance.runtime),
importing ``cdf.governance`` FIRST re-enters the partially initialized package
and dies — an order-dependent failure the full test suite happens to dodge, so
it must be pinned in a FRESH interpreter, not in-process."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "first_import",
    ["cdf.governance", "cdf.query", "cdf.mcp_server", "cdf.service"],
)
def test_any_package_can_be_imported_first(first_import: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", f"import {first_import}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"importing {first_import} first failed (order-dependent cycle?):\n"
        f"{proc.stderr[-800:]}"
    )
