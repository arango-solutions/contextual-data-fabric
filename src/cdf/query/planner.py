"""Query-graph partition planner (M5 / E1).

Given a conceptual SPARQL query and a :class:`~cdf.query.catalog.SourceCatalog`,
partition the query's graph pattern by the source each concept/property maps to,
and emit per-source sub-queries plus the cross-source join keys.

Routing algorithm (OBDA-style, two passes):

1. **Class binding.** Every ``?x a :Class`` triple binds variable ``?x`` to the
   source that owns ``:Class``.
2. **Route.** Each triple goes to:
   - a ``rdf:type`` triple → the class's source;
   - any other triple whose subject variable is class-bound → that source (the
     subject's class wins, which resolves properties whose plain name is shared
     across sources);
   - otherwise, if the predicate maps to exactly one source → that source;
   - else it is left **unresolved** (surfaced, never dropped).

A variable that ends up in the buckets of two *different* sources is a
cross-source **join key** — the point where the executor reconciles results via
the canonical entity hub (M6). This falls out of the routing naturally: an
object-property triple stays with its subject's source, while the object
variable is independently class-bound to the other source, so it appears on both
sides.

**Single-leg FILTER / OPTIONAL pushdown.** Beyond the pure BGP, E1 also accepts
a query whose ``FILTER`` conjuncts and ``OPTIONAL`` groups each fall entirely
within one source leg, and pushes them *into* that leg's sub-query:

- a ``FILTER`` conjunct (``?var op literal``) is routed to the leg(s) whose
  required triples bind its variable; a filter on a **join key** is *replicated*
  into every such leg (conjunctive semantics make that correct and it only
  shrinks each leg's result);
- an ``OPTIONAL`` group of BGP triples that all route to one source is attached
  to that leg.

Anything E1 still cannot faithfully split — a cross-source ``OPTIONAL``, a
``FILTER`` on an unbound variable, a non-simple filter expression (``EXISTS``,
``REGEX``, disjunction, ``?var op ?var``), or ``UNION`` / ``MINUS`` / ``BIND`` /
``GRAPH`` / aggregation — is **refused by name**, never silently dropped.
"""

from __future__ import annotations

from typing import Any

from rdflib import RDF, Literal, URIRef
from rdflib.namespace import XSD
from rdflib.plugins.sparql import prepareQuery
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.term import Variable

from .catalog import SourceCatalog
from .types import PartitionPlan, SourceRef, SubQuery, TriplePattern

_TermTriple = tuple[Any, Any, Any]
#: One collected FILTER conjunct: (variable, comparison operator, literal).
_Conjunct = tuple[Variable, str, Literal]


class UnsupportedQueryError(ValueError):
    """Raised when a query uses a construct E1 cannot faithfully partition.

    E1 partitions **basic graph patterns** plus **single-leg** ``FILTER`` /
    ``OPTIONAL`` (see the module docstring). A construct that changes the
    pattern's meaning in ways a naive per-source split would silently get wrong —
    ``UNION``, ``MINUS``, ``BIND``, a named ``GRAPH`` block, aggregation, a
    *cross-source* ``OPTIONAL``, a ``FILTER`` on an unbound variable, or a
    non-simple filter expression — is refused (naming the offending construct)
    rather than returning an incorrect plan. These land in a later E1 iteration.
    """


# Algebra node names that carry graph-pattern semantics a partitioner would drop
# or mangle. FILTER (``Filter``) and OPTIONAL (``LeftJoin``) are handled by the
# structured walk below, so they are *not* here. Result modifiers (Project,
# OrderBy, Slice, Distinct, Reduced, ToMultiSet, SelectQuery) are safe — they
# apply after the per-source results are joined.
_UNSUPPORTED_NODES = {
    "Union": "UNION",
    "Minus": "MINUS",
    "Extend": "BIND / expression assignment",
    "Graph": "named GRAPH block",
    "Group": "GROUP BY / aggregation",
    "AggregateJoin": "aggregation",
    # User-authored SERVICE is refused BY DESIGN, not by gap: the planner must
    # own source placement (concept ownership, OBAC, citations) — a user-pinned
    # endpoint would bypass all three. Emitting the *plan* in SERVICE form as an
    # EXPLAIN/interop artifact is a separate idea (issue #15).
    "ServiceGraphPattern": "SERVICE (user-directed federation)",
}

#: Comparison operators a pushed-down FILTER conjunct may use.
_FILTER_OPS = {">", "<", ">=", "<=", "=", "!="}
#: Flip a comparison when the conjunct is written ``literal op ?var``.
_FLIP_OP = {">": "<", "<": ">", ">=": "<=", "<=": ">=", "=": "=", "!=": "!="}
#: Result-modifier / query wrappers that carry a single child pattern under ``p``.
_PATTERN_WRAPPERS = {
    "SelectQuery", "Project", "Distinct", "Reduced", "Slice", "OrderBy", "ToMultiSet",
}
#: Wrappers legitimate on the path from the query root down to the ``Group``
#: node of a top-level aggregation: result modifiers, the aggregation plumbing
#: itself (``AggregateJoin`` + the ``Extend``s that bind aggregate results to
#: their SELECT aliases), and ``Filter`` (a HAVING clause). Anything else on
#: that path means the aggregation is nested inside a shape E1 cannot admit.
_AGGREGATION_WRAPPERS = _PATTERN_WRAPPERS | {"Extend", "Filter", "AggregateJoin"}

#: Source kinds whose leg can execute a full SPARQL GROUP BY today (rung 2 of
#: the admission ladder, issue #14): Ontop is a complete SPARQL 1.1 endpoint
#: and the arango leg's transpiler ships aggregate goldens upstream. The
#: native snowflake/clickhouse BGP→SQL emitters have no GROUP BY — hardcoded
#: capability knowledge until the M11 capability registry (issue #15).
_AGGREGATION_CAPABLE_KINDS = {"postgresql", "arango"}

#: xsd numeric datatypes rendered bare in a pushed-down FILTER (``?v <= 1000``),
#: so the leg SPARQL stays engine-neutral rather than carrying ``"1000"^^xsd:…``.
_NUMERIC_XSD = {
    XSD.integer, XSD.decimal, XSD.double, XSD.float,
    XSD.long, XSD.int, XSD.short, XSD.byte,
    XSD.nonNegativeInteger, XSD.nonPositiveInteger,
    XSD.positiveInteger, XSD.negativeInteger,
    XSD.unsignedLong, XSD.unsignedInt, XSD.unsignedShort, XSD.unsignedByte,
}


def _check_supported(node: Any, found: set[str]) -> None:
    """Collect any hard-unsupported graph-pattern node names in the algebra."""
    if isinstance(node, CompValue):
        if node.name in _UNSUPPORTED_NODES:
            found.add(node.name)
        for value in node.values():
            _check_supported(value, found)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _check_supported(item, found)


def _describe_expr(expr: Any) -> str:
    """A short human tag for an unsupported FILTER expression (for the refusal)."""
    name = getattr(expr, "name", None)
    if name == "RelationalExpression":
        return f"`?x {expr.get('op', '?')} ?y` / non-literal comparison"
    return name or type(expr).__name__


def _collect_filter_conjuncts(expr: Any, out: list[_Conjunct]) -> None:
    """Collect ``?var op literal`` conjuncts from a FILTER expression.

    Accepts a single ``?var op literal`` (either operand order) and conjunctions
    (``&&``) of them. Anything else — disjunction, ``EXISTS``, ``REGEX``,
    ``?var op ?var``, a function call — is refused by name.
    """
    name = getattr(expr, "name", None)
    if name == "ConditionalAndExpression":
        _collect_filter_conjuncts(expr["expr"], out)
        for sub in expr["other"]:
            _collect_filter_conjuncts(sub, out)
        return
    if name == "RelationalExpression":
        left, op, right = expr["expr"], expr["op"], expr["other"]
        if op in _FILTER_OPS:
            if isinstance(left, Variable) and isinstance(right, Literal):
                out.append((left, op, right))
                return
            if isinstance(left, Literal) and isinstance(right, Variable):
                out.append((right, _FLIP_OP[op], left))
                return
    raise UnsupportedQueryError(
        "FILTER supports only conjunctions of `?var {<,<=,=,!=,>=,>} literal`; "
        f"got {_describe_expr(expr)}"
    )


def _is_trivial_leftjoin(expr: Any) -> bool:
    """True when an OPTIONAL carries no join condition (a bare ``OPTIONAL {…}``).

    A non-trivial condition (an ``OPTIONAL { … FILTER(…) }`` whose filter binds
    across the boundary) is refused — E1 pushes only self-contained groups.
    """
    return expr is None or getattr(expr, "name", None) == "TrueFilter"


def _collect_optional_bgp(node: Any, out: list[_TermTriple]) -> None:
    """Collect the triples of an OPTIONAL body, requiring it be a plain BGP."""
    name = getattr(node, "name", None)
    if name == "BGP":
        out.extend(node["triples"])
    elif name == "Join":
        _collect_optional_bgp(node["p1"], out)
        _collect_optional_bgp(node["p2"], out)
    else:
        raise UnsupportedQueryError(
            "OPTIONAL body must be a basic graph pattern (got "
            f"{name or type(node).__name__})"
        )


def _decompose(
    node: Any,
    required: list[_TermTriple],
    filters: list[_Conjunct],
    optional_groups: list[list[_TermTriple]],
) -> None:
    """Structured walk that separates the pattern into required BGP triples,
    FILTER conjuncts, and OPTIONAL groups. Refuses (by name) any shape E1 cannot
    push down into a single leg."""
    name = getattr(node, "name", None)
    if name == "BGP":
        required.extend(node["triples"])
    elif name == "Filter":
        _collect_filter_conjuncts(node["expr"], filters)
        _decompose(node["p"], required, filters, optional_groups)
    elif name == "LeftJoin":
        if not _is_trivial_leftjoin(node.get("expr")):
            raise UnsupportedQueryError("OPTIONAL with a join FILTER condition")
        _decompose(node["p1"], required, filters, optional_groups)
        group: list[_TermTriple] = []
        _collect_optional_bgp(node["p2"], group)
        optional_groups.append(group)
    elif name == "Join":
        _decompose(node["p1"], required, filters, optional_groups)
        _decompose(node["p2"], required, filters, optional_groups)
    elif name in _PATTERN_WRAPPERS:
        _decompose(node["p"], required, filters, optional_groups)
    else:
        raise UnsupportedQueryError(
            f"unsupported graph pattern: {name or type(node).__name__}"
        )


def _triple_vars(triple: _TermTriple) -> list[Variable]:
    return [t for t in triple if isinstance(t, Variable)]


def _route(
    triple: _TermTriple,
    catalog: SourceCatalog,
    var_source: dict[Variable, SourceRef],
) -> SourceRef | None:
    subject, predicate, obj = triple
    if predicate == RDF.type and isinstance(obj, URIRef):
        return catalog.source_of_class(str(obj))
    if isinstance(subject, Variable) and subject in var_source:
        return var_source[subject]
    if isinstance(predicate, URIRef):
        sources = catalog.sources_of_property(str(predicate))
        if len(sources) == 1:
            return next(iter(sources))
    return None


def _public(triple: _TermTriple) -> TriplePattern:
    s, p, o = triple
    return TriplePattern(subject=s.n3(), predicate=p.n3(), object=o.n3())


def _filter_literal(lit: Literal) -> str:
    """Render a FILTER literal: bare for xsd numerics / booleans, else n3()."""
    dt = lit.datatype
    if dt in _NUMERIC_XSD:
        return str(lit)
    if dt == XSD.boolean:
        return "true" if lit.toPython() else "false"
    return lit.n3()


def _serialize_filter(conjunct: _Conjunct) -> str:
    var, op, lit = conjunct
    return f"{var.n3()} {op} {_filter_literal(lit)}"


def _serialize(
    select_vars: list[str],
    triples: list[_TermTriple],
    filters: tuple[str, ...] = (),
    optional_groups: tuple[tuple[_TermTriple, ...], ...] = (),
) -> str:
    """Render one leg's self-contained SELECT: required triples, then FILTER
    lines, then OPTIONAL blocks. With no filters/optionals the output is
    byte-identical to a plain-BGP leg (the regression contract)."""
    lines = [f"  {s.n3()} {p.n3()} {o.n3()} ." for s, p, o in triples]
    for f in filters:
        lines.append(f"  FILTER({f})")
    for group in optional_groups:
        inner = "\n".join(f"    {s.n3()} {p.n3()} {o.n3()} ." for s, p, o in group)
        lines.append("  OPTIONAL {\n" + inner + "\n  }")
    body = "\n".join(lines)
    projection = " ".join(select_vars) if select_vars else "*"
    return f"SELECT {projection} WHERE {{\n{body}\n}}"


def _admit_single_leg_aggregation(
    algebra: Any, sparql: str, catalog: SourceCatalog
) -> PartitionPlan:
    """Admit a TOP-LEVEL aggregation whose whole pattern routes to ONE
    aggregation-capable source (issue #14 — rung 2 of the admission ladder).

    The winning leg receives the **original query verbatim**: the owning engine
    executes GROUP BY / aggregates / HAVING / ORDER itself, and the federator
    treats the returned rows as final bindings (no join stage — there is
    nothing to join). Cross-source aggregation stays refused: SPARQL aggregates
    are defined over the *joined* solution multiset, so aggregating per leg and
    joining afterwards is silently wrong under join multiplicity (the two-phase
    design over declared-unique join keys is issue #15).
    """
    projection = tuple(f"?{v}" for v in (algebra.get("PV") or []))

    # Descend to the Group node through aggregation plumbing only.
    node: Any = algebra
    group_node: Any = None
    while isinstance(node, CompValue):
        if node.name == "Group":
            group_node = node
            break
        if node.name not in _AGGREGATION_WRAPPERS:
            raise UnsupportedQueryError(
                "aggregation is supported only at the query's top level "
                f"(found {node.name} wrapping the GROUP BY)"
            )
        node = node.get("p")
    if group_node is None:
        raise UnsupportedQueryError("aggregation without a groupable pattern")

    # The grouped pattern itself must be an E1-decomposable shape (BGP +
    # simple FILTER conjuncts + self-contained OPTIONAL); _decompose refuses
    # anything else by name (a genuine BIND, UNION, nested aggregation, …).
    required: list[_TermTriple] = []
    filters: list[_Conjunct] = []
    optional_groups: list[list[_TermTriple]] = []
    _decompose(group_node["p"], required, filters, optional_groups)

    var_source: dict[Variable, SourceRef] = {}
    for subject, predicate, obj in required:
        if predicate == RDF.type and isinstance(obj, URIRef) and isinstance(subject, Variable):
            source = catalog.source_of_class(str(obj))
            if source is not None:
                var_source[subject] = source

    sources: set[SourceRef] = set()
    unroutable: list[_TermTriple] = []
    for triple in required + [t for g in optional_groups for t in g]:
        hit = _route(triple, catalog, var_source)
        if hit is None:
            unroutable.append(triple)
        else:
            sources.add(hit)
    if unroutable:
        missing = ", ".join(t[1].n3() for t in unroutable[:3])
        raise UnsupportedQueryError(
            f"aggregation references concept(s) no known source maps: {missing}"
        )
    if len(sources) != 1:
        ids = ", ".join(sorted(s.source_id for s in sources))
        raise UnsupportedQueryError(
            f"cross-source aggregation (pattern spans {ids}): SPARQL aggregates "
            "are defined over the JOINED solution multiset, so per-leg "
            "aggregation would be silently wrong — refused"
        )
    (source,) = sources
    if source.kind not in _AGGREGATION_CAPABLE_KINDS:
        raise UnsupportedQueryError(
            f"aggregation routes to {source.source_id} (kind {source.kind}), "
            "whose native leg does not emit GROUP BY; aggregation is supported "
            "today on kinds: " + ", ".join(sorted(_AGGREGATION_CAPABLE_KINDS))
        )

    # Leg variables: everything the pattern binds PLUS the projection (the
    # aggregate aliases, e.g. ?n, exist only in the engine's result — they must
    # be declared leg-supplied or the executor would report them unavailable).
    ordered_vars: list[str] = []
    present: set[str] = set()
    for triple in required + [t for g in optional_groups for t in g]:
        for var in _triple_vars(triple):
            name = f"?{var}"
            if name not in present:
                present.add(name)
                ordered_vars.append(name)
    for name in projection:
        if name not in present:
            present.add(name)
            ordered_vars.append(name)

    sub = SubQuery(
        source=source,
        triples=tuple(_public(t) for t in required),
        variables=tuple(ordered_vars),
        sparql=sparql,  # verbatim — the owning engine runs the aggregation
        filters=tuple(_serialize_filter(c) for c in filters),
        optional_groups=tuple(tuple(_public(t) for t in g) for g in optional_groups),
    )
    return PartitionPlan(
        sub_queries=(sub,), join_keys=(), projection=projection, unresolved=()
    )


def partition_query(sparql: str, catalog: SourceCatalog) -> PartitionPlan:
    """Partition a conceptual SPARQL query into per-source sub-queries.

    Args:
        sparql: a conceptual SPARQL SELECT over the master ontology.
        catalog: the concept→source index.

    Returns:
        A :class:`PartitionPlan`. Sub-queries are ordered by ``source_id`` for
        determinism; ``unresolved`` lists any triple whose concept/property maps
        to no known source.
    """
    algebra = prepareQuery(sparql).algebra
    projection = tuple(f"?{v}" for v in (algebra.get("PV") or []))

    # Refuse the hard-unsupported constructs first (nicely named) — except
    # aggregation, which E1.5 admits when the WHOLE query routes to one
    # aggregation-capable source (issue #14). The Extend nodes an aggregation
    # produces (binding aggregate results) are plumbing, not user BIND — the
    # admission path validates the wrapper chain itself.
    unsupported: set[str] = set()
    _check_supported(algebra, unsupported)
    if unsupported & {"Group", "AggregateJoin"}:
        return _admit_single_leg_aggregation(algebra, sparql, catalog)
    if unsupported:
        constructs = sorted(_UNSUPPORTED_NODES[n] for n in unsupported)
        raise UnsupportedQueryError(
            "E1 partitions basic graph patterns (+ single-leg FILTER/OPTIONAL) "
            "only; unsupported construct(s): " + ", ".join(constructs)
        )

    # Structured walk → required BGP, FILTER conjuncts, OPTIONAL groups.
    required: list[_TermTriple] = []
    filters: list[_Conjunct] = []
    optional_groups: list[list[_TermTriple]] = []
    _decompose(algebra, required, filters, optional_groups)

    # Pass 1 — class binding (from the required triples).
    var_source: dict[Variable, SourceRef] = {}
    for subject, predicate, obj in required:
        if predicate == RDF.type and isinstance(obj, URIRef) and isinstance(subject, Variable):
            source = catalog.source_of_class(str(obj))
            if source is not None:
                var_source[subject] = source

    # Pass 2 — route each required triple to a source bucket.
    buckets: dict[SourceRef, list[_TermTriple]] = {}
    unresolved: list[_TermTriple] = []
    for triple in required:
        source = _route(triple, catalog, var_source)
        if source is None:
            unresolved.append(triple)
        else:
            buckets.setdefault(source, []).append(triple)

    # Which source(s) bind each variable (from the required buckets).
    var_sources: dict[Variable, set[SourceRef]] = {}
    for source, source_triples in buckets.items():
        for triple in source_triples:
            for var in _triple_vars(triple):
                var_sources.setdefault(var, set()).add(source)
    join_keys = tuple(sorted(f"?{v}" for v, srcs in var_sources.items() if len(srcs) > 1))
    join_key_vars = {v for v, srcs in var_sources.items() if len(srcs) > 1}

    # FILTER placement — push each conjunct into the leg(s) binding its variable
    # (replicated across legs when the variable is a join key).
    source_filters: dict[SourceRef, list[_Conjunct]] = {}
    for conjunct in filters:
        var = conjunct[0]
        legs = var_sources.get(var, set())
        if not legs:
            raise UnsupportedQueryError(f"FILTER on unbound variable {var.n3()}")
        for src in legs:
            source_filters.setdefault(src, []).append(conjunct)

    # OPTIONAL placement — route each group to its single source leg.
    required_vars = {v for t in required for v in _triple_vars(t)}
    filter_vars = {c[0] for c in filters}
    routed_groups: list[tuple[SourceRef, list[_TermTriple]]] = []
    for group in optional_groups:
        routed: set[SourceRef] = set()
        for triple in group:
            hit = _route(triple, catalog, var_source)
            if hit is None:
                raise UnsupportedQueryError(
                    "OPTIONAL references a concept/property that maps to no source"
                )
            routed.add(hit)
        if len(routed) != 1:
            raise UnsupportedQueryError("cross-source OPTIONAL")
        (leg,) = routed  # exactly one source
        if leg not in buckets:
            raise UnsupportedQueryError("OPTIONAL group not connected to a required leg")
        routed_groups.append((leg, group))

    # Well-designedness guard. A variable an OPTIONAL *introduces* (not in its own
    # leg's required triples — the connector) must be local to that one group: it
    # may not also be bound by another leg's required triples, a FILTER, a join
    # key, or another OPTIONAL group. Otherwise the single-leg pushdown would make
    # a value that another leg needs *optional*, breaking the cross-source join.
    source_optionals: dict[SourceRef, list[list[_TermTriple]]] = {}
    for i, (src, group) in enumerate(routed_groups):
        connectors = {v for t in buckets[src] for v in _triple_vars(t)}
        other_optional_vars = {
            v
            for j, (_s, other) in enumerate(routed_groups)
            if j != i
            for t in other
            for v in _triple_vars(t)
        }
        for var in {v for t in group for v in _triple_vars(t)}:
            if var in connectors:
                continue  # links the OPTIONAL to its leg's required pattern
            if var in required_vars or var in filter_vars or var in join_key_vars \
                    or var in other_optional_vars:
                raise UnsupportedQueryError(
                    f"OPTIONAL variable {var.n3()} is not well-designed "
                    "(bound outside its OPTIONAL group)"
                )
        source_optionals.setdefault(src, []).append(group)

    # Build per-source sub-queries (stable order).
    sub_queries: list[SubQuery] = []
    for source in sorted(buckets, key=lambda s: s.source_id):
        source_triples = buckets[source]
        opt_groups = source_optionals.get(source, [])
        conjuncts = source_filters.get(source, [])

        ordered_vars: list[str] = []
        present: set[str] = set()
        for triple in source_triples:
            for var in _triple_vars(triple):
                name = f"?{var}"
                if name not in present:
                    present.add(name)
                    ordered_vars.append(name)
        # OPTIONAL projection variables ride the envelope like any column — they
        # must be in `variables` so the executor counts the leg as supplying them
        # (else a working optional column would be reported as unavailable).
        for group in opt_groups:
            for triple in group:
                for var in _triple_vars(triple):
                    name = f"?{var}"
                    if name not in present:
                        present.add(name)
                        ordered_vars.append(name)

        select_vars = [v for v in projection if v in present]
        for key in join_keys:
            if key in present and key not in select_vars:
                select_vars.append(key)

        filter_strs = tuple(_serialize_filter(c) for c in conjuncts)
        opt_public = tuple(tuple(_public(t) for t in group) for group in opt_groups)
        opt_terms = tuple(tuple(group) for group in opt_groups)

        sub_queries.append(
            SubQuery(
                source=source,
                triples=tuple(_public(t) for t in source_triples),
                variables=tuple(ordered_vars),
                sparql=_serialize(select_vars, source_triples, filter_strs, opt_terms),
                filters=filter_strs,
                optional_groups=opt_public,
            )
        )

    return PartitionPlan(
        sub_queries=tuple(sub_queries),
        join_keys=join_keys,
        projection=projection,
        unresolved=tuple(_public(t) for t in unresolved),
    )
