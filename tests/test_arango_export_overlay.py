"""Tests for the reverse-CSI declared-reference overlay (deploy/arango/export_csi.py).

ASA derives relationships only from edge collections; the cmf corpus links by
attribute. The overlay merge is pure over an injected containment checker, so
these tests need no database."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "arango_export_csi", REPO / "deploy" / "arango" / "export_csi.py"
)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _csi():
    return {
        "conceptualModel": {"entities": [], "relationships": []},
        "arangoPhysicalMapping": {
            "entities": {
                "Chunk": {"collectionName": "chunks"},
                "Document": {"collectionName": "documents"},
            }
        },
    }


REF = {"fromCollection": "chunks", "attribute": "document_id",
       "toCollection": "documents", "toAttribute": "_key"}


def test_verified_reference_is_admitted_with_cc12_name():
    csi = _csi()
    admitted = mod.merge_declared_references(
        csi, {"references": [REF]}, containment=lambda ref: (80, 80)
    )
    assert admitted == ["chunksToDocuments"]
    assert csi["conceptualModel"]["relationships"] == [
        {"type": "chunksToDocuments", "fromEntity": "Chunk", "toEntity": "Document"}
    ]


def test_broken_declaration_fails_the_export_loudly():
    with pytest.raises(SystemExit, match="79/80"):
        mod.merge_declared_references(
            _csi(), {"references": [REF]}, containment=lambda ref: (80, 79)
        )


def test_reference_to_unmapped_collection_fails():
    csi = _csi()
    del csi["arangoPhysicalMapping"]["entities"]["Document"]
    with pytest.raises(SystemExit, match="did not map"):
        mod.merge_declared_references(
            csi, {"references": [REF]}, containment=lambda ref: (80, 80)
        )


def test_empty_from_collection_is_a_broken_declaration():
    """Zero rows can't verify anything — refuse rather than admit vacuously."""
    with pytest.raises(SystemExit):
        mod.merge_declared_references(
            _csi(), {"references": [REF]}, containment=lambda ref: (0, 0)
        )
