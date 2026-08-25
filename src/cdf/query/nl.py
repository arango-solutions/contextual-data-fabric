"""Natural-language → conceptual SPARQL front-end (M5 / D1).

Turns an English question into a conceptual SPARQL query the federation engine
can run. It **harvests the owned LLM client** from ``arango-sparql-py``'s
``nl2sparql`` package (provider-agnostic, env-driven — the same engine adapted
from ``arango-cypher-py``'s NL work) but grounds the prompt in the caller's
**authorized projection of the federation catalog** and validates the model's
output with **E1 ``partition_query``**, not an Arango-only AQL transpile — so an
authorized cross-source query is accepted.

Contract, tuned to the fabric's "refuse over guess" principle:

- The LLM is constrained to the catalog's concept IRIs and to the graph
  patterns E1 can partition — basic graph patterns plus single-leg
  FILTER/OPTIONAL (E1 still refuses UNION/MINUS/BIND/aggregation/…).
- The output is validated by partitioning it: a query that references unknown
  concepts (``plan.unresolved``) or won't route triggers a **repair round**;
  after ``max_repairs`` it is **refused**, never passed through as a guess.

The LLM client is injected (a ``ScriptedLLMClient`` in tests), so the grounding,
validation and repair logic is fully testable without a provider or network.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cdf.eval.nl_corpus import CorpusExample, FewShotRetriever

from .catalog import SourceCatalog
from .planner import UnsupportedQueryError, partition_query

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NlResult:
    """Outcome of an NL → SPARQL translation."""

    question: str
    sparql: str | None
    ok: bool
    warnings: tuple[str, ...] = ()
    llm_calls: int = 0
    error: str | None = None


def default_client() -> Any | None:
    """Build the env-driven LLM client from ``arango-sparql-py``, or ``None``.

    Returns ``None`` when the engine isn't installed or no API key is configured
    (``NL2SPARQL_API_KEY`` / ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``) — the
    caller then treats NL as unavailable and answers only prepared questions.

    A missing or mismatched NL dependency is **logged**, not silently swallowed.
    The import is guarded against any ``ImportError`` — a missing module *or* a
    symbol a stale ``arango-query-core`` pin can't supply (e.g. ``LabelIndex``) —
    so the service degrades to prepared-questions-only with a visible reason
    instead of a silent, hard-to-diagnose refusal.
    """
    try:
        from arango_sparql.nl2sparql.client import get_default_client
    except ImportError as exc:  # missing engine, or a stale/mismatched pin
        logger.warning(
            "NL front-end unavailable (%s); answering prepared questions only. "
            "Install the NL engine with the nl extra: "
            "pip install -e 'arango-sparql-py[nl]' (+ a compatible arango-query-core).",
            exc,
        )
        return None
    return get_default_client()


def build_system_prompt(
    catalog: SourceCatalog,
    *,
    vocabulary: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Ground the model in exactly the concepts the catalog knows."""
    base = catalog.concept_base
    lines = [
        "You translate a user's question into ONE SPARQL 1.1 SELECT query over a",
        "conceptual ontology that federates several data sources. Output ONLY the",
        "SPARQL query — no prose, no explanation.",
        "",
        f"Every class and property IRI is under <{base}...>. Use ONLY the concepts",
        "listed below. Each class lists ITS OWN properties — a property may be",
        "used only on a subject typed as the class that owns it:",
        "",
    ]
    for src in vocabulary if vocabulary is not None else catalog.vocabulary():
        lines.append(f"# source {src['source_id']} ({src['kind']})")
        for cls in src["classes"]:
            props = ", ".join(cls["properties"]) or "(no properties)"
            lines.append(f"  class {cls['name']} — properties: {props}")
        if src.get("relationships"):
            lines.append("  relationships: " + ", ".join(src["relationships"]))
    lines += [
        "",
        "Rules:",
        f"- Prefix: PREFIX c: <{base}>  and write concepts as c:Name.",
        "- Type every subject: `?x a c:ClassName`.",
        "- Use on a subject ONLY the properties listed under ITS class above. Do",
        "  not borrow a property from another class (that yields an empty answer).",
        "- Allowed patterns: basic triples, plus SIMPLE filters and optionals:",
        "  * FILTER of the form `FILTER(?var op literal)` with op one of",
        "    < <= = != >= > — conjunctions with && are fine. NOTHING else in a",
        "    FILTER: no REGEX/CONTAINS/functions, no ||, no `?x op ?y`.",
        "  * OPTIONAL { ... } whose triples all belong to ONE source's class(es)",
        "    and introduce only variables used nowhere else.",
        "- Do NOT use UNION, MINUS, BIND, GRAPH, subqueries, or aggregation",
        "  (COUNT/SUM/GROUP BY) — those are refused by the engine.",
        "- To join across sources, reuse the SAME variable for a shared property",
        "  that both entities carry (e.g. c:account_id) — that is the join key.",
        '- When the question refers to an entity (e.g. "for each account"), return',
        "  its human-readable name/label (e.g. c:account_name), NOT just an opaque",
        "  id — join across sources on the shared key when that name lives in a",
        "  different source than the rest of the data.",
        "- Add only triples needed to answer; each added triple must use a",
        "  property that exists on its subject's class (listed above).",
        "- EVERY variable in SELECT must be bound by a triple in WHERE.",
        "",
        "Shape for a cross-source question — a shared-key variable does the join,",
        "and each SELECT variable is bound by a triple:",
        "  SELECT ?labelA ?propB WHERE {",
        "    ?a a c:ClassA ; c:shared_key ?k ; c:labelA ?labelA .",
        "    ?b a c:ClassB ; c:shared_key ?k ; c:propB  ?propB .",
        "  }",
        "A range condition rides a simple FILTER on a bound variable:",
        "  SELECT ?labelA ?amount WHERE {",
        "    ?a a c:ClassA ; c:labelA ?labelA ; c:amount ?amount .",
        "    FILTER(?amount > 1000)",
        "  }",
    ]
    return "\n".join(lines)


class AuthorizedFewShotRetriever:
    """Filter prompt examples to an already-authorized vocabulary projection."""

    def __init__(
        self,
        retriever: FewShotRetriever,
        vocabulary: Sequence[Mapping[str, Any]],
        concept_base: str,
    ) -> None:
        self.retriever = retriever
        self.concept_base = concept_base
        self.source_ids = frozenset(str(item["source_id"]) for item in vocabulary)
        self.concepts = frozenset(
            str(concept["name"])
            for item in vocabulary
            for concept in item.get("classes", ())
        )
        self.properties = frozenset(
            str(prop)
            for item in vocabulary
            for concept in item.get("classes", ())
            for prop in concept.get("properties", ())
        )
        self.relationships = frozenset(
            str(relationship)
            for item in vocabulary
            for relationship in item.get("relationships", ())
        )

    def retrieve(self, question: str, *, top_k: int) -> Sequence[CorpusExample]:
        if top_k <= 0:
            return ()
        corpus = getattr(self.retriever, "corpus", None)
        available = len(getattr(corpus, "examples", ()))
        candidate_limit = max(top_k, available, top_k * 10)
        examples = self.retriever.retrieve(question, top_k=candidate_limit)
        allowed_names = self.concepts | self.properties | self.relationships
        filtered: list[CorpusExample] = []
        for example in examples:
            if example.refusal or example.sparql is None:
                continue
            if not set(example.expected_sources).issubset(self.source_ids):
                continue
            referenced = frozenset(
                re.findall(r"\bc:([A-Za-z_][A-Za-z0-9_]*)", example.sparql)
            )
            referenced |= frozenset(
                re.findall(
                    rf"<{re.escape(self.concept_base)}([^>]+)>",
                    example.sparql,
                )
            )
            if not referenced.issubset(allowed_names):
                continue
            filtered.append(example)
            if len(filtered) == top_k:
                break
        return tuple(filtered)


def build_few_shot_section(
    question: str,
    retriever: FewShotRetriever | None,
    *,
    top_k: int,
) -> str:
    """Render retrieved examples as prompt guidance, never executable routing."""
    if retriever is None or top_k <= 0:
        return ""
    examples = retriever.retrieve(question, top_k=top_k)
    if not examples:
        return ""
    lines = [
        "",
        "Trusted examples (guidance only; do not copy entity-specific constants",
        "unless the current user explicitly supplied the same entity):",
    ]
    for example in examples:
        answer = "REFUSE" if example.refusal else (example.sparql or "REFUSE")
        lines.extend(
            [
                f"Example question: {example.question}",
                f"Example answer: {answer}",
            ]
        )
    return "\n".join(lines)


_SPARQL_FENCE = re.compile(r"```(?:sparql)?\s*(.+?)```", re.IGNORECASE | re.DOTALL)
_SPARQL_START = re.compile(r"(?is)\b(PREFIX|SELECT|ASK|CONSTRUCT|DESCRIBE)\b")


def extract_sparql(text: str) -> str:
    """Pull the SPARQL query out of an LLM reply (fenced or bare)."""
    if not text:
        return ""
    fenced = _SPARQL_FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    match = _SPARQL_START.search(candidate)
    return candidate[match.start():].strip() if match else candidate.strip()


def _unresolved_feedback(plan: Any) -> str:
    triples = "; ".join(f"{t.subject} {t.predicate} {t.object}" for t in plan.unresolved)
    return (
        "These pattern(s) reference concepts not in the catalog: "
        f"{triples}. Use ONLY the listed concept IRIs."
    )


def nl_to_sparql(
    question: str,
    catalog: SourceCatalog,
    *,
    client: Any,
    max_repairs: int = 2,
    few_shot_retriever: FewShotRetriever | None = None,
    few_shot_top_k: int = 3,
    vocabulary: Sequence[Mapping[str, Any]] | None = None,
) -> NlResult:
    """Translate ``question`` to a catalog-grounded, partition-valid SPARQL query.

    Args:
        question: the natural-language question.
        catalog: the federation catalog (grounds the prompt + validates output).
        client: an ``LLMClient`` (``generate(messages) -> response.content``).
        max_repairs: how many times to feed a validation error back to the model.
        few_shot_retriever: optional prompt-example retrieval seam. Retrieved
            queries are context only and are never returned directly.
        few_shot_top_k: maximum prompt examples to retrieve.
        vocabulary: optional policy-projected vocabulary used for prompt
            construction. Query validation still uses the full local catalog;
            callers must retain final plan authorization.

    Returns:
        An :class:`NlResult`. ``ok`` is ``True`` only when the query parsed, used
        known concepts, and routed to ≥1 source; otherwise it is **refused**.
    """
    system_prompt = build_system_prompt(
        catalog,
        vocabulary=vocabulary,
    ) + build_few_shot_section(
        question,
        few_shot_retriever,
        top_k=few_shot_top_k,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]
    warnings: list[str] = []
    calls = 0
    last_sparql: str | None = None

    for attempt in range(max_repairs + 1):
        response = client.generate(messages)
        calls += 1
        content = str(getattr(response, "content", "") or "")
        sparql = extract_sparql(content)
        last_sparql = sparql or last_sparql

        if not sparql:
            feedback = "No SPARQL found. Reply with ONLY a SPARQL SELECT query."
        else:
            try:
                plan = partition_query(sparql, catalog)
            except UnsupportedQueryError as exc:
                feedback = (
                    f"{exc} Allowed: basic triples, FILTER(?var op literal) "
                    "conjunctions, and single-source OPTIONAL groups. Not "
                    "allowed: UNION/MINUS/BIND/GRAPH/subqueries/aggregation, "
                    "REGEX or other filter functions, and cross-source OPTIONAL."
                )
            except Exception as exc:  # noqa: BLE001 — surface any parse error to the model
                feedback = (
                    f"The query failed to parse ({type(exc).__name__}: {exc}). "
                    "Return valid SPARQL 1.1."
                )
            else:
                bound = {v for sq in plan.sub_queries for v in sq.variables}
                unbound = [v for v in plan.projection if v not in bound]
                if plan.unresolved:
                    feedback = _unresolved_feedback(plan)
                elif not plan.sub_queries:
                    feedback = (
                        "The query didn't route to any source. Type each subject "
                        "with `a c:ClassName` using the listed concepts."
                    )
                elif unbound:
                    # Projected a variable no triple produces -> the executor
                    # would refuse. Give the model a chance to bind or drop it.
                    feedback = (
                        f"SELECT variable(s) {', '.join(unbound)} are not bound by "
                        "any triple. Add a triple that produces each (a property "
                        "that exists on its class), or remove it from SELECT."
                    )
                else:
                    return NlResult(
                        question=question,
                        sparql=sparql,
                        ok=True,
                        warnings=tuple(warnings),
                        llm_calls=calls,
                    )

        if attempt < max_repairs:
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": feedback})
            warnings.append(f"repair {attempt + 1}: {feedback[:80]}")

    return NlResult(
        question=question,
        sparql=last_sparql,
        ok=False,
        warnings=tuple(warnings),
        llm_calls=calls,
        error="could not ground the question in the catalog after repairs",
    )
