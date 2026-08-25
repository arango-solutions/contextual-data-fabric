"""Grounding & provenance envelope + cite-or-refuse gate (M5 / E3).

Wraps a :class:`~cdf.query.executor.FederatedResult` into a **grounded, cited
answer envelope** and applies the **deterministic grounding gate**: refuse
rather than present an answer that isn't fully traceable to real source data.
This is the M5→M7 boundary — E3 produces the structured, gated envelope
(bindings + citations + retrieval path + status); M7 (customer-context) adds the
natural-language answer and the citation/traversal UI on top.

The gate (M7 FR-3/FR-6, PRD "no confident-wrong-answers"):

- **grounded** — every leg succeeded and every query pattern routed; the answer
  is fully cited.
- **refused** — the answer cannot be faithfully produced: a requested
  (projected) variable has no successful source, or (in the default strict mode)
  any leg failed / any pattern was unroutable. No bindings are returned.
- **partial** — only in the opt-in concierge mode (``allow_partial=True``) and
  only when every *projected* variable is still available: the answer is
  returned but a leg failure / dropped constraint is **declared**, never hidden.

Strict mode (default) is the cite-or-refuse contract; the ``allow_partial`` knob
mirrors the deterministic-vs-concierge split without branching the caller's
code.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from cdf.auth import RequestMetadata
from cdf.governance import (
    AuthorizationEvent,
    AuthorizationRefusal,
    MaskingKeyResolver,
    PlanAuthorization,
    mask_bindings,
    postflight_refusal,
)
from cdf.resolution import (
    ResolutionEvent,
    ResolutionLegMetrics,
    ResolutionPlanMetrics,
    ResolutionRefusal,
    ResolutionShortfall,
)

from .admission import AdmissionRefusal
from .assembly import AssemblyMetrics, AssemblyRefusal
from .executor import Binding, FederatedResult, PlanExecutionMetrics, RetrievalStep
from .optimizer import PlanEstimate


@dataclass(frozen=True)
class Citation:
    """One source-backed citation for the answer (M7 FR-2)."""

    source_id: str
    kind: str
    sparql: str
    native_query: str | None = None
    source_objects: tuple[str, ...] = ()
    as_of: str | None = None
    row_count: int = 0
    resolution_events: tuple[ResolutionEvent, ...] = ()
    resolution_metrics: ResolutionLegMetrics | None = None
    authorization_events: tuple[AuthorizationEvent, ...] = ()


@dataclass(frozen=True)
class NlMetrics:
    """How the question became conceptual SPARQL — cost/latency provenance (CC-6).

    ``path`` is ``"registry"`` for a prepared-question hit, ``"deterministic"``
    for an exact corpus route (both have zero LLM usage/cost), or ``"llm"`` for
    the provider-backed NL front-end.
    ``cost_usd`` is ``None`` when the (provider, model) pair is unpriced —
    unpriced is not free, so it is never reported as ``0.0``.
    """

    path: str  # "registry" | "deterministic" | "llm"
    duration_ms: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class AnswerEnvelope:
    """The gated, cited envelope E3 hands to M7."""

    status: str  # "grounded" | "partial" | "refused"
    bindings: tuple[Binding, ...]
    citations: tuple[Citation, ...]
    retrieval_path: tuple[RetrievalStep, ...]
    conceptual_sparql: str | None = None
    """The overall conceptual query (over the master ontology) the plan was
    partitioned from — provenance for the whole answer, alongside the per-leg
    queries in :attr:`retrieval_path`. Set by the service layer."""
    refusal_reason: str | None = None
    failed_sources: tuple[str, ...] = ()
    unavailable_vars: tuple[str, ...] = ()
    unresolved: tuple[object, ...] = ()
    nl_metrics: NlMetrics | None = None
    """Translation provenance for the question path (registry hit or LLM call);
    ``None`` when the caller sent conceptual SPARQL directly. Set by the
    service layer."""
    execution_metrics: PlanExecutionMetrics | None = None
    """Plan and per-source execution telemetry; ``None`` when no plan ran."""
    plan_estimate: PlanEstimate | None = None
    """Inspectable deterministic optimizer estimate and physical strategy."""
    presentation: dict[str, Any] | None = None
    """Advisory presentation hint parsed from the question (issue #17) — how
    the caller asked to SEE the answer (e.g. {"requested": "pie"}). Carries no
    truth content: it lives beside the bindings, never affects them, never
    rescues a refusal, and renderers validate it against the result shape."""
    admission_refusal: AdmissionRefusal | None = None
    """Structured preflight/runtime refusal, when a configured cap denied work."""
    resolution_events: tuple[ResolutionEvent, ...] = ()
    resolution_shortfalls: tuple[ResolutionShortfall, ...] = ()
    resolution_metrics: ResolutionPlanMetrics = ResolutionPlanMetrics()
    resolution_refusal: ResolutionRefusal | None = None
    assembly_metrics: AssemblyMetrics = AssemblyMetrics(
        mode="virtual", cleanup_status="not_applicable"
    )
    """Execution-mode, temporary-materialization, TTL, and cleanup telemetry."""
    assembly_refusal: AssemblyRefusal | None = None
    """Structured assembled-mode setup, budget, runtime, or cleanup refusal."""
    request_metadata: RequestMetadata | None = None
    """Secret-free query identity metadata; never contains bearer material."""
    withheld_sources: tuple[str, ...] = ()
    policy_ids: tuple[str, ...] = ()
    refusal_class: str | None = None
    authorization_refusal: AuthorizationRefusal | None = None
    authorization_events: tuple[AuthorizationEvent, ...] = ()

    @property
    def is_grounded(self) -> bool:
        return self.status == "grounded"

    @property
    def is_refused(self) -> bool:
        return self.status == "refused"


def _source_disclosed(authorization: PlanAuthorization | None, source_id: str) -> bool:
    if authorization is None:
        return True
    return all(
        item.disclose_source
        for item in authorization.decisions
        if item.source_id == source_id
    )


def _governed_path(result: FederatedResult) -> tuple[RetrievalStep, ...]:
    return tuple(
        step
        if _source_disclosed(result.authorization, step.source_id)
        else RetrievalStep(
            source_id="withheld",
            kind="withheld",
            sparql="",
            status=step.status,
            row_count=0,
            error="withheld" if step.error is not None else None,
            authorization_events=step.authorization_events,
        )
        for step in result.retrieval_path
    )


def _citations(result: FederatedResult) -> tuple[Citation, ...]:
    return tuple(
        Citation(
            source_id=(
                step.source_id
                if _source_disclosed(result.authorization, step.source_id)
                else "withheld"
            ),
            kind=(
                step.kind
                if _source_disclosed(result.authorization, step.source_id)
                else "withheld"
            ),
            sparql=(
                step.sparql
                if _source_disclosed(result.authorization, step.source_id)
                else ""
            ),
            native_query=(
                step.native_query
                if _source_disclosed(result.authorization, step.source_id)
                else None
            ),
            source_objects=(
                step.source_objects
                if _source_disclosed(result.authorization, step.source_id)
                else ()
            ),
            as_of=(
                step.as_of
                if _source_disclosed(result.authorization, step.source_id)
                else None
            ),
            row_count=(
                step.row_count
                if _source_disclosed(result.authorization, step.source_id)
                else 0
            ),
            resolution_events=step.resolution_events,
            resolution_metrics=step.resolution_metrics,
            authorization_events=step.authorization_events,
        )
        for step in result.retrieval_path
        if step.status == "ok"
    )


def ground(
    result: FederatedResult,
    *,
    allow_partial: bool = False,
    postflight_authorization: PlanAuthorization | None = None,
    masking_key_resolver: MaskingKeyResolver | None = None,
) -> AnswerEnvelope:
    """Apply the grounding gate to a federated result.

    Args:
        result: the :class:`FederatedResult` from
            :func:`cdf.query.execute_plan`.
        allow_partial: when ``True`` (concierge mode), return a **partial**
            answer if a leg failed or a pattern was unroutable but every
            *projected* variable is still available — with the shortfall
            declared. When ``False`` (default, strict cite-or-refuse), any such
            shortfall refuses.

    Returns:
        An :class:`AnswerEnvelope`. ``bindings`` is empty when refused.
    """
    governed_path = _governed_path(result)
    citations = _citations(result)
    authorization_refusal = result.authorization_refusal
    if authorization_refusal is None and result.authorization is not None:
        authorization_refusal = postflight_refusal(
            result.authorization,
            postflight_authorization,
        )
    masked_bindings = result.bindings
    if authorization_refusal is None and result.authorization is not None:
        masked_bindings, authorization_refusal = mask_bindings(
            result.bindings,
            result.authorization,
            masking_key_resolver,
        )
    if authorization_refusal is not None:
        return AnswerEnvelope(
            status="refused",
            bindings=(),
            citations=citations,
            retrieval_path=governed_path,
            refusal_reason=authorization_refusal.message,
            failed_sources=result.failed_sources,
            unavailable_vars=result.unavailable_vars,
            unresolved=result.unresolved,
            execution_metrics=result.execution_metrics,
            plan_estimate=result.plan_estimate,
            admission_refusal=result.admission_refusal,
            resolution_events=result.resolution_events,
            resolution_shortfalls=result.resolution_shortfalls,
            resolution_metrics=result.resolution_metrics,
            resolution_refusal=result.resolution_refusal,
            assembly_metrics=result.assembly_metrics,
            assembly_refusal=result.assembly_refusal,
            request_metadata=result.request_metadata,
            withheld_sources=result.withheld_sources,
            policy_ids=(
                result.authorization.policy_ids if result.authorization is not None else ()
            ),
            refusal_class=authorization_refusal.refusal_class,
            authorization_refusal=authorization_refusal,
            authorization_events=result.authorization_events,
        )
    dropped = tuple(
        item.variable
        for item in (result.authorization.masking_rules if result.authorization else ())
        if item.mode == "drop"
    )
    result = replace(
        result,
        bindings=masked_bindings,
        retrieval_path=governed_path,
        partial=result.partial or bool(dropped),
        unavailable_vars=tuple(dict.fromkeys((*result.unavailable_vars, *dropped))),
    )

    if result.resolution_refusal is not None:
        return AnswerEnvelope(
            status="refused",
            bindings=(),
            citations=citations,
            retrieval_path=result.retrieval_path,
            refusal_reason=result.resolution_refusal.message,
            failed_sources=result.failed_sources,
            unavailable_vars=result.unavailable_vars,
            unresolved=result.unresolved,
            execution_metrics=result.execution_metrics,
            plan_estimate=result.plan_estimate,
            admission_refusal=result.admission_refusal,
            resolution_events=result.resolution_events,
            resolution_shortfalls=result.resolution_shortfalls,
            resolution_metrics=result.resolution_metrics,
            resolution_refusal=result.resolution_refusal,
            assembly_metrics=result.assembly_metrics,
            assembly_refusal=result.assembly_refusal,
            request_metadata=result.request_metadata,
            withheld_sources=result.withheld_sources,
            policy_ids=(
                result.authorization.policy_ids if result.authorization is not None else ()
            ),
            authorization_events=result.authorization_events,
        )

    if result.assembly_refusal is not None:
        return AnswerEnvelope(
            status="refused",
            bindings=(),
            citations=citations,
            retrieval_path=result.retrieval_path,
            refusal_reason=result.assembly_refusal.message,
            failed_sources=result.failed_sources,
            unavailable_vars=result.unavailable_vars,
            unresolved=result.unresolved,
            execution_metrics=result.execution_metrics,
            plan_estimate=result.plan_estimate,
            admission_refusal=result.admission_refusal,
            resolution_events=result.resolution_events,
            resolution_shortfalls=result.resolution_shortfalls,
            resolution_metrics=result.resolution_metrics,
            resolution_refusal=result.resolution_refusal,
            assembly_metrics=result.assembly_metrics,
            assembly_refusal=result.assembly_refusal,
            request_metadata=result.request_metadata,
            withheld_sources=result.withheld_sources,
            policy_ids=(
                result.authorization.policy_ids if result.authorization is not None else ()
            ),
            authorization_events=result.authorization_events,
        )

    if result.admission_refusal is not None:
        return AnswerEnvelope(
            status="refused",
            bindings=(),
            citations=citations,
            retrieval_path=result.retrieval_path,
            refusal_reason=result.admission_refusal.message,
            failed_sources=result.failed_sources,
            unavailable_vars=result.unavailable_vars,
            unresolved=result.unresolved,
            execution_metrics=result.execution_metrics,
            plan_estimate=result.plan_estimate,
            admission_refusal=result.admission_refusal,
            resolution_events=result.resolution_events,
            resolution_shortfalls=result.resolution_shortfalls,
            resolution_metrics=result.resolution_metrics,
            resolution_refusal=result.resolution_refusal,
            assembly_metrics=result.assembly_metrics,
            assembly_refusal=result.assembly_refusal,
            request_metadata=result.request_metadata,
            withheld_sources=result.withheld_sources,
            policy_ids=(
                result.authorization.policy_ids if result.authorization is not None else ()
            ),
            authorization_events=result.authorization_events,
        )

    reasons: list[str] = []
    if result.unavailable_vars:
        reasons.append(
            "no source can supply requested variable(s): "
            + ", ".join(result.unavailable_vars)
        )
    if result.failed_sources:
        reasons.append("source leg(s) failed: " + ", ".join(result.failed_sources))
    if result.unresolved:
        reasons.append(
            f"{len(result.unresolved)} query pattern(s) mapped to no known source"
        )
    if result.resolution_shortfalls:
        reasons.append(
            "runtime resolution removed "
            f"{sum(item.count for item in result.resolution_shortfalls)} row(s): "
            + ", ".join(
                f"{item.reason}={item.count}" for item in result.resolution_shortfalls
            )
        )

    if not result.partial:
        return AnswerEnvelope(
            status="grounded",
            bindings=result.bindings,
            citations=citations,
            retrieval_path=result.retrieval_path,
            failed_sources=result.failed_sources,
            unavailable_vars=result.unavailable_vars,
            unresolved=result.unresolved,
            execution_metrics=result.execution_metrics,
            plan_estimate=result.plan_estimate,
            resolution_events=result.resolution_events,
            resolution_shortfalls=result.resolution_shortfalls,
            resolution_metrics=result.resolution_metrics,
            resolution_refusal=result.resolution_refusal,
            assembly_metrics=result.assembly_metrics,
            assembly_refusal=result.assembly_refusal,
            request_metadata=result.request_metadata,
            withheld_sources=result.withheld_sources,
            policy_ids=(
                result.authorization.policy_ids if result.authorization is not None else ()
            ),
            authorization_events=result.authorization_events,
        )

    # Partial: a requested column with no source is always load-bearing; and in
    # strict mode ANY shortfall refuses (a dropped constraint can silently
    # broaden the answer — we don't present that as complete).
    dropped_variables = {
        item.variable
        for item in (result.authorization.masking_rules if result.authorization else ())
        if item.mode == "drop"
    }
    load_bearing = (
        bool(set(result.unavailable_vars) - dropped_variables) or not allow_partial
    )
    if load_bearing:
        return AnswerEnvelope(
            status="refused",
            bindings=(),
            citations=citations,
            retrieval_path=result.retrieval_path,
            refusal_reason="; ".join(reasons) or "answer is not fully grounded",
            failed_sources=result.failed_sources,
            unavailable_vars=result.unavailable_vars,
            unresolved=result.unresolved,
            execution_metrics=result.execution_metrics,
            plan_estimate=result.plan_estimate,
            resolution_events=result.resolution_events,
            resolution_shortfalls=result.resolution_shortfalls,
            resolution_metrics=result.resolution_metrics,
            resolution_refusal=result.resolution_refusal,
            assembly_metrics=result.assembly_metrics,
            assembly_refusal=result.assembly_refusal,
            request_metadata=result.request_metadata,
            withheld_sources=result.withheld_sources,
            policy_ids=(
                result.authorization.policy_ids if result.authorization is not None else ()
            ),
            authorization_events=result.authorization_events,
        )

    return AnswerEnvelope(
        status="partial",
        bindings=result.bindings,
        citations=citations,
        retrieval_path=result.retrieval_path,
        refusal_reason=None,
        failed_sources=result.failed_sources,
        unavailable_vars=result.unavailable_vars,
        unresolved=result.unresolved,
        execution_metrics=result.execution_metrics,
        plan_estimate=result.plan_estimate,
        resolution_events=result.resolution_events,
        resolution_shortfalls=result.resolution_shortfalls,
        resolution_metrics=result.resolution_metrics,
        resolution_refusal=result.resolution_refusal,
        assembly_metrics=result.assembly_metrics,
        assembly_refusal=result.assembly_refusal,
        request_metadata=result.request_metadata,
        withheld_sources=result.withheld_sources,
        policy_ids=(
            result.authorization.policy_ids if result.authorization is not None else ()
        ),
        authorization_events=result.authorization_events,
    )
