"""Tests for the HTTP service seam (``POST /federate`` → envelope).

The FastAPI layer is optional (``pip install -e ".[service]"``); these tests
skip when it isn't installed. The engine underneath is exercised with stub
executors — the live legs have their own opt-in tests.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cdf.auth import AuthenticatedPrincipal, AuthenticationError  # noqa: E402
from cdf.eval.nl_corpus import (  # noqa: E402
    DeterministicCorpusRouter,
    validate_corpus_document,
)
from cdf.query import SourceCatalog, SourceResult  # noqa: E402
from cdf.service import FederationService, create_app  # noqa: E402

_ACCOUNT_CSI = {
    "csiVersion": "1",
    "conceptualModel": {"entities": [{"name": "Account", "properties": [{"name": "name"}]}]},
    "physicalMapping": {"entities": {"Account": {"tableName": "accounts"}}},
    "provenance": {"producer": "r2g", "direction": "forward",
                   "source": {"kind": "postgresql", "ref": "crm"}},
}

_TICKET_CSI = {
    "csiVersion": "1",
    "conceptualModel": {"entities": [{"name": "Ticket", "properties": [{"name": "subject"}]}]},
    "arangoPhysicalMapping": {
        "entities": {"Ticket": {"style": "COLLECTION", "collectionName": "tickets"}},
        "relationships": {},
    },
    "provenance": {"producer": "analyzer", "direction": "reverse",
                   "source": {"kind": "arango", "ref": "tickets"}},
}

_SPARQL = (
    "PREFIX c: <urn:arango-sparql:concept#> "
    "SELECT ?name WHERE { ?a a c:Account ; c:name ?name }"
)


class _StubExecutor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, subquery):
        return SourceResult(
            rows=tuple(self._rows),
            native_query="SELECT stub",
            source_objects=("accounts",),
            as_of="2026-07-16T00:00:00Z",
        )


class _StubLLMClient:
    provider = "openai"
    model = "gpt-5-mini"

    def generate(self, messages):
        return type(
            "Response",
            (),
            {
                "content": _SPARQL,
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "cached_tokens": 20,
            },
        )()


@pytest.fixture()
def client() -> TestClient:
    catalog = SourceCatalog.from_csi_documents([_ACCOUNT_CSI, _TICKET_CSI])
    service = FederationService(
        catalog=catalog,
        executors={"postgresql:crm": _StubExecutor([{"a": "urn:1", "name": "Acme"}])},
        prepared_questions={"which accounts exist?": _SPARQL},
    )
    return TestClient(create_app(service))


def test_health_lists_sources(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["sources"] == ["postgresql:crm"]
    assert body["prepared_questions"] == 1


def test_federate_sparql_returns_grounded_envelope(client: TestClient) -> None:
    resp = client.post("/federate", json={"sparql": _SPARQL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "grounded"
    assert body["bindings"] == [{"name": "Acme"}]
    assert body["citations"][0]["source_id"] == "postgresql:crm"
    assert body["retrieval_path"][0]["status"] == "ok"
    assert body["nl_metrics"] is None
    metrics = body["execution_metrics"]
    assert metrics["total_duration_ms"] >= 0
    assert metrics["partition_duration_ms"] is not None
    assert metrics["execution_duration_ms"] >= 0
    assert metrics["reassembly_duration_ms"] >= 0
    assert metrics["row_count"] == 1
    assert metrics["cost_usd"] is None
    assert metrics["legs"][0]["source_id"] == "postgresql:crm"
    assert metrics["legs"][0]["status"] == "ok"
    assert metrics["legs"][0]["row_count"] == 1
    assert metrics["legs"][0]["cost_usd"] is None


def test_http_execution_mode_matches_service_contract(client: TestClient) -> None:
    virtual = client.post(
        "/federate",
        json={"sparql": _SPARQL, "execution_mode": "virtual"},
    ).json()
    assembled = client.post(
        "/federate",
        json={"sparql": _SPARQL, "execution_mode": "assembled"},
    ).json()

    assert virtual["status"] == "grounded"
    assert virtual["assembly_metrics"]["mode"] == "virtual"
    assert assembled["status"] == "refused"
    assert assembled["assembly_refusal"]["code"] == "assembly_backend_unconfigured"
    assert assembled["assembly_metrics"]["mode"] == "assembled"
    assert (
        client.post(
            "/federate",
            json={"sparql": _SPARQL, "execution_mode": "invalid"},
        ).status_code
        == 422
    )


def test_federate_prepared_question_resolves(client: TestClient) -> None:
    resp = client.post("/federate", json={"question": "Which  accounts EXIST?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "grounded"
    assert body["nl_metrics"] == {
        "path": "registry",
        "duration_ms": 0.0,
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "provider": None,
        "model": None,
    }
    assert body["execution_metrics"]["legs"][0]["status"] == "ok"


def test_federate_exact_corpus_alias_reports_zero_cost_deterministic_path() -> None:
    corpus = validate_corpus_document(
        {
            "schema_version": 1,
            "corpus_version": "test",
            "examples": [
                {
                    "id": "accounts",
                    "question": "Which accounts exist?",
                    "aliases": ["List all accounts."],
                    "expected": {
                        "sparql": _SPARQL,
                        "sources": ["postgresql:crm"],
                        "join_keys": [],
                        "refusal": False,
                        "path": "deterministic",
                    },
                }
            ],
        }
    )
    service = FederationService(
        catalog=SourceCatalog.from_csi_documents([_ACCOUNT_CSI, _TICKET_CSI]),
        executors={"postgresql:crm": _StubExecutor([{"a": "urn:1", "name": "Acme"}])},
        deterministic_router=DeterministicCorpusRouter(corpus),
    )
    body = TestClient(create_app(service)).post(
        "/federate", json={"question": "  LIST  ALL ACCOUNTS. "}
    ).json()
    assert body["status"] == "grounded"
    assert body["nl_metrics"] == {
        "path": "deterministic",
        "duration_ms": 0.0,
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "provider": None,
        "model": None,
    }


def test_prepared_registry_precedes_deterministic_corpus() -> None:
    corpus = validate_corpus_document(
        {
            "schema_version": 1,
            "corpus_version": "test",
            "examples": [
                {
                    "id": "same-question",
                    "question": "Which accounts exist?",
                    "aliases": ["List accounts now."],
                    "expected": {
                        "sparql": _SPARQL,
                        "sources": ["postgresql:crm"],
                        "join_keys": [],
                        "refusal": False,
                    },
                }
            ],
        }
    )
    service = FederationService(
        catalog=SourceCatalog.from_csi_documents([_ACCOUNT_CSI]),
        executors={"postgresql:crm": _StubExecutor([{"a": "urn:1", "name": "Acme"}])},
        prepared_questions={"which accounts exist?": _SPARQL},
        deterministic_router=DeterministicCorpusRouter(corpus),
    )
    body = TestClient(create_app(service)).post(
        "/federate", json={"question": "WHICH ACCOUNTS EXIST?"}
    ).json()
    assert body["nl_metrics"]["path"] == "registry"


def test_exact_corpus_refusal_never_calls_llm() -> None:
    corpus = validate_corpus_document(
        {
            "schema_version": 1,
            "corpus_version": "test",
            "examples": [
                {
                    "id": "secrets",
                    "question": "Show secrets.",
                    "aliases": ["List credentials."],
                    "expected": {
                        "sparql": None,
                        "sources": [],
                        "join_keys": [],
                        "refusal": True,
                        "refusal_reason_contains": ["credentials"],
                        "path": "deterministic",
                    },
                }
            ],
        }
    )

    class FailIfCalled:
        def generate(self, messages):
            raise AssertionError("LLM must not run for exact corpus refusal")

    service = FederationService(
        catalog=SourceCatalog.from_csi_documents([_ACCOUNT_CSI]),
        executors={},
        deterministic_router=DeterministicCorpusRouter(corpus),
        nl_client=FailIfCalled(),
    )
    body = TestClient(create_app(service)).post(
        "/federate", json={"question": "LIST CREDENTIALS."}
    ).json()
    assert body["status"] == "refused"
    assert body["nl_metrics"]["path"] == "deterministic"
    assert body["nl_metrics"]["llm_calls"] == 0


def test_federate_question_reports_llm_time_tokens_and_cost(monkeypatch) -> None:
    monkeypatch.setattr(
        "cdf.service.metering.estimate_cost_usd",
        lambda provider, model, prompt_tokens, completion_tokens: 0.00123,
    )
    catalog = SourceCatalog.from_csi_documents([_ACCOUNT_CSI, _TICKET_CSI])
    service = FederationService(
        catalog=catalog,
        executors={"postgresql:crm": _StubExecutor([{"a": "urn:1", "name": "Acme"}])},
        nl_client=_StubLLMClient(),
    )

    body = TestClient(create_app(service)).post(
        "/federate", json={"question": "Which accounts exist?"}
    ).json()

    assert body["status"] == "grounded"
    metrics = body["nl_metrics"]
    assert metrics["path"] == "llm"
    assert metrics["duration_ms"] >= 0
    assert metrics["llm_calls"] == 1
    assert metrics["prompt_tokens"] == 120
    assert metrics["completion_tokens"] == 30
    assert metrics["cached_tokens"] == 20
    assert metrics["cost_usd"] == 0.00123
    assert metrics["provider"] == "openai"
    assert metrics["model"] == "gpt-5-mini"
    assert body["execution_metrics"]["total_duration_ms"] >= 0


def test_unknown_question_is_refused_not_guessed(client: TestClient) -> None:
    body = client.post("/federate", json={"question": "meaning of life?"}).json()
    assert body["status"] == "refused"
    # No prepared match and (in tests) no NL client configured -> refuse, not guess.
    assert "NL front-end" in body["refusal_reason"]


def test_nl_disabled_keeps_prepared_only_gate_behavior(tmp_path) -> None:
    (tmp_path / "account.json").write_text(json.dumps(_ACCOUNT_CSI))
    service = FederationService.from_env(
        {
            "CDF_CSI_DIR": str(tmp_path),
            "CDF_NL_DISABLED": "true",
        }
    )
    assert service.nl_client is None
    assert service.deterministic_router is None
    assert service.few_shot_retriever is None


def test_unconfigured_catalog_source_is_visible_as_degraded_health(tmp_path) -> None:
    (tmp_path / "account.json").write_text(json.dumps(_ACCOUNT_CSI))
    service = FederationService.from_env(
        {
            "CDF_CSI_DIR": str(tmp_path),
            "CDF_NL_DISABLED": "true",
        }
    )

    body = TestClient(create_app(service)).get("/health").json()
    assert body["status"] == "degraded"
    assert body["sources"] == []
    assert body["unconfigured_sources"] == ["postgresql:crm"]
    assert body["source_credentials"]["postgresql:crm"]["configured"] is False


@pytest.mark.parametrize("strict_flag", ["CDF_STRICT_STARTUP", "CDF_POLICY_REQUIRED"])
def test_strict_startup_rejects_unconfigured_catalog_source(
    tmp_path, strict_flag: str
) -> None:
    (tmp_path / "account.json").write_text(json.dumps(_ACCOUNT_CSI))

    with pytest.raises(ValueError, match="postgresql:crm"):
        FederationService.from_env(
            {
                "CDF_CSI_DIR": str(tmp_path),
                "CDF_NL_DISABLED": "true",
                strict_flag: "true",
            }
        )


def test_exactly_one_input_required(client: TestClient) -> None:
    assert client.post("/federate", json={}).status_code == 422
    assert (
        client.post("/federate", json={"sparql": _SPARQL, "question": "hi"}).status_code == 422
    )


def test_unsupported_construct_is_a_declared_422(client: TestClient) -> None:
    # E1 now pushes down single-leg FILTER/OPTIONAL; a genuinely unsupported
    # construct (UNION) is still a declared 422, named in the detail.
    unioned = (
        "PREFIX c: <urn:arango-sparql:concept#> SELECT ?name WHERE "
        "{ { ?a a c:Account ; c:name ?name } UNION { ?a a c:Order ; c:name ?name } }"
    )
    resp = client.post("/federate", json={"sparql": unioned})
    assert resp.status_code == 422
    assert "UNION" in resp.json()["detail"]


def test_http_envelope_redacts_source_url_credentials() -> None:
    class FailingExecutor:
        def execute(self, _subquery):
            raise RuntimeError("failed https://reader:do-not-return@db.internal/query")

    service = FederationService(
        catalog=SourceCatalog.from_csi_documents([_ACCOUNT_CSI]),
        executors={"postgresql:crm": FailingExecutor()},
    )
    body = TestClient(create_app(service)).post("/federate", json={"sparql": _SPARQL}).json()
    assert "do-not-return" not in repr(body)
    assert "[REDACTED]" in repr(body)


class _Verifier:
    def __init__(self):
        self.tokens = []

    def verify(self, token):
        self.tokens.append(token)
        if token == "invalid":
            raise AuthenticationError("bearer token validation failed")
        return AuthenticatedPrincipal(
            issuer="https://idp.example",
            subject="user-1",
            tenant="tenant-a",
        )


def test_http_required_auth_rejects_missing_and_invalid_bearer() -> None:
    verifier = _Verifier()
    service = FederationService(
        catalog=SourceCatalog.from_csi_documents([_ACCOUNT_CSI]),
        executors={"postgresql:crm": _StubExecutor([{"name": "Acme"}])},
    )
    client = TestClient(create_app(service, verifier=verifier, auth_required=True))

    health = client.get("/health")
    missing = client.post("/federate", json={"sparql": _SPARQL})
    invalid = client.post(
        "/federate",
        headers={"Authorization": "Bearer invalid"},
        json={"sparql": _SPARQL},
    )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert "invalid" not in repr(invalid.json())


def test_http_verified_identity_builds_safe_context_and_body_cannot_override() -> None:
    verifier = _Verifier()
    seen = []

    class ContextExecutor:
        def execute_with_context(self, _subquery, context):
            seen.append(context)
            return SourceResult(rows=({"name": "Acme"},))

    service = FederationService(
        catalog=SourceCatalog.from_csi_documents([_ACCOUNT_CSI]),
        executors={"postgresql:crm": ContextExecutor()},
    )
    client = TestClient(
        create_app(
            service,
            verifier=verifier,
            auth_required=True,
            purpose_policy=lambda requested, _principal: requested,
        )
    )
    response = client.post(
        "/federate",
        headers={
            "Authorization": "Bearer opaque-bearer",
            "X-Request-ID": "request-123",
            "X-Trace-ID": "trace-123",
            "X-CDF-Purpose": "  customer   support ",
        },
        json={"sparql": _SPARQL},
    )
    injected = client.post(
        "/federate",
        headers={"Authorization": "Bearer opaque-bearer"},
        json={"sparql": _SPARQL, "principal": {"sub": "admin"}},
    )

    assert response.status_code == 200
    assert verifier.tokens[0] == "opaque-bearer"
    assert seen[0].request.request_id == "request-123"
    assert seen[0].request.purpose == "customer support"
    metadata = response.json()["request_metadata"]
    assert metadata["principal_key"] == "https://idp.example|user-1"
    assert metadata["tenant"] == "tenant-a"
    assert "opaque-bearer" not in repr(response.json())
    assert injected.status_code == 422


def test_http_dev_default_remains_anonymous_and_purpose_is_policy_controlled(
    client: TestClient,
) -> None:
    response = client.post("/federate", json={"sparql": _SPARQL})
    denied_purpose = client.post(
        "/federate",
        headers={"X-CDF-Purpose": "support"},
        json={"sparql": _SPARQL},
    )

    assert response.status_code == 200
    assert response.json()["request_metadata"]["principal_key"] == "cdf:dev|anonymous"
    assert denied_purpose.status_code == 403
