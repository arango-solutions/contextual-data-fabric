"""Tests for the demo ontology-map payload (deploy/demo/ontology.py).

The builder is pure over `SourceCatalog.vocabulary()` output, so the unit tests
feed synthetic vocabularies; the smoke test runs the real deploy/csi documents
through `payload_from_repo` against the real curator artifacts, proving the
panel's data matches what catalog-integrity reports in CI.
"""

from __future__ import annotations

import importlib.util
import json
from glob import glob
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "demo_ontology", REPO / "deploy" / "demo" / "ontology.py"
)
assert _spec is not None and _spec.loader is not None
demo_ontology = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo_ontology)


def _vocab(*sources):
    """Synthetic vocabulary in SourceCatalog.vocabulary()'s shape."""
    return [
        {
            "source_id": sid,
            "kind": kind,
            "ref": ref,
            "classes": [{"name": n, "properties": list(props)} for n, props in classes],
            "relationships": [],
        }
        for sid, kind, ref, classes in sources
    ]


TWO_SOURCES = _vocab(
    ("postgresql:crm", "postgresql", "crm",
     [("Account", ("accountId", "accountName", "role"))]),
    ("arango:cmf", "arango", "cmf",
     [("Document", ("accountId", "role", "citableUrl"))]),
)


def test_payload_shape_and_sources():
    payload = demo_ontology.build_ontology_payload(TWO_SOURCES)
    assert [s["source_id"] for s in payload["sources"]] == ["postgresql:crm", "arango:cmf"]
    assert payload["joinKeys"] == ["accountId"]
    account = payload["sources"][0]["classes"][0]
    assert account["name"] == "Account" and "accountName" in account["properties"]


def test_join_key_is_a_hub_not_a_collision():
    payload = demo_ontology.build_ontology_payload(TWO_SOURCES)
    hub_labels = {h["label"] for h in payload["overlap"]["hubs"]}
    assert "account id" in hub_labels  # humanized join key, on both entities
    # The join key must never be reported as a collision (it is the spine).
    assert all(c["label"] != "account id" for c in payload["overlap"]["collisions"])


def test_cross_source_collision_reported_unaccepted_by_default():
    payload = demo_ontology.build_ontology_payload(TWO_SOURCES)
    roles = [c for c in payload["overlap"]["collisions"] if c["label"] == "role"]
    assert len(roles) == 1
    c = roles[0]
    assert c["crossSource"] is True and c["accepted"] is False
    carriers = {(a["entity"], a["property"]) for a in c["attributes"]}
    assert carriers == {("Account", "role"), ("Document", "role")}


def test_allowlisted_collision_is_marked_accepted_with_reason():
    payload = demo_ontology.build_ontology_payload(
        TWO_SOURCES, allowed={"role": "intentional: contact role vs document role"}
    )
    c = next(x for x in payload["overlap"]["collisions"] if x["label"] == "role")
    assert c["accepted"] is True
    assert "intentional" in c["reason"]


def test_real_catalog_smoke_matches_curation_artifacts():
    """The live deploy/csi documents + deploy/catalog curation artifacts produce
    a coherent panel payload: all sources present, the accountId hub found, and
    every allowlisted label arriving pre-accepted."""
    from cdf.query import SourceCatalog

    docs = [json.loads(Path(p).read_text()) for p in sorted(glob(str(REPO / "deploy/csi/*.json")))]
    catalog = SourceCatalog.from_csi_documents(docs)
    payload = demo_ontology.payload_from_repo(catalog, REPO)

    assert len(payload["sources"]) == len(docs)
    assert "accountId" in payload["joinKeys"]
    assert any(h["label"] == "account id" for h in payload["overlap"]["hubs"])

    allow = json.loads((REPO / "deploy/catalog/label-collisions-allow.json").read_text())
    allowed_labels = {e["label"] for e in allow["allowed"]}
    reported = {c["label"]: c for c in payload["overlap"]["collisions"]}
    for label in allowed_labels & set(reported):
        assert reported[label]["accepted"] is True, f"{label} should arrive accepted"


def test_page_embeds_the_ontology_panel():
    """Drift guard: the page template carries the placeholder, the init call,
    and the post-query highlight hook."""
    page = (REPO / "deploy/demo/server.py").read_text()
    assert "__ONTOLOGY__" in page
    assert "initOntologyMap(__ONTOLOGY__)" in page
    assert "markOntologyActive(d)" in page
    assert 'id="ontomap"' in page


def test_relationship_details_pass_through():
    """Structured relationship edges (type/from/to) reach the payload — a
    diagram is unreadable without them."""
    vocab = _vocab(
        ("postgresql:crm", "postgresql", "crm",
         [("Account", ("accountId", "accountName")),
          ("Contract", ("accountId", "contractId"))]),
    )
    vocab[0]["relationshipDetails"] = [
        {"type": "contractsToAccounts", "from": "Contract", "to": "Account"}
    ]
    payload = demo_ontology.build_ontology_payload(vocab)
    assert payload["sources"][0]["relationshipDetails"] == [
        {"type": "contractsToAccounts", "from": "Contract", "to": "Account"}
    ]


def test_spine_edges_connect_carriers_to_the_hub_across_sources():
    payload = demo_ontology.build_ontology_payload(TWO_SOURCES)
    spine = payload["spine"]
    # Document (arango) carries accountId -> hub Account (postgres); the hub
    # itself gets no self-edge.
    assert {"key": "accountId", "from": "Document", "fromSource": "arango:cmf",
            "to": "Account", "toSource": "postgresql:crm"} in spine
    assert all(e["from"] != "Account" for e in spine)


def test_spine_yields_to_declared_relationships():
    """A declared FK between a pair outranks the inferred spine edge — one
    edge per pair, the stronger one."""
    vocab = _vocab(
        ("postgresql:crm", "postgresql", "crm",
         [("Account", ("accountId",)), ("Contract", ("accountId",))]),
    )
    vocab[0]["relationshipDetails"] = [
        {"type": "contractsToAccounts", "from": "Contract", "to": "Account"}
    ]
    payload = demo_ontology.build_ontology_payload(vocab)
    assert payload["spine"] == []  # the declared edge covers the only pair


def test_spine_draws_nothing_for_a_hubless_key():
    """A join key naming no catalog entity draws no edges — never a guess."""
    vocab = _vocab(
        ("postgresql:crm", "postgresql", "crm",
         [("Contract", ("tenantId", "contractId"))]),
    )
    payload = demo_ontology.build_ontology_payload(vocab, join_keys=("tenantId",))
    assert payload["spine"] == []


def test_page_embeds_the_diagram():
    page = (REPO / "deploy/demo/server.py").read_text()
    assert 'id="onto-diagram"' in page
    assert "renderOntologyDiagram(" in page
    assert "ed-spine" in page and "onto-arrow" in page
