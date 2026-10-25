import copy
import json
import threading
from types import SimpleNamespace

import anthropic
import httpx2 as httpx
import pytest

from aeiou.agent import BUYER_DECISION_BUDGET_S, BuyerDecisionTimeout, ClaudeBackend
from aeiou.eval_suite import CancellableBackend, EvaluationCancelled
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package
from aeiou.simulation import local_cases


@pytest.fixture
def timed_buyer(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    timer = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr("aeiou.agent.time", timer)
    monkeypatch.setattr("aeiou.prompt_protocol.time", timer)
    replies, requests = [], []

    def respond(request):
        requests.append(request)
        seconds, text = replies.pop(0)
        clock.now += seconds
        if isinstance(text, Exception):
            raise text
        return httpx.Response(200, json={
            "id": "msg_fixture", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    sdk = anthropic.Anthropic(
        api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr("aeiou.agent.anthropic.Anthropic", lambda **kwargs: sdk)
    backend = ClaudeBackend(model="claude-sonnet-5-5", timeout=BUYER_DECISION_BUDGET_S)
    cancel = threading.Event()
    agent = PromptLabAgent(package(), CancellableBackend(backend, cancel), PromptSpec("eval", "Negotiate."))
    case = copy.deepcopy(local_cases()["case:S01"])
    sid = case["suppliers"][0]
    threads = {sid: [{
        "from": sid, "status": "open", "body": "Opening quotation.",
        "term_sheets": [{"term_sheet_id": "offer:1", "terms": {"unit_price": 16, "payment_days": 60}}],
    }]}
    valid = json.dumps({"supplier_id": sid, "action": "send", "body": "Please improve your quote.", "term_sheets": []})
    yield SimpleNamespace(**locals())
    sdk.close()


def test_high_effort_response_after_old_cutoff_is_delivered(timed_buyer):
    t = timed_buyer
    t.replies.append((42, t.valid))
    move = t.agent.decide(t.case, t.threads)
    assert move["message"]["body"] == "Please improve your quote."
    assert t.requests[0].extensions["timeout"]["read"] == 50
    assert json.loads(t.requests[0].content)["output_config"]["effort"] == "high"
    assert t.agent.decisions[0]["elapsed_s"] == 42


@pytest.mark.parametrize("repair_seconds", [5, 11])
def test_invalid_output_repair_shares_original_deadline(timed_buyer, repair_seconds):
    t = timed_buyer
    t.replies.extend([(40, "invalid JSON"), (repair_seconds, t.valid)])
    if repair_seconds == 5:
        assert t.agent.decide(t.case, t.threads)["message"]["body"] == "Please improve your quote."
    else:
        with pytest.raises(BuyerDecisionTimeout, match="action was not sent"):
            t.agent.decide(t.case, t.threads)
        assert t.agent.decisions == []
    assert [r.extensions["timeout"]["read"] for r in t.requests] == [50, 10]


def test_expired_budget_does_not_start_another_request(timed_buyer):
    t = timed_buyer
    with pytest.raises(BuyerDecisionTimeout, match="no further model request"):
        t.backend.complete_before("system", "user", t.clock.now)
    assert t.requests == []
    assert t.backend.calls == 0


def test_network_timeout_is_reported_without_retry_or_substitute_action(timed_buyer):
    t = timed_buyer
    t.replies.append((50, httpx.ReadTimeout("Timed out")))
    with pytest.raises(BuyerDecisionTimeout, match="50.0s available"):
        t.agent.decide(t.case, t.threads)
    assert len(t.requests) == 1
    assert t.backend.errors == 1
    assert t.agent.decisions == []


def test_cancelled_batch_never_calls_deadline_backend(timed_buyer):
    t = timed_buyer
    t.cancel.set()
    with pytest.raises(EvaluationCancelled):
        t.agent.decide(t.case, t.threads)
    assert t.requests == []
