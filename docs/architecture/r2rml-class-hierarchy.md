# Note — class hierarchies in the Ontop / R2RML path

**Context.** `relational-schema-analyzer` and `arangodb-schema-analyzer` now discover class
abstractions (`rdfs:subClassOf`) through a shared `conceptual-taxonomy` library: single-table
inheritance, sibling tables with a shared property core, class-table inheritance, and ER
specialization. That changes what the OWL half of the OWL + R2RML pair carries, and this
deployment (`deploy/ontop`) is where it lands.

## The short version

**Nothing needs to change in the R2RML mapping.** R2RML has no slot for `rdfs:subClassOf` —
it maps rows to triples, it does not express terminology. The taxonomy belongs in the
ontology, and the ontology now has it.

That is not a gap; it is how OBDA is supposed to work. Ontop is fed both artifacts and does
the reasoning:

```bash
relational-schema-analyzer owl   --source postgresql --url "$DSN" -o ontology.ttl
relational-schema-analyzer r2rml --source postgresql --url "$DSN" -o mapping.ttl
```

Class and property IRIs are identical across the two, so a `?x a :Account` query resolves
against the *subtype* TriplesMaps via subclass reasoning, with no `Account` mapping and no
`account` table required.

## Why this is worth writing down

A synthesized abstract class has **no TriplesMap at all**, by design — there is no table
behind it. A reasonable person reading the R2RML file will notice `Account` is absent and
conclude the export is incomplete. It is not: the class exists only in the ontology, and the
extent is computed by Ontop as the union over its subclasses.

This is precisely the case OBDA handles well and a materializing pipeline handles badly — a
materializer would have to physically emit `Account` rows or lose the class.

## What to verify before relying on it

Unverified against this deployment. The reasoning is standard OWL 2 QL, which Ontop supports,
but three things are worth an actual query rather than an assumption:

1. **Subclass reasoning is enabled.** Ontop must be loading `ontology.ttl`, not only the
   mapping. Check the config actually passes both.
2. **`?x a :Account` returns the union of the subtype extents**, and the count matches the
   sum of the subtype counts (minus overlap, if the specialization is not disjoint).
3. **Multi-level hierarchies resolve transitively** — `Account → FinancialAccount →
   CheckingAccount` should mean a `:Account` query reaches checking accounts two hops up.

Item 3 matters most: the taxonomy library deliberately emits intermediate layers rather than
a flat parent, so a transitive-closure failure would silently under-return rather than error.

## Two things the analyzers now emit that Ontop could use

- **`owl:disjointWith`** between sibling subtypes, and a covering axiom on the parent — but
  **only when measured**, from key-overlap counts. Unmeasured stays `null`, never `false`.
  These are OWL 2 QL-compatible and would let Ontop prune query plans.
- **`sharedProperties` vs `partialProperties`.** A property present on every subclass is safe
  to aggregate over the parent extent; one present on only some is not. `SUM(balance)` across
  all accounts is sound, `SUM(monthlyPayment)` reads only mortgages and under-reports with no
  error. Ontop will happily answer the second query. If the fabric exposes aggregate queries
  over abstract classes, that distinction needs surfacing to the user rather than being
  silently correct-looking.

The second is the one with teeth. It is a correctness property, not a hint.

## Related

- `relational-schema-analyzer/docs/DESIGN-ADDENDUM-taxonomy.md` — the RSA side, including a
  proposed `shacl` export target
- `conceptual-taxonomy/docs/SPEC.md` §4.3.1 — how disjointness and completeness are earned
- `arango-schema-analyzer/docs/prd-patch-proposal-relational-and-taxonomy.md` — §6.4.1
  cross-reference, and the planned `sql` relational-view export where an abstract class would
  become a UNION view
