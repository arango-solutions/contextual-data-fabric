"""The engine's HTTP seam: ``POST /federate`` → grounded envelope (M5/M9).

This is the thin service the demo UI (M9) calls — the library pipeline
(:func:`cdf.query.partition_query` → :func:`cdf.query.execute_plan` →
:func:`cdf.query.ground`) behind one endpoint. It owns **no** query logic.

Two ways in, per the P1 plan:

- ``sparql`` — a conceptual query, run as-is (the D1 NL front-end will emit
  these; agents and tests can already send them).
- ``question`` — natural language, resolved against a **prepared-questions
  registry** (the M9 "pre-run" demo mode: the seed questions mapped to their
  conceptual queries). An unknown question is *refused, not guessed* — the
  honest answer until WP-D1 wires the LLM front-end.

Credentials stay in the engine per CC-7: executors are built from environment
variables by :func:`FederationService.from_env`; the HTTP surface never sees a
connection string.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from cdf.auth import (
    ANONYMOUS_DEV_PRINCIPAL,
    AuthenticatedPrincipal,
    AuthenticationError,
    OIDCVerifier,
    RequestContext,
    anonymous_request_context,
    normalize_purpose,
    normalize_request_identifier,
)
from cdf.catalog import FileCatalogLoader
from cdf.connectors import (
    BaseSourceIdentity,
    ConnectorRef,
    ConnectorRegistry,
    DelegationBroker,
    EnvSecretResolver,
    FileSecretResolver,
    SourceAuthMode,
    resolver_from_env,
)
from cdf.connectors.factory import executor_builder
from cdf.connectors.redaction import SecretValueLease, register_secret_values
from cdf.eval.nl_corpus import (
    DeterministicCorpusRouter,
    FewShotRetriever,
    LexicalFewShotRetriever,
    load_nl_corpus,
    normalize_question,
)
from cdf.governance import (
    AuthorizationRefusal,
    MaskingKeyResolver,
    NonePolicyPDP,
    PolicyDecisionPoint,
    ResourceRequest,
    authorize_plan,
    masking_key_resolver_from_env,
    policy_pdp_from_env,
)
from cdf.query import (
    ArangoAssemblyBackend,
    AssemblyBackend,
    AssemblyExecution,
    AssemblyMetrics,
    AssemblyPolicy,
    AssemblyRefusal,
    ExecutionMode,
    PlanAdmissionPolicy,
    SourceCatalog,
    estimate_plan,
    execute_plan,
    ground,
    partition_query,
)
from cdf.query.assembly import (
    AssemblyFailure,
    backend_refusal,
    cleanup_refusal,
    new_job_id,
)
from cdf.query.executor import SourceExecutor
from cdf.query.grounding import AnswerEnvelope, NlMetrics
from cdf.query.planner import UnsupportedQueryError
from cdf.query.presentation import split_presentation
from cdf.resolution import EntityResolver, ResolutionRefusal


@dataclass(frozen=True)
class _NlResolution:
    sparql: str | None
    metrics: NlMetrics | None
    error: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FederationService:
    """The wired engine: a catalog, executors, and optional prepared questions."""

    catalog: SourceCatalog
    executors: Mapping[str, SourceExecutor]
    prepared_questions: Mapping[str, str] = field(default_factory=dict)
    nl_client: Any | None = None
    """Optional LLM client (WP-D1). When set, an unregistered question is
    translated to conceptual SPARQL via :func:`cdf.query.nl.nl_to_sparql`."""
    deterministic_router: DeterministicCorpusRouter | None = None
    """Exact corpus router evaluated after the prepared-question registry."""
    few_shot_retriever: FewShotRetriever | None = None
    """Prompt-only example retriever used by the LLM fallback."""
    few_shot_top_k: int = 3
    admission_policy: PlanAdmissionPolicy = field(default_factory=PlanAdmissionPolicy)
    entity_resolver: EntityResolver | None = None
    assembly_backend: AssemblyBackend | None = None
    assembly_policy: AssemblyPolicy = field(default_factory=AssemblyPolicy)
    connector_registry: ConnectorRegistry | None = None
    source_credentials: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    unconfigured_sources: tuple[str, ...] = ()
    delegation_broker: DelegationBroker | None = None
    source_base_identities: Mapping[str, BaseSourceIdentity] = field(default_factory=dict)
    policy_pdp: PolicyDecisionPoint = field(default_factory=NonePolicyPDP)
    masking_key_resolver: MaskingKeyResolver | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    secret_value_leases: tuple[SecretValueLease, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def credential_health(self) -> dict[str, dict[str, Any]]:
        """Return safe, value-free source credential state."""
        if self.connector_registry is not None:
            result = self.connector_registry.health()
        else:
            result = {
                source_id: dict(value) for source_id, value in self.source_credentials.items()
            }
        for source_id in self.unconfigured_sources:
            result[source_id] = {
                "configured": False,
                "backend": None,
                "generation": None,
                "last_reload_status": "missing",
                "last_reload_time": None,
            }
        return result

    def authorized_sources(self, context: RequestContext) -> tuple[Any, ...]:
        """Return only source metadata whose existence policy permits disclosing."""
        sources = tuple(sorted(self.catalog.sources, key=lambda item: item.source_id))
        resources = tuple(
            ResourceRequest(
                source_id=item.source_id,
                resource_type="source",
                resource_id=item.source_id,
                usage="introspection",
            )
            for item in sources
        )
        authorization = self.policy_pdp.authorize(
            resources,
            context,
            catalog_generation=self.catalog.manifest_generation,
        )
        return tuple(
            source
            for source, decision in zip(sources, authorization.decisions, strict=True)
            if decision.action != "deny" and decision.disclose_source
        )

    def authorized_vocabulary(self, context: RequestContext) -> list[dict[str, Any]]:
        """Filter classes/properties before exposing catalog vocabulary."""
        visible_sources = {
            item.source_id for item in self.authorized_sources(context)
        }
        result: list[dict[str, Any]] = []
        for entry in self.catalog.vocabulary():
            source_id = entry["source_id"]
            if source_id not in visible_sources:
                continue
            classes: list[dict[str, Any]] = []
            for concept in entry["classes"]:
                concept_request = ResourceRequest(
                    source_id=source_id,
                    resource_type="concept",
                    resource_id=concept["name"],
                    usage="introspection",
                )
                concept_decision = self.policy_pdp.authorize(
                    (concept_request,),
                    context,
                    catalog_generation=self.catalog.manifest_generation,
                ).decisions[0]
                if concept_decision.action == "deny" or not concept_decision.disclose_source:
                    continue
                properties: list[str] = []
                for property_name in concept["properties"]:
                    property_request = ResourceRequest(
                        source_id=source_id,
                        resource_type="property",
                        resource_id=property_name,
                        usage="introspection",
                    )
                    decision = self.policy_pdp.authorize(
                        (property_request,),
                        context,
                        catalog_generation=self.catalog.manifest_generation,
                    ).decisions[0]
                    if decision.action != "deny" and decision.disclose_source:
                        properties.append(property_name)
                classes.append({"name": concept["name"], "properties": properties})
            if classes:
                relationships: list[str] = []
                for relationship in entry["relationships"]:
                    relationship_request = ResourceRequest(
                        source_id=source_id,
                        resource_type="property",
                        resource_id=relationship,
                        usage="introspection",
                    )
                    decision = self.policy_pdp.authorize(
                        (relationship_request,),
                        context,
                        catalog_generation=self.catalog.manifest_generation,
                    ).decisions[0]
                    if decision.action != "deny" and decision.disclose_source:
                        relationships.append(relationship)
                result.append(
                    {
                        **entry,
                        "classes": classes,
                        "relationships": relationships,
                    }
                )
        return result

    def close(self) -> None:
        """Drain source clients and pools during service shutdown."""
        try:
            if self.connector_registry is not None:
                self.connector_registry.close()
            else:
                seen: set[int] = set()
                for executor in self.executors.values():
                    if id(executor) in seen:
                        continue
                    seen.add(id(executor))
                    lifecycle = getattr(executor, "drain", None)
                    if not callable(lifecycle):
                        lifecycle = getattr(executor, "close", None)
                    if callable(lifecycle):
                        lifecycle()
            close_assembly = getattr(self.assembly_backend, "close", None)
            if callable(close_assembly):
                close_assembly()
        finally:
            for lease in self.secret_value_leases:
                lease.close()

    def federate_sparql(
        self,
        sparql: str,
        *,
        allow_partial: bool = False,
        execution_mode: ExecutionMode = "virtual",
        context: RequestContext | None = None,
    ) -> AnswerEnvelope:
        request_context = context or anonymous_request_context()
        if execution_mode not in ("virtual", "assembled"):
            raise ValueError("execution_mode must be 'virtual' or 'assembled'")
        plan_started = perf_counter()
        plan = partition_query(sparql, self.catalog)
        partition_duration_ms = (perf_counter() - plan_started) * 1000
        source_auth_modes = self._source_auth_modes(plan)
        authorized = authorize_plan(
            plan,
            self.catalog,
            request_context,
            self.policy_pdp,
            source_auth_modes=source_auth_modes,
            allow_partial=allow_partial,
        )
        if authorized.refusal is not None:
            return self._authorization_refusal_envelope(
                sparql,
                authorized.refusal,
                execution_mode=execution_mode,
                context=request_context,
                policy_ids=authorized.authorization.policy_ids,
                withheld_sources=authorized.authorization.withheld_sources,
            )
        plan = authorized.plan
        estimate = estimate_plan(plan, self.catalog)
        resolution_refusal = self._resolution_preflight(plan)
        if resolution_refusal is not None:
            return AnswerEnvelope(
                status="refused",
                bindings=(),
                citations=(),
                retrieval_path=(),
                conceptual_sparql=sparql,
                refusal_reason=resolution_refusal.message,
                plan_estimate=estimate,
                resolution_refusal=resolution_refusal,
                assembly_metrics=AssemblyMetrics(
                    mode=execution_mode,
                    backend=(
                        self.assembly_backend.name
                        if execution_mode == "assembled"
                        and self.assembly_backend is not None
                        else None
                    ),
                    cleanup_status=(
                        "not_started"
                        if execution_mode == "assembled"
                        else "not_applicable"
                    ),
                ),
                request_metadata=request_context.safe_metadata(),
            )
        if execution_mode == "assembled":
            return self._federate_assembled(
                sparql,
                plan,
                estimate,
                plan_started=plan_started,
                partition_duration_ms=partition_duration_ms,
                allow_partial=allow_partial,
                context=request_context,
                authorization=authorized.authorization,
                authorization_resources=authorized.resources,
                source_auth_modes=source_auth_modes,
            )

        refusal = self.admission_policy.preflight(estimate)
        if refusal is not None:
            return AnswerEnvelope(
                status="refused",
                bindings=(),
                citations=(),
                retrieval_path=(),
                conceptual_sparql=sparql,
                refusal_reason=refusal.message,
                plan_estimate=estimate,
                admission_refusal=refusal,
                request_metadata=request_context.safe_metadata(),
            )
        result = execute_plan(
            plan,
            self.executors,
            strategy=estimate,
            admission_policy=self.admission_policy,
            allow_partial_limits=allow_partial,
            entity_resolver=self.entity_resolver,
            resolution_bindings=self._resolution_bindings(plan),
            request_context=request_context,
            source_auth_modes=source_auth_modes,
            delegation_broker=self.delegation_broker,
            source_base_identities=self.source_base_identities,
            plan_authorization=authorized.authorization,
        )
        if result.execution_metrics is not None:
            result = replace(
                result,
                execution_metrics=replace(
                    result.execution_metrics,
                    total_duration_ms=(perf_counter() - plan_started) * 1000,
                    partition_duration_ms=partition_duration_ms,
                ),
            )
        postflight = self.policy_pdp.authorize(
            authorized.resources,
            request_context,
            catalog_generation=self.catalog.manifest_generation,
        )
        envelope = ground(
            result,
            allow_partial=allow_partial,
            postflight_authorization=postflight,
            masking_key_resolver=self.masking_key_resolver,
        )
        # Carry the overall conceptual query as answer-level provenance
        # (the per-leg decompositions live in the retrieval path).
        return replace(
            envelope,
            conceptual_sparql=sparql,
            request_metadata=request_context.safe_metadata(),
        )

    def _federate_assembled(
        self,
        sparql: str,
        plan: Any,
        estimate: Any,
        *,
        plan_started: float,
        partition_duration_ms: float,
        allow_partial: bool,
        context: RequestContext,
        authorization: Any,
        authorization_resources: Any,
        source_auth_modes: Mapping[str, SourceAuthMode],
    ) -> AnswerEnvelope:
        """Execute one explicitly requested, bounded temporary assembly job."""
        if self.assembly_backend is None:
            refusal = AssemblyRefusal(
                code="assembly_backend_unconfigured",
                phase="preflight",
                message=(
                    "assembled execution is disabled or unconfigured; configure "
                    "CDF_ASSEMBLY_ENABLED and an Arango assembly backend"
                ),
            )
            return replace(
                _assembly_refusal_envelope(
                    sparql,
                    estimate,
                    refusal,
                    AssemblyMetrics(mode="assembled", cleanup_status="not_started"),
                ),
                request_metadata=context.safe_metadata(),
            )

        preflight_refusal = self.assembly_policy.preflight(estimate)
        if preflight_refusal is not None:
            return replace(
                _assembly_refusal_envelope(
                    sparql,
                    estimate,
                    preflight_refusal,
                    AssemblyMetrics(
                        mode="assembled",
                        backend=self.assembly_backend.name,
                        ttl_seconds=self.assembly_policy.ttl_seconds,
                        cleanup_status="not_started",
                    ),
                ),
                request_metadata=context.safe_metadata(),
            )

        job_id = new_job_id()
        try:
            job = self.assembly_backend.create_job(
                job_id,
                self.assembly_policy.ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            setup_refusal = backend_refusal(exc)
            return replace(
                _assembly_refusal_envelope(
                    sparql,
                    estimate,
                    setup_refusal,
                    AssemblyMetrics(
                        mode="assembled",
                        backend=self.assembly_backend.name,
                        job_id=job_id,
                        ttl_seconds=self.assembly_policy.ttl_seconds,
                        cleanup_status="setup_failed",
                        cleanup_error=setup_refusal.message,
                    ),
                ),
                request_metadata=context.safe_metadata(),
            )

        assembly = AssemblyExecution(
            job,
            backend_name=self.assembly_backend.name,
            policy=self.assembly_policy,
        )
        envelope: AnswerEnvelope | None = None
        execution_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        try:
            admission_refusal = self.admission_policy.preflight(estimate)
            if admission_refusal is not None:
                envelope = AnswerEnvelope(
                    status="refused",
                    bindings=(),
                    citations=(),
                    retrieval_path=(),
                    conceptual_sparql=sparql,
                    refusal_reason=admission_refusal.message,
                    plan_estimate=estimate,
                    admission_refusal=admission_refusal,
                    assembly_metrics=assembly.metrics(),
                    request_metadata=context.safe_metadata(),
                )
            else:
                result = execute_plan(
                    plan,
                    self.executors,
                    strategy=estimate,
                    admission_policy=self.admission_policy,
                    allow_partial_limits=allow_partial,
                    assembly=assembly,
                    entity_resolver=self.entity_resolver,
                    resolution_bindings=self._resolution_bindings(plan),
                    request_context=context,
                    source_auth_modes=source_auth_modes,
                    delegation_broker=self.delegation_broker,
                    source_base_identities=self.source_base_identities,
                    plan_authorization=authorization,
                )
                if result.execution_metrics is not None:
                    result = replace(
                        result,
                        execution_metrics=replace(
                            result.execution_metrics,
                            total_duration_ms=(perf_counter() - plan_started) * 1000,
                            partition_duration_ms=partition_duration_ms,
                        ),
                    )
                postflight = self.policy_pdp.authorize(
                    authorization_resources,
                    context,
                    catalog_generation=self.catalog.manifest_generation,
                )
                envelope = replace(
                    ground(
                        result,
                        allow_partial=allow_partial,
                        postflight_authorization=postflight,
                        masking_key_resolver=self.masking_key_resolver,
                    ),
                    conceptual_sparql=sparql,
                )
        except BaseException as exc:
            execution_error = exc
        finally:
            try:
                job.cleanup()
            except BaseException as exc:
                cleanup_error = exc

        if execution_error is not None:
            if cleanup_error is not None:
                raise AssemblyFailure(cleanup_refusal(cleanup_error)) from execution_error
            raise execution_error

        assert envelope is not None
        metrics = assembly.metrics(
            cleanup_status="failed" if cleanup_error is not None else "succeeded",
            cleanup_error=str(cleanup_error) if cleanup_error is not None else None,
        )
        cleanup_failure = cleanup_refusal(cleanup_error) if cleanup_error is not None else None
        return replace(
            _with_final_assembly(envelope, metrics, cleanup_failure),
            request_metadata=context.safe_metadata(),
        )

    def _resolve_question(
        self,
        question: str,
        context: RequestContext,
    ) -> _NlResolution:
        """Resolve prepared → exact corpus → LLM, otherwise refuse."""
        from cdf.service.metering import (
            DETERMINISTIC_METRICS,
            REGISTRY_METRICS,
            MeteredLLMClient,
        )

        sparql = self.prepared_questions.get(normalize_question(question))
        if sparql is not None:
            return _NlResolution(sparql=sparql, metrics=REGISTRY_METRICS)

        if self.deterministic_router is not None:
            route = self.deterministic_router.route(question)
            if route is not None:
                return _NlResolution(
                    sparql=route.sparql,
                    metrics=DETERMINISTIC_METRICS,
                    error=route.refusal_reason if route.refusal else None,
                )

        if self.nl_client is None:
            return _NlResolution(
                sparql=None,
                metrics=None,
                error=(
                    "question is not in the prepared-question registry or deterministic "
                    "corpus and no NL front-end is configured "
                    "(set NL2SPARQL_API_KEY, or send 'sparql')"
                ),
            )

        # WP-D1/D2: translate NL → conceptual SPARQL, grounded in the catalog,
        # enriched with prompt-only corpus examples, and validated by E1.
        from cdf.query.nl import AuthorizedFewShotRetriever, nl_to_sparql

        vocabulary = self.authorized_vocabulary(context)
        if not vocabulary:
            return _NlResolution(
                sparql=None,
                metrics=None,
                error="no authorized catalog vocabulary is available for NL translation",
            )
        authorized_retriever = (
            AuthorizedFewShotRetriever(
                self.few_shot_retriever,
                vocabulary,
                self.catalog.concept_base,
            )
            if self.few_shot_retriever is not None
            else None
        )

        meter = MeteredLLMClient(self.nl_client)
        result = nl_to_sparql(
            question,
            self.catalog,
            client=meter,
            few_shot_retriever=authorized_retriever,
            few_shot_top_k=self.few_shot_top_k,
            vocabulary=vocabulary,
        )
        return _NlResolution(
            sparql=result.sparql if result.ok else None,
            metrics=meter.metrics(),
            error=None if result.ok else (result.error or "could not translate the question"),
            warnings=result.warnings,
        )

    def resolve_question_for_preview(
        self,
        question: str,
        context: RequestContext,
    ) -> _NlResolution:
        """Resolve NL only when the resulting plan is authorized to be disclosed."""
        resolution = self._resolve_question(question, context)
        if resolution.sparql is None:
            return resolution
        try:
            plan = partition_query(resolution.sparql, self.catalog)
        except UnsupportedQueryError as exc:
            return replace(
                resolution,
                sparql=None,
                error=f"unsupported query construct: {exc}",
            )
        authorized = authorize_plan(
            plan,
            self.catalog,
            context,
            self.policy_pdp,
            source_auth_modes=self._source_auth_modes(plan),
            allow_partial=False,
        )
        if authorized.refusal is not None:
            return replace(
                resolution,
                sparql=None,
                error=authorized.refusal.message,
            )
        return resolution

    def _authorization_refusal_envelope(
        self,
        sparql: str | None,
        refusal: AuthorizationRefusal,
        *,
        execution_mode: ExecutionMode,
        context: RequestContext,
        policy_ids: tuple[str, ...] = (),
        withheld_sources: tuple[str, ...] = (),
    ) -> AnswerEnvelope:
        return AnswerEnvelope(
            status="refused",
            bindings=(),
            citations=(),
            retrieval_path=(),
            conceptual_sparql=sparql,
            refusal_reason=refusal.message,
            request_metadata=context.safe_metadata(),
            assembly_metrics=AssemblyMetrics(
                mode=execution_mode,
                cleanup_status=(
                    "not_started" if execution_mode == "assembled" else "not_applicable"
                ),
            ),
            withheld_sources=withheld_sources,
            policy_ids=policy_ids,
            refusal_class=refusal.refusal_class,
            authorization_refusal=refusal,
        )

    def _resolution_bindings(self, plan: Any) -> dict[str, Any]:
        return {
            subquery.source.source_id: binding
            for subquery in plan.sub_queries
            if (binding := self.catalog.resolution_for(subquery.source)) is not None
        }

    def _source_auth_modes(self, plan: Any) -> dict[str, SourceAuthMode]:
        """Resolve catalog auth mode; legacy CSI defaults to service identity."""
        result: dict[str, SourceAuthMode] = {}
        for subquery in plan.sub_queries:
            source_id = subquery.source.source_id
            metadata = self.catalog.auth_for(source_id)
            mode = metadata.mode if metadata is not None else "service"
            # ``none`` is a catalog/build-time value. A query connector still
            # runs under the service identity until delegated mode is explicit.
            if mode == "delegated":
                result[source_id] = "delegated"
            else:
                result[source_id] = "service"
        return result

    def _resolution_preflight(self, plan: Any) -> ResolutionRefusal | None:
        """Refuse configured resolution plans before source or assembly work."""
        for subquery in plan.sub_queries:
            source_id = subquery.source.source_id
            binding = self.catalog.resolution_for(source_id)
            if binding is None or binding.mode == "none":
                continue
            if self.entity_resolver is None:
                return ResolutionRefusal(
                    code="entity_resolver_unconfigured",
                    phase="preflight",
                    source_id=source_id,
                    reason="entity_resolver_unconfigured",
                    message=(
                        f"runtime resolution is configured for source {source_id} "
                        "but no CDF entity_resolver was injected"
                    ),
                )
            available = {
                variable[1:] if variable.startswith("?") else variable
                for variable in subquery.variables
            }
            # The join binding is always required. Scope/observable bindings
            # may be absent when every returned value is already canonical;
            # runtime guards remove/refuse any non-canonical row that cannot
            # form an honest scoped observation.
            required = {binding.join_variable}
            missing = sorted(
                variable
                for variable in required
                if variable is not None and variable not in available
            )
            if missing:
                return ResolutionRefusal(
                    code="resolution_bindings_missing",
                    phase="preflight",
                    source_id=source_id,
                    reason="resolution_bindings_missing",
                    message=(
                        f"source {source_id} runtime resolution requires query "
                        f"binding(s): {', '.join(missing)}"
                    ),
                )
            policy = getattr(self.entity_resolver, "policy", None)
            policy_fields = set(getattr(policy, "observable_fields", ()))
            configured_fields = set(binding.observable_bindings)
            if policy_fields and not configured_fields.issubset(policy_fields):
                unsupported = sorted(configured_fields - policy_fields)
                return ResolutionRefusal(
                    code="resolution_policy_mismatch",
                    phase="preflight",
                    source_id=source_id,
                    reason="non_observable_field",
                    message=(
                        f"source {source_id} config is not allowlisted by the injected "
                        f"resolver policy: {', '.join(unsupported)}"
                    ),
                )
        return None

    def federate_question(
        self,
        question: str,
        *,
        allow_partial: bool = False,
        execution_mode: ExecutionMode = "virtual",
        context: RequestContext | None = None,
    ) -> AnswerEnvelope:
        request_context = context or anonymous_request_context()
        if execution_mode not in ("virtual", "assembled"):
            raise ValueError("execution_mode must be 'virtual' or 'assembled'")
        # Split a trailing presentation directive ("... and display as a pie
        # chart") from the QUERY before resolution: the directive is advisory
        # metadata for renderers, never part of the conceptual query (#17).
        question, presentation = split_presentation(question)
        resolution = self._resolve_question(question, request_context)
        if resolution.sparql is None:
            return AnswerEnvelope(
                status="refused",
                bindings=(),
                citations=(),
                retrieval_path=(),
                refusal_reason=resolution.error or "could not translate the question",
                nl_metrics=resolution.metrics,
                assembly_metrics=AssemblyMetrics(
                    mode=execution_mode,
                    backend=(
                        self.assembly_backend.name
                        if execution_mode == "assembled" and self.assembly_backend is not None
                        else None
                    ),
                    cleanup_status=(
                        "not_started" if execution_mode == "assembled" else "not_applicable"
                    ),
                ),
                request_metadata=request_context.safe_metadata(),
                presentation=presentation,
            )
        envelope = self.federate_sparql(
            resolution.sparql,
            allow_partial=allow_partial,
            execution_mode=execution_mode,
            context=request_context,
        )
        return replace(
            envelope, nl_metrics=resolution.metrics, presentation=presentation
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> FederationService:
        """Wire logical source executors through the M1 resolver (CC-7/CC-8).

        - ``CDF_CATALOG_MANIFEST`` — authoritative file-backed catalog v1. When
          set, its validated CSI/R2RML paths replace the legacy directory paths.
        - ``CDF_CSI_DIR`` — legacy directory of ``*.json`` CSI v1 documents
          (default ``deploy/csi``). Every document becomes a catalog entry.
        - Arango leg (kind ``arango``): ``ARANGO_URL`` (+ ``ARANGO_DB``,
          ``ARANGO_USER``, ``ARANGO_PASSWORD``).
        - ClickHouse leg (kind ``clickhouse``): ``CLICKHOUSE_DSN`` + the
          r2g-emitted R2RML at ``CDF_R2RML_DIR/<source_id>.ttl`` (default
          ``deploy/r2rml``).
        - Snowflake leg (kind ``snowflake``, Option B / ADR-0002):
          ``SNOWFLAKE_ACCOUNT`` + ``_USER`` and exactly one of ``_PASSWORD`` or
          ``_PRIVATE_KEY_FILE`` (optional ``_PRIVATE_KEY_FILE_PWD``), plus
          ``_WAREHOUSE``/``_DATABASE``/``_SCHEMA``/``_ROLE`` and the same
          per-source R2RML file.
        - Relational leg (any other kind): ``ONTOP_SPARQL_ENDPOINT``. Optional
          ``ONTOP_REFORMULATE_ENDPOINT`` captures Ontop's generated PostgreSQL
          SQL as native-query provenance.
        - ``CDF_SECRET_BACKEND=env|file`` selects legacy/registry env or the
          production mounted-file resolver. File mode requires
          ``CDF_SECRET_REGISTRY_PATH`` and optionally ``CDF_SECRET_MOUNT_PATH``.
          ``CDF_SECRET_POLL_INTERVAL_SECONDS`` bounds generation checks.
        - ``CDF_STRICT_STARTUP=true`` refuses to start when any catalog source
          lacks resolved connector configuration. ``CDF_POLICY_REQUIRED=true``
          also enables this fail-fast behavior.
        - ``CDF_PREPARED_QUESTIONS`` — JSON file of ``{question: sparql}``.
        - ``CDF_NL_CORPUS`` — validated v1 corpus path (default: packaged corpus).
        - ``CDF_NL_FEW_SHOT_TOP_K`` — prompt-only lexical examples (default 3).
        - ``CDF_NL_DISABLED`` — prepared registry only; disables corpus + LLM.
        - Admission/runtime guardrails: ``CDF_MAX_ESTIMATED_ROWS``,
          ``CDF_MAX_ESTIMATED_BYTES``, ``CDF_MAX_ESTIMATED_COST_USD``,
          ``CDF_RUNTIME_WALL_TIME_MS``, ``CDF_MAX_INTERMEDIATE_ROWS``,
          ``CDF_MAX_FINAL_ROWS``, ``CDF_SEED_BATCH_ROWS``, and
          ``CDF_MAX_SEED_ROWS``. Runtime entity resolution additionally uses
          ``CDF_MAX_RESOLUTION_CALLS``, ``CDF_RESOLUTION_BATCH_SIZE``, and
          ``CDF_RESOLUTION_DEADLINE_MS``.
        - ``CDF_ENTITY_RESOLVER_FACTORY=package.module:function`` is the explicit
          operator-owned composition seam for an optional CDF ``EntityResolver``.
          The factory receives the environment mapping. CDF does not import or
          claim a released AER integration.
        - Assembled mode is disabled unless ``CDF_ASSEMBLY_ENABLED=true``.
          Its Arango connection resolves logical source ``cdf:assembly``; the
          env backend uses ``CDF_ASSEMBLY_ARANGO_URL`` (falling back to
          ``ARANGO_URL``) plus optional ``_DATABASE``, ``_USER``, and
          ``_PASSWORD`` values. Mandatory budgets use
          ``CDF_ASSEMBLY_MAX_ROWS``, ``CDF_ASSEMBLY_MAX_BYTES``,
          ``CDF_ASSEMBLY_WALL_TIME_MS``, and ``CDF_ASSEMBLY_TTL_SECONDS``.
        """
        env = dict(environ if environ is not None else os.environ)

        manifest_path = env.get("CDF_CATALOG_MANIFEST")
        manifest_r2rml_paths: Mapping[str, Path] = {}
        if manifest_path:
            loaded_catalog = FileCatalogLoader(Path(manifest_path)).load()
            docs = [dict(document) for document in loaded_catalog.csi_documents]
            catalog = loaded_catalog.source_catalog()
            manifest_r2rml_paths = loaded_catalog.r2rml_paths
        else:
            csi_dir = Path(env.get("CDF_CSI_DIR", "deploy/csi"))
            docs = [json.loads(p.read_text()) for p in sorted(csi_dir.glob("*.json"))]
            catalog = SourceCatalog.from_csi_documents(docs)

        resolver = resolver_from_env(env)
        rotating = isinstance(resolver, FileSecretResolver) or (
            isinstance(resolver, EnvSecretResolver) and resolver.has_registry
        )
        registry = ConnectorRegistry() if rotating else None
        executors: dict[str, SourceExecutor] = {}
        source_credentials: dict[str, dict[str, Any]] = {}
        unconfigured_sources: list[str] = []
        secret_value_leases: list[SecretValueLease] = []
        r2rml_dir = Path(env.get("CDF_R2RML_DIR", "deploy/r2rml"))
        poll_interval = _nonnegative_float(
            env.get("CDF_SECRET_POLL_INTERVAL_SECONDS", "0"),
            "CDF_SECRET_POLL_INTERVAL_SECONDS",
        )
        for doc in docs:
            from cdf.query.catalog import source_ref_from_csi

            ref = source_ref_from_csi(doc)
            connector_ref = ConnectorRef(ref.source_id, ref.kind, ref.ref)
            resolved = resolver.resolve(connector_ref)
            if resolved is None:
                unconfigured_sources.append(ref.source_id)
                continue
            r2rml_path = manifest_r2rml_paths.get(
                ref.source_id,
                r2rml_dir / f"{ref.source_id.replace(':', '_')}.ttl",
            )
            builder = executor_builder(doc, r2rml_path=r2rml_path)
            if registry is not None:
                registry.register(
                    connector_ref,
                    resolver,
                    builder,
                    poll_interval=poll_interval,
                )
            else:
                executors[ref.source_id] = builder(resolved)
                secret_value_leases.append(
                    register_secret_values(resolved.redaction_values())
                )
                source_credentials[ref.source_id] = {
                    "configured": True,
                    "backend": resolved.backend,
                    "generation": resolved.generation,
                    "last_reload_status": "succeeded",
                    "last_reload_time": datetime.now(timezone.utc).isoformat(),
                }
        executor_mapping: Mapping[str, SourceExecutor] = (
            registry if registry is not None else executors
        )
        strict_startup = _boolean(env.get("CDF_STRICT_STARTUP", "")) or _boolean(
            env.get("CDF_POLICY_REQUIRED", "")
        )
        if strict_startup and unconfigured_sources:
            if registry is not None:
                registry.close()
            missing = ", ".join(sorted(unconfigured_sources))
            raise ValueError(
                f"strict startup requires connector configuration for catalog sources: {missing}"
            )

        questions: dict[str, str] = {}
        questions_file = env.get("CDF_PREPARED_QUESTIONS")
        if questions_file:
            raw = json.loads(Path(questions_file).read_text())
            questions = {normalize_question(q): s for q, s in raw.items()}

        # CDF_NL_DISABLED=1 pins the NL front-end off regardless of which
        # provider API keys happen to be in the environment — the golden gate
        # uses it to stay deterministic (deploy/demo/gate.py).
        nl_disabled = env.get("CDF_NL_DISABLED", "").strip().casefold() in (
            "1",
            "true",
            "yes",
        )
        router: DeterministicCorpusRouter | None = None
        retriever: FewShotRetriever | None = None
        few_shot_top_k = _nonnegative_int(env.get("CDF_NL_FEW_SHOT_TOP_K", "3"))
        if nl_disabled:
            nl_client = None
        else:
            corpus = load_nl_corpus(env.get("CDF_NL_CORPUS") or None)
            router = DeterministicCorpusRouter(corpus)
            retriever = LexicalFewShotRetriever(corpus)
            from cdf.query.nl import default_client

            nl_client = default_client()

        assembly_backend: AssemblyBackend | None = None
        assembly_enabled = _boolean(env.get("CDF_ASSEMBLY_ENABLED", ""))
        assembly_secret = (
            resolver.resolve(ConnectorRef("cdf:assembly", "assembly", "temporary"))
            if assembly_enabled
            else None
        )
        if assembly_enabled and assembly_secret is not None:
            import arango  # lazy: assembled mode remains an optional production dependency

            fields = assembly_secret.fields
            assembly_client = arango.ArangoClient(hosts=fields["url"])
            assembly_db = assembly_client.db(
                fields.get("database", "cmf"),
                username=fields.get("user", "root"),
                password=fields.get("password", ""),
            )
            assembly_backend = ArangoAssemblyBackend(assembly_db, client=assembly_client)
            secret_value_leases.append(
                register_secret_values(assembly_secret.redaction_values())
            )

        entity_resolver = _entity_resolver_from_env(env)
        return cls(
            catalog=catalog,
            executors=executor_mapping,
            prepared_questions=questions,
            nl_client=nl_client,
            deterministic_router=router,
            few_shot_retriever=retriever,
            few_shot_top_k=few_shot_top_k,
            admission_policy=PlanAdmissionPolicy.from_env(env),
            entity_resolver=entity_resolver,
            assembly_backend=assembly_backend,
            assembly_policy=AssemblyPolicy.from_env(env),
            connector_registry=registry,
            source_credentials=source_credentials,
            unconfigured_sources=tuple(sorted(unconfigured_sources)),
            secret_value_leases=tuple(secret_value_leases),
            policy_pdp=policy_pdp_from_env(catalog, env),
            masking_key_resolver=masking_key_resolver_from_env(env),
        )


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("CDF_NL_FEW_SHOT_TOP_K must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError("CDF_NL_FEW_SHOT_TOP_K must be a non-negative integer")
    return parsed


def _nonnegative_float(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return parsed


def _boolean(value: str) -> bool:
    return value.strip().casefold() in ("1", "true", "yes")


def _entity_resolver_from_env(environ: Mapping[str, str]) -> EntityResolver | None:
    path = environ.get("CDF_ENTITY_RESOLVER_FACTORY", "").strip()
    if not path:
        return None
    module_name, separator, attribute = path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(
            "CDF_ENTITY_RESOLVER_FACTORY must use package.module:function syntax"
        )
    factory = getattr(import_module(module_name), attribute, None)
    if not callable(factory):
        raise ValueError(f"CDF_ENTITY_RESOLVER_FACTORY target {path!r} is not callable")
    resolver = factory(environ)
    if not callable(getattr(resolver, "resolve", None)):
        raise ValueError(
            "CDF_ENTITY_RESOLVER_FACTORY must return a CDF EntityResolver"
        )
    return resolver


def _assembly_refusal_envelope(
    sparql: str,
    estimate: Any,
    refusal: AssemblyRefusal,
    metrics: AssemblyMetrics,
) -> AnswerEnvelope:
    return AnswerEnvelope(
        status="refused",
        bindings=(),
        citations=(),
        retrieval_path=(),
        conceptual_sparql=sparql,
        refusal_reason=refusal.message,
        plan_estimate=estimate,
        assembly_metrics=metrics,
        assembly_refusal=refusal,
    )


def _with_final_assembly(
    envelope: AnswerEnvelope,
    metrics: AssemblyMetrics,
    cleanup_failure: AssemblyRefusal | None,
) -> AnswerEnvelope:
    execution_metrics = envelope.execution_metrics
    if execution_metrics is not None:
        execution_metrics = replace(execution_metrics, assembly_metrics=metrics)
    if cleanup_failure is None:
        return replace(
            envelope,
            execution_metrics=execution_metrics,
            assembly_metrics=metrics,
        )
    reason = cleanup_failure.message
    if envelope.refusal_reason:
        reason = f"{envelope.refusal_reason}; {reason}"
    return replace(
        envelope,
        status="refused",
        bindings=(),
        refusal_reason=reason,
        execution_metrics=execution_metrics,
        assembly_metrics=metrics,
        assembly_refusal=cleanup_failure,
    )


def app_from_env() -> Any:
    """Uvicorn factory entry point::

        uvicorn --factory cdf.service.app:app_from_env --port 8600
    """
    return create_app(FederationService.from_env())


try:  # the service extra is optional; the library core must import without it
    from pydantic import BaseModel, ConfigDict
    from starlette.requests import Request as StarletteRequest

    class FederateRequest(BaseModel):
        """Body of ``POST /federate`` — exactly one of ``sparql``/``question``.

        Module-scoped on purpose: under ``from __future__ import annotations``
        FastAPI resolves the endpoint's string annotations against module
        globals, and a closure-local model silently degrades to a query param.
        """

        model_config = ConfigDict(extra="forbid")

        sparql: str | None = None
        question: str | None = None
        allow_partial: bool = False
        execution_mode: Literal["virtual", "assembled"] = "virtual"

except ModuleNotFoundError:  # pragma: no cover
    FederateRequest = None  # type: ignore[assignment,misc]
    StarletteRequest = Any  # type: ignore[misc,assignment]


PurposePolicy = Callable[[str | None, AuthenticatedPrincipal], str | None]


def create_app(
    service: FederationService,
    *,
    verifier: OIDCVerifier | None = None,
    auth_required: bool = False,
    purpose_policy: PurposePolicy | None = None,
    default_request_timeout_seconds: float = 30.0,
    max_request_timeout_seconds: float = 300.0,
) -> Any:
    """Build the FastAPI app around a wired :class:`FederationService`."""
    from fastapi import FastAPI, HTTPException

    if auth_required and verifier is None:
        raise ValueError("auth_required needs an OIDC verifier")
    if not 0 < default_request_timeout_seconds <= max_request_timeout_seconds:
        raise ValueError("request timeout bounds are invalid")

    app = FastAPI(title="Contextual Data Fabric — federated query engine (M5)")
    app.router.add_event_handler("shutdown", service.close)

    def request_context(request: StarletteRequest) -> RequestContext:
        authorization = request.headers.get("authorization")
        principal = ANONYMOUS_DEV_PRINCIPAL
        if authorization is None:
            if auth_required:
                raise HTTPException(
                    401,
                    "authentication required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        else:
            scheme, separator, token = authorization.partition(" ")
            if (
                separator != " "
                or scheme.casefold() != "bearer"
                or not token
                or any(char.isspace() for char in token)
                or verifier is None
            ):
                raise HTTPException(
                    401,
                    "invalid bearer token",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            try:
                principal = verifier.verify(token)
            except AuthenticationError as exc:
                raise HTTPException(
                    401,
                    str(exc),
                    headers={"WWW-Authenticate": "Bearer"},
                ) from exc

        try:
            request_id = normalize_request_identifier(
                request.headers.get("x-request-id") or uuid4().hex,
                "request_id",
            )
            trace_id = normalize_request_identifier(
                request.headers.get("x-trace-id") or request_id,
                "trace_id",
            )
            requested_purpose = normalize_purpose(request.headers.get("x-cdf-purpose"))
            if purpose_policy is None:
                if requested_purpose is not None:
                    raise HTTPException(403, "request purpose is not permitted")
                purpose = None
            else:
                purpose = normalize_purpose(purpose_policy(requested_purpose, principal))
            now = datetime.now(timezone.utc)
            deadline_header = request.headers.get("x-cdf-deadline")
            if deadline_header is None:
                deadline = now + timedelta(seconds=default_request_timeout_seconds)
            else:
                deadline = datetime.fromisoformat(deadline_header.replace("Z", "+00:00"))
                if deadline.tzinfo is None or deadline.utcoffset() is None:
                    raise ValueError("deadline must include a timezone")
                deadline = deadline.astimezone(timezone.utc)
            if deadline <= now:
                raise ValueError("deadline must be in the future")
            if deadline > now + timedelta(seconds=max_request_timeout_seconds):
                raise ValueError("deadline exceeds the configured maximum")
        except HTTPException:
            raise
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return RequestContext(
            principal=principal,
            request_id=request_id,
            trace_id=trace_id,
            purpose=purpose,
            deadline=deadline,
        )

    @app.get("/health")
    def health(request: StarletteRequest) -> dict[str, Any]:
        if auth_required and request.headers.get("authorization") is None:
            return {"status": "ok"}
        context = request_context(request)
        visible = service.authorized_sources(context)
        if auth_required and not visible:
            return {"status": "ok"}
        visible_ids = {item.source_id for item in visible}
        visible_unconfigured = visible_ids & set(service.unconfigured_sources)
        return {
            "status": "degraded" if visible_unconfigured else "ok",
            "sources": sorted(visible_ids & set(service.executors.keys())),
            "unconfigured_sources": sorted(visible_unconfigured),
            "source_credentials": {
                source_id: value
                for source_id, value in service.credential_health().items()
                if source_id in visible_ids
            },
            "prepared_questions": len(service.prepared_questions),
        }

    @app.post("/federate")
    def federate(req: FederateRequest, request: StarletteRequest) -> dict[str, Any]:
        context = request_context(request)
        if bool(req.sparql) == bool(req.question):
            raise HTTPException(422, "provide exactly one of 'sparql' or 'question'")
        try:
            if req.sparql:
                envelope = service.federate_sparql(
                    req.sparql,
                    allow_partial=req.allow_partial,
                    execution_mode=req.execution_mode,
                    context=context,
                )
            else:
                envelope = service.federate_question(
                    req.question or "",
                    allow_partial=req.allow_partial,
                    execution_mode=req.execution_mode,
                    context=context,
                )
        except UnsupportedQueryError as exc:
            # Planner refusal is a feature (never a silently-wrong partition);
            # surface it as a client error with the reason.
            raise HTTPException(422, f"unsupported query construct: {exc}") from exc
        return asdict(envelope)

    @app.post("/nl-preview")
    def nl_preview(req: FederateRequest, request: StarletteRequest) -> dict[str, Any]:
        """Show the SPARQL an English question translates to (no execution)."""
        context = request_context(request)
        if not req.question:
            raise HTTPException(422, "provide 'question'")
        result = service.resolve_question_for_preview(req.question, context)
        return {
            "question": req.question,
            "sparql": result.sparql,
            "ok": result.sparql is not None,
            "warnings": list(result.warnings),
            "error": result.error,
            "nl_metrics": asdict(result.metrics) if result.metrics is not None else None,
            "request_metadata": asdict(context.safe_metadata()),
        }

    return app
