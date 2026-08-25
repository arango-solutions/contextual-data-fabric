"""Semantic MCP surface for the federated query engine (M5 / F2).

The tools in this module stay at the conceptual layer: agents can ask an
English question or inspect the catalog, but cannot submit native AQL/SQL.
Every executed question goes through :class:`FederationService`, exactly like
``POST /federate``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from cdf.auth import (
    AuthenticationError,
    RequestContext,
    anonymous_request_context,
    principal_from_claims,
)
from cdf.service import FederationService

ServiceFactory = Callable[[], FederationService]
ContextFactory = Callable[[], RequestContext | None]


def request_context_from_access_token(access_token: Any) -> RequestContext:
    """Map the MCP SDK's already-verified token without retaining its bearer."""
    claims = dict(access_token.claims or {})
    if access_token.subject is not None:
        claims["sub"] = access_token.subject
    principal = principal_from_claims(
        claims,
        client_id=access_token.client_id,
        scopes=access_token.scopes,
        authentication_method="mcp",
    )
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=30)
    if access_token.expires_at is not None:
        token_deadline = datetime.fromtimestamp(access_token.expires_at, timezone.utc)
        deadline = min(deadline, token_deadline)
    if deadline <= now:
        raise AuthenticationError("MCP access token is expired")
    request_id = uuid4().hex
    return RequestContext(
        principal=principal,
        request_id=request_id,
        trace_id=request_id,
        purpose=None,
        deadline=deadline,
    )


def create_mcp_server(
    service_factory: ServiceFactory = FederationService.from_env,
    *,
    token_verifier: Any | None = None,
    auth: Any | None = None,
    auth_required: bool = False,
    context_factory: ContextFactory | None = None,
) -> Any:
    """Build an MCP server around one injected federation service.

    ``service_factory`` is the dependency-injection seam for tests and
    alternative deployments. It is called once while constructing the server,
    so all tools share the same executor pools and catalog.
    """
    from mcp.server import MCPServer

    # ToolError is the SDK's sanctioned "expected tool failure" channel: its
    # message reaches the caller verbatim. Newer 2.x SDKs mask any OTHER
    # exception as a generic UnexpectedToolError (sensible hardening — no
    # internal leakage), which silently ate our auth refusals when they were
    # raised as bare PermissionError.
    from mcp.server.mcpserver.exceptions import ToolError

    if (token_verifier is None) != (auth is None):
        raise ValueError("token_verifier and auth must be provided together")

    service = service_factory()
    server_options: dict[str, Any] = {}
    if token_verifier is not None:
        server_options.update(token_verifier=token_verifier, auth=auth)
    server = MCPServer(
        "Contextual Data Fabric",
        version="0.1.0",
        instructions=(
            "Ask natural-language questions across the configured data sources. "
            "Answers are grounded, cited, and may refuse when they cannot be supported."
        ),
        **server_options,
    )

    def current_context() -> RequestContext:
        if context_factory is not None:
            context = context_factory()
        else:
            from mcp.server.auth.middleware.auth_context import get_access_token

            access_token = get_access_token()
            context = (
                request_context_from_access_token(access_token)
                if access_token is not None
                else None
            )
        if context is None:
            if auth_required:
                raise ToolError("authorization required")
            return anonymous_request_context()
        if (
            auth_required
            and context.principal.authentication_method == "anonymous-dev"
        ):
            raise ToolError("authenticated subject required")
        return context

    @server.tool()
    def federate(
        question: str,
        allow_partial: bool = False,
        execution_mode: Literal["virtual", "assembled"] = "virtual",
    ) -> dict[str, Any]:
        """Answer an English question with a grounded, cited federation envelope."""
        context = current_context()
        return asdict(
            service.federate_question(
                question,
                allow_partial=allow_partial,
                execution_mode=execution_mode,
                context=context,
            )
        )

    @server.tool()
    def list_sources() -> list[dict[str, Any]]:
        """List logical sources and whether each has an active executor."""
        context = current_context()
        health = service.credential_health()
        result = []
        for source in service.authorized_sources(context):
            item = {
                "source_id": source.source_id,
                "kind": source.kind,
                "ref": source.ref,
                "available": source.source_id in service.executors,
                **service.catalog.safe_metadata_for(source),
            }
            if source.source_id in health:
                item["credential_health"] = health[source.source_id]
            result.append(item)
        return result

    @server.tool()
    def list_concepts() -> list[dict[str, Any]]:
        """List the safe conceptual vocabulary grouped by logical source."""
        context = current_context()
        return service.authorized_vocabulary(context)

    @server.tool()
    def nl_preview(question: str) -> dict[str, Any]:
        """Resolve an English question to conceptual SPARQL without executing it."""
        context = current_context()
        resolution = service.resolve_question_for_preview(question, context)
        return {
            "question": question,
            "sparql": resolution.sparql,
            "ok": resolution.sparql is not None,
            "warnings": list(resolution.warnings),
            "error": resolution.error,
            "nl_metrics": asdict(resolution.metrics) if resolution.metrics is not None else None,
        }

    return server


def main() -> None:
    """Run the semantic MCP server over stdio."""
    create_mcp_server().run()


if __name__ == "__main__":
    main()
