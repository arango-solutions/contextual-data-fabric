"""Fixture-mode CSI documents, one per generated system.

These are **not** analyzer output. In live mode the CSI for each system comes
from the estate (r2g ``export-csi`` over an RSA snapshot, or ASA's reverse
export). In fixture mode there is no database to introspect, so CDF derives a
CSI from the descriptor using the same CC-12 naming r2g's forge uses, and
stamps it ``provenance.producer = cdf-forge-fixture`` so the label is honest.

What the planner needs from a CSI: the conceptual model (entities, properties,
relationships) and ``provenance.source.kind/ref`` (the routing id). What a
system can execute is *not* read from the CSI: capabilities are declared in
the descriptor and travel on each golden's ``sources[].capabilities`` (manifest
format), so the planner consults the registry, never the kind. The physical
mapping is carried for completeness and for the live-mode diff (fixture vs.
estate-produced CSI is a useful drift signal).
"""

from __future__ import annotations

from typing import Any

from r2g.csi import owl_property_name
from r2g.forge import column_name, foreign_key_column, table_name

from cdf.eval.forge.descriptor import FIXTURE_PRODUCER
from cdf.eval.forge.sampler import Shape, System


def fk_property(parent: str) -> str:
    """Conceptual name of the FK column r2g's forge emits: ``account_id`` → ``accountId``."""
    return owl_property_name(foreign_key_column(parent))


def entity_properties(shape: Shape, entity: str) -> list[dict[str, str]]:
    """All conceptual properties of ``entity`` as the forward pipeline would
    expose them: the ``id`` spine, one FK property per relationship it is the
    child of, then the declared properties — sorted by name like r2g's CSI."""
    props: list[dict[str, str]] = [{"name": "id", "type": "integer"}]
    for rel in shape.relationships():
        if rel["fromEntity"] == entity:
            props.append({"name": fk_property(rel["toEntity"]), "type": "integer"})
    props.extend(dict(p) for p in shape.entity(entity)["properties"])
    return sorted(props, key=lambda p: p["name"])


def fixture_csi(shape: Shape, system: System) -> dict[str, Any]:
    owned = [e["name"] for e in shape.entities() if shape.owner[e["name"]] == system.name]
    entities = []
    physical: dict[str, Any] = {}
    for name in owned:
        props = entity_properties(shape, name)
        entities.append({"name": name, "properties": props})
        physical[name] = {
            "style": "COLLECTION",
            "collectionName": table_name(name),
            "properties": {p["name"]: {"field": column_name(p["name"])} for p in props},
        }
    # A relationship is declared where its FK lives: on the child's system.
    relationships = [dict(r) for r in shape.relationships() if r["fromEntity"] in owned]
    return {
        "csiVersion": "1",
        "conceptualModel": {"entities": entities, "relationships": relationships},
        "arangoPhysicalMapping": {"entities": physical, "relationships": {}},
        "provenance": {
            "producer": FIXTURE_PRODUCER,
            "direction": "forward",
            "source": {"kind": system.kind, "ref": system.name},
            "note": (
                "Synthesised by cdf.eval.forge from the shape descriptor for fixture-mode "
                "planning; not analyzer output. Live mode replaces this document."
            ),
        },
    }
