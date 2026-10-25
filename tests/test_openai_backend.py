import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest
from fastapi.testclient import TestClient

from aeiou import config, eval_suite, service
from aeiou.agent import BuyerDecisionTimeout, ProviderRefusalError, make_backend
from aeiou.openai_backend import IncompleteModelResponseError, OpenAIBackend


@pytest.fixture
def api_mock(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-only-openai")
    monkeypatch.setattr(config, "dotenv_values", lambda path: {})
    state = SimpleNamespace(
        requests=[],
        clients=[],
        streams=[],
        status="completed",
        delay=0,
        http_status=200,
        content=[{"type": "output_text", "text": '{"action":"send"}', "annotations": []}],
    )

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield (
                b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"partial"}\n\n'
            )
            if state.delay:
                await asyncio.sleep(state.delay)
            response = {
                "id": "resp_fixture",
                "object": "response",
                "created_at": 1,
                "status": state.status,
                "model": "gpt-5.5-2026-04-23",
                "error": None,
                "incomplete_details": {"reason": "max_output_tokens"} if state.status == "incomplete" else None,
                "output": [
                    {
                        "type": "message",
                        "id": "msg_fixture",
                        "role": "assistant",
                        "status": "completed",
                        "content": state.content,
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 30,
                    "total_tokens": 130,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens_details": {"reasoning_tokens": 10},
                },
            }
            event = {"type": f"response.{state.status}", "response": response, "sequence_number": 1}
            yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()

        async def aclose(self):
            self.closed = True

    def respond(request):
        state.requests.append(request)
        if state.http_status != 200:
            return httpx.Response(state.http_status, json={"error": {"message": "Test error", "type": "server_error"}})
        stream = Stream()
        state.streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    original = openai.AsyncOpenAI

    def client(**kwargs):
        http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        state.clients.append(http)
        return original(**kwargs, http_client=http)

    monkeypatch.setattr("aeiou.openai_backend.openai.AsyncOpenAI", client)
    return state


def test_openai_factory_streams_exact_prompt_and_records_usage(api_mock):
    buyer = make_backend(model="gpt-5.5", effort="high")
    assert isinstance(buyer, OpenAIBackend)
    assert buyer.complete("Exact buyer strategy", "Exact research and threads") == '{"action":"send"}'
    request = api_mock.requests[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer sk-test-only-openai"
    body = json.loads(request.content)
    assert body["model"] == "gpt-5.5"
    assert body["instructions"] == "Exact buyer strategy"
    assert body["input"] == [{"role": "user", "content": "Exact research and threads"}]
    assert body["reasoning"] == {"effort": "high"}
    assert body["stream"] and body["store"] is False
    assert "speed" not in body and "output_config" not in body
    assert (buyer.input_tokens, buyer.output_tokens) == (100, 30)
    assert (buyer.reasoning_tokens, buyer.cached_input_tokens) == (10, 20)
    assert buyer.resolved_model == "gpt-5.5-2026-04-23"
    assert all(c.is_closed for c in api_mock.clients)
    assert all(s.closed for s in api_mock.streams)


def test_structured_output_uses_responses_format(api_mock):
    schema = {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}
    OpenAIBackend().complete_json("Supplier", "Quote", schema)
    body = json.loads(api_mock.requests[0].content)
    assert body["text"]["format"] == {"type": "json_schema", "name": "negotiation_action", "schema": schema}


def test_partial_output_from_token_limit_is_not_used(api_mock):
    api_mock.status = "incomplete"
    buyer = OpenAIBackend()
    with pytest.raises(IncompleteModelResponseError, match="max_output_tokens"):
        buyer.complete("system", "user")
    assert buyer.errors == 1 and buyer.output_tokens == 30


def test_refusal_has_separate_diagnostic(api_mock):
    api_mock.content = [{"type": "refusal", "refusal": "Cannot comply"}]
    buyer = OpenAIBackend()
    with pytest.raises(ProviderRefusalError):
        buyer.complete("system", "user")
    assert buyer.refusals == buyer.errors == 1
    assert "sk-test" not in json.dumps(buyer.refusal_details)


def test_total_deadline_closes_stream_with_no_action(api_mock):
    api_mock.delay = 10
    buyer = OpenAIBackend(timeout=0.1)
    started = time.monotonic()
    with pytest.raises(BuyerDecisionTimeout):
        buyer.complete("system", "user")
    assert time.monotonic() - started < 2
    assert len(api_mock.requests) == 1
    assert all(s.closed for s in api_mock.streams)
    assert all(c.is_closed for c in api_mock.clients)


def test_shared_deadline_reduces_request_budget(api_mock):
    api_mock.delay = 10
    buyer = OpenAIBackend(timeout=50)
    with pytest.raises(BuyerDecisionTimeout):
        buyer.complete_before("system", "user", time.monotonic() + 0.1)
    assert api_mock.requests[0].extensions["timeout"]["read"] <= 0.1


def test_stop_interrupts_in_flight_stream(api_mock):
    api_mock.delay = 10
    cancel = threading.Event()
    buyer = OpenAIBackend()
    wrapped = eval_suite.CancellableBackend(buyer, cancel)
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="Stopped"):
            wrapped.complete_before("system", "user", time.monotonic() + 50)
    finally:
        timer.join()
    assert time.monotonic() - started < 2
    assert all(c.is_closed for c in api_mock.clients)


def test_provider_failure_does_not_retry_or_fallback(api_mock):
    api_mock.http_status = 429
    with pytest.raises(RuntimeError, match="HTTP 429"):
        OpenAIBackend().complete("system", "user")
    assert len(api_mock.requests) == 1


def test_anthropic_key_is_never_sent_to_openai(api_mock, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ant-test-wrong-provider")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIBackend()
    assert api_mock.requests == []


def test_route_snapshots_independent_models_and_efforts(api_mock, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-only")
    monkeypatch.setenv("ARENA_API_KEY", "arena-test-only")
    monkeypatch.setattr(service, "JOBS", {})
    queued = []
    monkeypatch.setattr(service, "POOL", SimpleNamespace(submit=lambda *args: queued.append(args)))
    client = TestClient(service.app)
    response = client.post(
        "/api/eval-suite/compare",
        json={
            "prompt_a": "Same strategy",
            "prompt_b": "Same strategy",
            "model_a": "claude-sonnet-5-5",
            "effort_a": "high",
            "model_b": "gpt-5.5",
            "effort_b": "medium",
        },
    )
    assert response.status_code == 202, response.text
    config.set_model("claude-opus-5-5")
    reports = [
        json.loads((config.state_root() / "eval-suite" / f"{b['id']}.json").read_text())
        for b in response.json()["batches"]
    ]
    assert [(r["model"], r["provider"], r["effort"]) for r in reports] == [
        ("claude-sonnet-5-5", "anthropic", "high"),
        ("gpt-5.5", "openai", "medium"),
    ]
    assert len(queued) == 2 and not api_mock.requests


def test_missing_key_blocks_both_before_any_session(api_mock, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-only")
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(service, "JOBS", {})
    queued = []
    monkeypatch.setattr(service, "POOL", SimpleNamespace(submit=lambda *args: queued.append(args)))
    response = TestClient(service.app).post(
        "/api/eval-suite/compare",
        json={
            "prompt_a": "A",
            "prompt_b": "B",
            "model_a": "claude-sonnet-5-5",
            "model_b": "gpt-5.5",
        },
    )
    assert response.status_code == 409 and "OPENAI_API_KEY" in response.text
    assert queued == [] and service.JOBS == {}


@pytest.mark.parametrize(
    ("model", "effort"), [("gpt-invalid", "medium"), ("gpt-5.5", "ultra"), ("claude-sonnet-5-5", "none")]
)
def test_bad_model_settings_rejected(api_mock, model, effort):
    response = TestClient(service.app).post(
        "/api/eval-suite",
        json={
            "prompt": "Test",
            "model": model,
            "effort": effort,
        },
    )
    assert response.status_code == 422


def test_added_local_key_is_detected_and_redacted(api_mock, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setattr(config, "dotenv_values", lambda path: {"OPENAI_API_KEY": "sk-local-test-key"})
    assert config.provider_configured("openai")
    assert "sk-local-test-key" not in eval_suite.error_text(RuntimeError("Bad sk-local-test-key"))
    status = TestClient(service.app).get("/api/status")
    assert status.json()["openai_configured"]
    assert "sk-local-test-key" not in status.text
