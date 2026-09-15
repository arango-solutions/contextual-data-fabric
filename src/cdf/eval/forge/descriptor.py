"""The shape descriptor (ADR-0006 D-1): the one artifact that determines a
generated federation and its expected outcomes.

Written as YAML with a fixed key order so a regeneration is byte-identical
(D-5). Everything a consumer may assert is derivable from it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

FORGE_SHAPE_VERSION = 1

DESCRIPTOR_FILE = "shape.yaml"
ONTOLOGY_FILE = "ontology.json"
CATALOG_FILE = "expected-catalog.json"
GOLDENS_DIR = "goldens"
SYSTEMS_DIR = "systems"
FIXTURE_PRODUCER = "cdf-forge-fixture"


def descriptor_document(
    *,
    name: str,
    family: str,
    seed: int,
    rows_per_entity: int,
    partition_map: dict[str, dict[str, str]],
    systems: dict[str, dict[str, str]],
    generator: str,
    golden_names: list[str],
) -> dict[str, Any]:
    """Build the descriptor mapping in D-1 key order."""
    return {
        "forgeShapeVersion": FORGE_SHAPE_VERSION,
        "name": name,
        "family": family,
        "seed": seed,
        "ontology": ONTOLOGY_FILE,
        "scale": {"rowsPerEntity": rows_per_entity},
        "systems": systems,
        "partitionMap": partition_map,
        "denormLog": [],
        "expected": {
            "catalog": CATALOG_FILE,
            "goldens": [f"{GOLDENS_DIR}/{n}.json" for n in golden_names],
        },
        "fixture": {
            "producer": FIXTURE_PRODUCER,
            "systems": {s: f"{SYSTEMS_DIR}/{s}.csi.json" for s in systems},
            "note": (
                "Fixture-mode CSI documents are synthesised by CDF from the descriptor and "
                "labelled as such; live mode replaces them with r2g/RSA/ASA-produced CSI."
            ),
        },
        "generator": {"rows": generator},
    }


def dump_descriptor(doc: dict[str, Any]) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False)


def load_descriptor(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_descriptor(doc)
    return doc


def validate_descriptor(doc: Any) -> None:
    """Structural validation: the keys every consumer relies on."""
    if not isinstance(doc, dict):
        raise ValueError("descriptor must be a mapping")
    if doc.get("forgeShapeVersion") != FORGE_SHAPE_VERSION:
        raise ValueError(f"forgeShapeVersion must be {FORGE_SHAPE_VERSION}")
    for key in (
        "name",
        "family",
        "seed",
        "ontology",
        "scale",
        "systems",
        "partitionMap",
        "denormLog",
        "expected",
    ):
        if key not in doc:
            raise ValueError(f"descriptor missing {key!r}")
    if not isinstance(doc["seed"], int):
        raise ValueError("seed must be an integer")
    systems = doc["systems"]
    for concept, target in doc["partitionMap"].items():
        if target.get("system") not in systems:
            raise ValueError(
                f"partitionMap[{concept!r}] names unknown system {target.get('system')!r}"
            )
        if systems[target["system"]].get("dialect") != target.get("dialect"):
            raise ValueError(
                f"partitionMap[{concept!r}] dialect disagrees with systems[{target['system']!r}]"
            )
    expected = doc["expected"]
    if "catalog" not in expected or "goldens" not in expected:
        raise ValueError("expected must name catalog and goldens")
