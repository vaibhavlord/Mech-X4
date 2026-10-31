"""Offline Ollama HTTP tests: no model downloads or inference in CI."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import ai_service, main


def evidence_payload():
    """The documented Swagger example."""
    return {"evidence": {
        "pr_ids": [101, 102, 103],
        "project_count": 3,
        "similarity_score": 0.96,
        "account_ages": [2, 3, 1],
        "time_window_minutes": 30,
    }}


def assessment(**changes):
    """Controlled model output, used only in mocked tests."""
    result = {
        "summary": "Similar submissions across projects warrant review; their quality remains unverified.",
        "risk_level": "needs_review",
        "evidence_referenced": ["project_count: 3", "similarity_score: 0.96"],
    }
    result.update(changes)
    return result


def envelope(output=None, **changes):
    """An Ollama non-streaming completion envelope."""
    result = {
        "model": "gemma3:4b",
        "done": True,
        "done_reason": "stop",
        "response": json.dumps(assessment() if output is None else output),
    }
    result.update(changes)
    return result


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Each test has its own database and predictable AI configuration."""
    monkeypatch.setenv("TRUSTMESH_DB_PATH", str(tmp_path / "data" / "ai-test.db"))
    monkeypatch.delenv("TRUSTMESH_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("TRUSTMESH_OLLAMA_TIMEOUT_SECONDS", raising=False)


@pytest.fixture(autouse=True)
def ollama(monkeypatch):
    """Intercept every async HTTP request at the transport layer, never localhost."""
    state = {
        "requests": [],
        "client_options": [],
        "response": httpx.Response(200, json=envelope()),
        "error": None,
    }
    real_client = httpx.AsyncClient

    def handle_request(request):
        state["requests"].append(request)
        if state["error"] is not None:
            raise state["error"]
        return state["response"]

    def create_client(**options):
        state["client_options"].append(options)
        return real_client(transport=httpx.MockTransport(handle_request), **options)

    monkeypatch.setattr(ai_service.httpx, "AsyncClient", create_client)
    return state


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.mark.parametrize("risk", ["suspicious", "needs_review", "inconclusive"])
def test_valid_assessment_uses_real_http_contract(client, ollama, risk):
    generated = assessment(risk_level=risk)
    ollama["response"] = httpx.Response(200, json=envelope(generated))
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 200
    assert response.json() == {
        **generated,
        "summary": generated["summary"] + " " + ai_service.MAINTAINER_NOTE,
        "model": "gemma3:4b",
        "used_model": True,
    }
    assert len(ollama["requests"]) == 1
    request = ollama["requests"][0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:11434/api/generate"
    body = json.loads(request.content)
    assert body["model"] == "gemma3:4b"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    schema = body["format"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"summary", "risk_level", "evidence_referenced"}
    assert schema["properties"]["risk_level"]["enum"] == ["suspicious", "needs_review", "inconclusive"]
    prompt = json.loads(body["prompt"])
    assert prompt["evidence"] == evidence_payload()["evidence"]
    assert prompt["evidence_units"] == {"account_ages": "days", "time_window_minutes": "minutes"}
    assert prompt["response_schema"] == schema
    assert schema["properties"]["evidence_referenced"]["items"]["enum"] == [
        "pr_ids: [101, 102, 103]",
        "project_count: 3",
        "similarity_score: 0.96",
        "account_ages: [2, 3, 1]",
        "time_window_minutes: 30.0",
    ]
    assert "Never invent account identities" in body["system"]
    assert "measured in DAYS, never minutes or hours" in body["system"]
    assert "final decision remains with maintainers" in body["system"]
    assert "not a confidence percentage" in body["system"]
    assert ollama["client_options"][0]["trust_env"] is False
    assert ollama["client_options"][0]["follow_redirects"] is False
    assert request.extensions["timeout"]["read"] == 120


def test_model_and_timeout_are_configurable(client, ollama, monkeypatch):
    monkeypatch.setenv("TRUSTMESH_OLLAMA_MODEL", "gemma3:1b")
    monkeypatch.setenv("TRUSTMESH_OLLAMA_TIMEOUT_SECONDS", "45.5")
    ollama["response"] = httpx.Response(200, json=envelope(model="gemma3:1b"))
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 200
    assert response.json()["model"] == "gemma3:1b"
    request = ollama["requests"][0]
    assert json.loads(request.content)["model"] == "gemma3:1b"
    assert request.extensions["timeout"]["read"] == 45.5
    assert request.extensions["timeout"]["connect"] == 5


def test_tagless_model_can_return_latest_tag(client, ollama, monkeypatch):
    monkeypatch.setenv("TRUSTMESH_OLLAMA_MODEL", "gemma3")
    ollama["response"] = httpx.Response(200, json=envelope(model="gemma3:latest"))
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 200
    assert response.json()["model"] == "gemma3:latest"


def test_different_evidence_is_sent_and_unsupported_old_values_rejected(client, ollama):
    payload = evidence_payload()
    payload["evidence"]["similarity_score"] = 0.2
    response = client.post("/api/explain", json=payload)
    assert response.status_code == 502
    sent = json.loads(ollama["requests"][0].content)
    assert json.loads(sent["prompt"])["evidence"]["similarity_score"] == 0.2
    assert "similarity_score: 0.2" in sent["format"]["properties"]["evidence_referenced"]["items"]["enum"]


@pytest.mark.parametrize("error", [
    httpx.ConnectError("Connection refused"),
    httpx.ReadTimeout("Inference timed out"),
    httpx.ConnectTimeout("Connection timed out"),
    httpx.RemoteProtocolError("Connection closed early"),
])
def test_ollama_unavailable_returns_503_without_fallback(client, ollama, error):
    ollama["error"] = error
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 503
    assert set(response.json()) == {"detail"}
    assert "Ollama" in response.json()["detail"]


@pytest.mark.parametrize("status", [404, 429, 500, 503, 302])
def test_upstream_http_errors_are_explicit(client, ollama, status):
    ollama["response"] = httpx.Response(
        status, json={"error": "upstream failure"}, headers={"Location": "https://example.com"}
    )
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 503
    assert set(response.json()) == {"detail"}
    assert len(ollama["requests"]) == 1  # A redirect must never reach hosted inference.
    if status == 404:
        assert "ollama pull gemma3:4b" in response.json()["detail"]


def test_ollama_error_inside_success_status_is_not_an_assessment(client, ollama):
    ollama["response"] = httpx.Response(200, json={"error": "model failed to load"})
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 503
    assert set(response.json()) == {"detail"}


@pytest.mark.parametrize("result", [
    [],
    None,
    {},
    envelope(done=False),
    envelope(done="true"),
    envelope(done_reason="length"),
    envelope(model="other-model:4b"),
    envelope(model=None),
    envelope(response=assessment()),
    envelope(response=""),
    envelope(response="not JSON"),
    envelope(response="```json\n" + json.dumps(assessment()) + "\n```"),
    envelope(response=json.dumps(assessment()) + " trailing text"),
    envelope(response='{"summary":"first","summary":"second","risk_level":"needs_review","evidence_referenced":[]}'),
    envelope(response='{"summary":NaN,"risk_level":"needs_review","evidence_referenced":[]}'),
    envelope(response='{"summary":Infinity,"risk_level":"needs_review","evidence_referenced":[]}'),
    envelope(assessment(risk_level="confirmed_spam")),
    envelope(assessment(summary=" ")),
    envelope(assessment(summary=123)),
    envelope(assessment(summary="x" * 2001)),
    envelope(assessment(evidence_referenced=[])),
    envelope(assessment(evidence_referenced=["project_count: 99"])),
    envelope(assessment(evidence_referenced=["author: invented-person"])),
    envelope(assessment(evidence_referenced=["project_count: 3", "project_count: 3"])),
    envelope(assessment(evidence_referenced=[123])),
    envelope(assessment(used_model=True)),
    envelope(assessment(model="gemma3:4b")),
    envelope({"summary": "Missing fields"}),
])
def test_invalid_outputs_are_rejected_without_fake_success(client, ollama, result):
    ollama["response"] = httpx.Response(200, content=json.dumps(result))
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 502
    assert set(response.json()) == {"detail"}
    assert "No assessment was accepted" in response.json()["detail"]


@pytest.mark.parametrize("body", ["<html>Server error</html>", '{"done":true,"done":false}', '{"response":NaN}'])
def test_invalid_outer_json_is_rejected(client, ollama, body):
    ollama["response"] = httpx.Response(200, text=body)
    assert client.post("/api/explain", json=evidence_payload()).status_code == 502


def test_maximum_length_summary_still_fits_maintainer_note(client, ollama):
    ollama["response"] = httpx.Response(200, json=envelope(assessment(summary="x" * 2000)))
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 200
    assert response.json()["summary"].endswith(ai_service.MAINTAINER_NOTE)


def test_existing_maintainer_note_is_not_duplicated(client, ollama):
    summary = assessment()["summary"] + " " + ai_service.MAINTAINER_NOTE
    ollama["response"] = httpx.Response(200, json=envelope(assessment(summary=summary)))
    response = client.post("/api/explain", json=evidence_payload())
    assert response.json()["summary"] == summary


@pytest.mark.parametrize("setting,value", [
    ("TRUSTMESH_OLLAMA_TIMEOUT_SECONDS", "invalid"),
    ("TRUSTMESH_OLLAMA_TIMEOUT_SECONDS", "0"),
    ("TRUSTMESH_OLLAMA_TIMEOUT_SECONDS", "-1"),
    ("TRUSTMESH_OLLAMA_TIMEOUT_SECONDS", "NaN"),
    ("TRUSTMESH_OLLAMA_TIMEOUT_SECONDS", "Infinity"),
    ("TRUSTMESH_OLLAMA_MODEL", " "),
    ("TRUSTMESH_OLLAMA_MODEL", "https://hosted.example/model"),
    ("TRUSTMESH_OLLAMA_MODEL", "example:cloud"),
    ("TRUSTMESH_OLLAMA_MODEL", "example:4b-cloud"),
])
def test_invalid_settings_do_not_call_ollama_or_break_health(client, ollama, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)
    response = client.post("/api/explain", json=evidence_payload())
    assert response.status_code == 500
    assert setting in response.json()["detail"]
    assert ollama["requests"] == []
    assert client.get("/api/health").json() == {"status": "ok"}


@pytest.mark.parametrize("changes", [
    {"pr_ids": []},
    {"pr_ids": [101, 101, 103]},
    {"pr_ids": [0, 102, 103]},
    {"pr_ids": [True, 102, 103]},
    {"project_count": 0},
    {"project_count": 4},
    {"project_count": "3"},
    {"similarity_score": -0.1},
    {"similarity_score": 1.1},
    {"similarity_score": True},
    {"account_ages": [2, -1, 3]},
    {"account_ages": [2, 1]},
    {"account_ages": [2, True, 3]},
    {"time_window_minutes": -1},
    {"author": "unverified-identity"},
])
def test_invalid_evidence_never_reaches_ollama(client, ollama, changes):
    payload = evidence_payload()
    payload["evidence"].update(changes)
    assert client.post("/api/explain", json=payload).status_code == 422
    assert ollama["requests"] == []


@pytest.mark.parametrize("field", list(evidence_payload()["evidence"]))
def test_missing_evidence_fields_rejected(client, ollama, field):
    payload = evidence_payload()
    del payload["evidence"][field]
    assert client.post("/api/explain", json=payload).status_code == 422
    assert ollama["requests"] == []


@pytest.mark.parametrize("payload", [{}, {"evidence": None}, {**evidence_payload(), "model": "other"}])
def test_invalid_request_shape_rejected(client, ollama, payload):
    assert client.post("/api/explain", json=payload).status_code == 422
    assert ollama["requests"] == []


def test_swagger_exposes_explain_contract(client):
    schema = client.get("/openapi.json").json()
    endpoint = schema["paths"]["/api/explain"]["post"]
    assert {"200", "422", "502", "503"} <= set(endpoint["responses"])
    assert set(schema["components"]["schemas"]["ExplainResponse"]["properties"]) == {
        "summary", "risk_level", "evidence_referenced", "model", "used_model",
    }
