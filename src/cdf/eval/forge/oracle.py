"""The oracle (ADR-0006 D-1/D-2): expected catalog and expected goldens,
computed from the descriptor and the pre-partition dataset — never from an
engine's answer.

Goldens are the *mechanical* template questions only (D-5: paraphrases are a
separate, quarantined step). Each golden is emitted in the fixture-golden
format ``cdf.eval.golden.run_golden`` consumes: the conceptual SPARQL, one
``sources[]`` entry per system the plan touches (fixture CSI + the rows that
system would return, keyed by the query's variable names), and the ``expect``
block (grounded bindings, or a named planner refusal).

Question families:

* **lookup** — one entity, up to two properties, single leg.
* **join** — child ⋈ parent over a relationship whose endpoints live on
  different systems (the cross-source contract shape, cf. g2).
* **chain** — three legs across three systems when the shape has one.
* **single-leg aggregation** — ``COUNT`` grouped by a boolean property; expected
  grounded on a system whose kind declares GROUP BY (postgresql, arango), and
  a *named refusal* elsewhere (ADR-0005 D4: the refusal names the capability).
* **cross-leg aggregation** — ``COUNT`` over a cross-system join; expected
  refusal until S2's fold-combine lands, at which point this golden flips.

Coverage policy — which families are emitted and in what order — is the
Solutions Engineer's lane (roadmap §4). Sign-off is recorded in the
hand-maintained ledger ``deploy/forge/signoff.yaml``
(:mod:`cdf.eval.forge.signoff`), never inside a generated golden: generated
files are a pure function of code plus seed, so regeneration cannot wipe a
sign-off and CI's drift check stays unambiguous (PR #34 review, item 2).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from r2g.csi import owl_property_name
from r2g.forge import column_name, foreign_key_column, table_name

from cdf.catalog.capabilities import default_capabilities_for_kind
from cdf.eval.forge.dataset import Dataset
from cdf.eval.forge.fixture_csi import fixture_csi
from cdf.eval.forge.sampler import Shape, System

PREFIX = "PREFIX c: <urn:arango-sparql:concept#>"


def _var(entity: str) -> str:
    """Variable name for an entity: lowerCamel of the class (``account``)."""
    return owl_property_name(entity)


def _pvar(entity: str, prop: str) -> str:
    return f"{_var(entity)}_{prop}"


def _declared(shape: Shape, entity: str, limit: int) -> list[dict[str, str]]:
    return list(shape.entity(entity)["properties"])[:limit]


def _first_prop(
    shape: Shape, entity: str, *, prefer_type: str | None = None
) -> dict[str, str] | None:
    props = shape.entity(entity)["properties"]
    if prefer_type:
        for p in props:
            if p["type"] == prefer_type:
                return p
        return None
    return props[0] if props else None


def _source_entry(
    shape: Shape, system: System, rows: list[dict[str, Any]], tables: list[str]
) -> dict[str, Any]:
    return {
        "system": system.name,
        "csi": fixture_csi(shape, system),
        "data": {
            "rows": rows,
            "native_query": (
                "-- forge fixture: rows projected from the pre-partition dataset for "
                + ", ".join(tables)
            ),
            "source_objects": tables,
            "as_of": "1970-01-01T00:00:00Z",
        },
    }


def _bindings_rows(
    dataset: Dataset, entity: str, props: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Per-source rows for a lookup leg: entity var → id, property vars → values."""
    out = []
    for r in dataset.rows(entity):
        row = {_var(entity): r["id"]}
        for p in props:
            row[_pvar(entity, p["name"])] = r[column_name(p["name"])]
        out.append(row)
    return out


def expected_catalog(shape: Shape) -> dict[str, Any]:
    """Ownership, join keys and collisions the M11 catalog must report."""
    join_keys = []
    for rel in shape.relationships():
        child, parent = rel["fromEntity"], rel["toEntity"]
        join_keys.append(
            {
                "relationship": rel["type"],
                "from": child,
                "to": parent,
                "fromSystem": shape.owner[child],
                "toSystem": shape.owner[parent],
                "crossSystem": shape.owner[child] != shape.owner[parent],
                "childColumn": foreign_key_column(parent),
                "parentColumn": "id",
            }
        )
    return {
        "shape": shape.name,
        "ownership": dict(shape.owner),
        "systems": {
            s.name: {"dialect": s.dialect, "kind": s.kind, "sourceId": s.source_id}
            for s in shape.systems
        },
        "joinKeys": join_keys,
        "collisions": [],  # denormLog is empty in this slice; the denormalizer (S5) fills it
        "tables": {e["name"]: table_name(e["name"]) for e in shape.entities()},
    }


def compose_goldens(shape: Shape, dataset: Dataset) -> list[dict[str, Any]]:
    """All mechanical goldens for a shape, deterministic in content and order."""
    cases: list[dict[str, Any]] = []
    cases.extend(_lookup_cases(shape, dataset))
    cross = shape.cross_system_relationships()
    cases.extend(_join_case(shape, dataset, rel) for rel in cross)
    chain = _chain_case(shape, dataset)
    if chain:
        cases.append(chain)
    cases.extend(_single_leg_aggregation_cases(shape, dataset))
    if cross:
        cases.append(_cross_leg_aggregation_case(shape, dataset, cross[0]))
    for c in cases:
        c["shape"] = shape.name
    return cases


def _lookup_cases(shape: Shape, dataset: Dataset) -> list[dict[str, Any]]:
    cases = []
    for e in shape.entities():
        name = e["name"]
        props = _declared(shape, name, 2)
        if not props:
            continue
        system = shape.owner_system(name)
        v = _var(name)
        pvars = [_pvar(name, p["name"]) for p in props]
        triples = " ; ".join(f"c:{p['name']} ?{_pvar(name, p['name'])}" for p in props)
        select = " ".join("?" + x for x in pvars)
        sparql = f"{PREFIX} SELECT {select} WHERE {{ ?{v} a c:{name} ; {triples} }}"
        rows = _bindings_rows(dataset, name, props)
        bindings = [{k: r[k] for k in pvars} for r in rows]
        cases.append(
            {
                "name": f"{shape.name}--lookup--{name}",
                "family": "lookup",
                "question": sparql,
                "sources": [_source_entry(shape, system, rows, [table_name(name)])],
                "expect": {
                    "status": "grounded",
                    "bindings": bindings,
                    "sources_touched": [system.source_id],
                },
            }
        )
    return cases


def _join_case(shape: Shape, dataset: Dataset, rel: dict[str, str]) -> dict[str, Any]:
    child, parent = rel["fromEntity"], rel["toEntity"]
    cp = _first_prop(shape, child) or {"name": "id", "type": "integer"}
    pp = _first_prop(shape, parent) or {"name": "id", "type": "integer"}
    cv, pv = _var(child), _var(parent)
    cpv, ppv = _pvar(child, cp["name"]), _pvar(parent, pp["name"])
    sparql = (
        f"{PREFIX} SELECT ?{cpv} ?{ppv} WHERE {{ "
        f"?{cv} a c:{child} ; c:{cp['name']} ?{cpv} ; c:{rel['type']} ?{pv} . "
        f"?{pv} a c:{parent} ; c:{pp['name']} ?{ppv} }}"
    )
    fk = foreign_key_column(parent)
    child_rows = [
        {cv: r["id"], cpv: r[column_name(cp["name"])], pv: r[fk]} for r in dataset.rows(child)
    ]
    parent_rows = [{pv: r["id"], ppv: r[column_name(pp["name"])]} for r in dataset.rows(parent)]
    parent_val = {r[pv]: r[ppv] for r in parent_rows}
    bindings = [{cpv: r[cpv], ppv: parent_val[r[pv]]} for r in child_rows if r[pv] in parent_val]
    csys, psys = shape.owner_system(child), shape.owner_system(parent)
    return {
        "name": f"{shape.name}--join--{child}-{parent}",
        "family": "join",
        "question": sparql,
        "sources": [
            _source_entry(shape, csys, child_rows, [table_name(child)]),
            _source_entry(shape, psys, parent_rows, [table_name(parent)]),
        ],
        "expect": {
            "status": "grounded",
            "bindings": bindings,
            "sources_touched": sorted({csys.source_id, psys.source_id}),
        },
    }


def _chain_case(shape: Shape, dataset: Dataset) -> dict[str, Any] | None:
    """A → B → C across three distinct systems, if the shape has one."""
    rels = shape.cross_system_relationships()
    by_child = {r["fromEntity"]: r for r in rels}
    for r1 in rels:
        r2 = by_child.get(r1["toEntity"])
        if not r2:
            continue
        a, b, c = r1["fromEntity"], r1["toEntity"], r2["toEntity"]
        systems = {shape.owner[a], shape.owner[b], shape.owner[c]}
        if len(systems) != 3:
            continue
        pa = _first_prop(shape, a) or {"name": "id"}
        pb = _first_prop(shape, b) or {"name": "id"}
        pc = _first_prop(shape, c) or {"name": "id"}
        va, vb, vc = _var(a), _var(b), _var(c)
        pav, pbv, pcv = _pvar(a, pa["name"]), _pvar(b, pb["name"]), _pvar(c, pc["name"])
        sparql = (
            f"{PREFIX} SELECT ?{pav} ?{pbv} ?{pcv} WHERE {{ "
            f"?{va} a c:{a} ; c:{pa['name']} ?{pav} ; c:{r1['type']} ?{vb} . "
            f"?{vb} a c:{b} ; c:{pb['name']} ?{pbv} ; c:{r2['type']} ?{vc} . "
            f"?{vc} a c:{c} ; c:{pc['name']} ?{pcv} }}"
        )
        fk_ab, fk_bc = foreign_key_column(b), foreign_key_column(c)
        rows_a = [
            {va: r["id"], pav: r[column_name(pa["name"])], vb: r[fk_ab]} for r in dataset.rows(a)
        ]
        rows_b = [
            {vb: r["id"], pbv: r[column_name(pb["name"])], vc: r[fk_bc]} for r in dataset.rows(b)
        ]
        rows_c = [{vc: r["id"], pcv: r[column_name(pc["name"])]} for r in dataset.rows(c)]
        b_by_id = {r[vb]: r for r in rows_b}
        c_by_id = {r[vc]: r for r in rows_c}
        bindings = []
        for ra in rows_a:
            rb = b_by_id.get(ra[vb])
            if rb is None:
                continue
            rc = c_by_id.get(rb[vc])
            if rc is None:
                continue
            bindings.append({pav: ra[pav], pbv: rb[pbv], pcv: rc[pcv]})
        sa, sb, sc = shape.owner_system(a), shape.owner_system(b), shape.owner_system(c)
        return {
            "name": f"{shape.name}--chain--{a}-{b}-{c}",
            "family": "chain",
            "question": sparql,
            "sources": [
                _source_entry(shape, sa, rows_a, [table_name(a)]),
                _source_entry(shape, sb, rows_b, [table_name(b)]),
                _source_entry(shape, sc, rows_c, [table_name(c)]),
            ],
            "expect": {
                "status": "grounded",
                "bindings": bindings,
                "sources_touched": sorted({sa.source_id, sb.source_id, sc.source_id}),
            },
        }
    return None


def _single_leg_aggregation_cases(shape: Shape, dataset: Dataset) -> list[dict[str, Any]]:
    cases = []
    for e in shape.entities():
        name = e["name"]
        flag = _first_prop(shape, name, prefer_type="boolean")
        if not flag:
            continue
        system = shape.owner_system(name)
        v, fv = _var(name), _pvar(name, flag["name"])
        sparql = (
            f"{PREFIX} SELECT ?{fv} (COUNT(?{v}) AS ?n) WHERE {{ "
            f"?{v} a c:{name} ; c:{flag['name']} ?{fv} }} GROUP BY ?{fv}"
        )
        counts = Counter(r[column_name(flag["name"])] for r in dataset.rows(name))
        aggregated = [{fv: k, "n": n} for k, n in sorted(counts.items(), key=lambda kv: str(kv[0]))]
        capable = default_capabilities_for_kind(system.kind).aggregation.group_by
        if capable:
            expect: dict[str, Any] = {
                "status": "grounded",
                "bindings": aggregated,
                "sources_touched": [system.source_id],
            }
        else:
            expect = {"unsupported_contains": ["declares no GROUP BY capability", system.source_id]}
        cases.append(
            {
                "name": f"{shape.name}--aggregate--{name}",
                "family": "single_leg_aggregation",
                "question": sparql,
                "sources": [_source_entry(shape, system, aggregated, [table_name(name)])],
                "expect": expect,
            }
        )
    return cases


def _cross_leg_aggregation_case(
    shape: Shape, dataset: Dataset, rel: dict[str, str]
) -> dict[str, Any]:
    child, parent = rel["fromEntity"], rel["toEntity"]
    pp = _first_prop(shape, parent) or {"name": "id", "type": "integer"}
    cv, pv, ppv = _var(child), _var(parent), _pvar(parent, pp["name"])
    sparql = (
        f"{PREFIX} SELECT ?{ppv} (COUNT(?{cv}) AS ?n) WHERE {{ "
        f"?{cv} a c:{child} ; c:{rel['type']} ?{pv} . "
        f"?{pv} a c:{parent} ; c:{pp['name']} ?{ppv} }} GROUP BY ?{ppv}"
    )
    csys, psys = shape.owner_system(child), shape.owner_system(parent)
    return {
        "name": f"{shape.name}--crossleg-aggregate--{child}-{parent}",
        "family": "cross_leg_aggregation",
        "question": sparql,
        "sources": [
            _source_entry(shape, csys, [], [table_name(child)]),
            _source_entry(shape, psys, [], [table_name(parent)]),
        ],
        "expect": {"unsupported_contains": ["cross-source aggregation"]},
        "note": (
            "Refusal expected until S2's fold-combine (ADR-0005 D1/D2) lands; then this "
            "golden is rewritten as grounded with the counts computed here."
        ),
    }
