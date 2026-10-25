import json

import anthropic
import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from aeiou.agent import ClaudeBackend
from aeiou.service import app


@pytest.mark.parametrize(
    ("model", "effort", "speed"),
    [("claude-opus-5-5", "medium", "fast"), ("claude-sonnet-5-5", "high", "standard")],
)
def test_dashboard_selection_reaches_anthropic_request(monkeypatch, tmp_path, model, effort, speed):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_fixture", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": "Ready"}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    sdk = anthropic.Anthropic(api_key="test-only", http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr("aeiou.agent.anthropic.Anthropic", lambda **kwargs: sdk)
    selected = TestClient(app).post("/api/settings/model", json={"model": model})
    assert selected.status_code == 200
    assert selected.json() == {"model": model, "provider": "anthropic", "effort": effort, "speed": speed}
    backend = ClaudeBackend()
    try:
        assert backend.complete("System prompt", "Next move") == "Ready"
    finally:
        backend.client.close()
    assert len(requests) == 1
    assert requests[0]["model"] == model
    assert requests[0]["output_config"]["effort"] == effort
    assert requests[0].get("speed", "standard") == speed
