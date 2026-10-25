"""Exercise real runner recovery against the hosted API's rejection contract."""

import copy
import json
from pathlib import Path

import httpx
import pytest
from arena_kit import ArenaClient
from arena_kit.client import ArenaError
from arena_kit.play import play

from aeiou.action_policy import NegotiationBlocked
from aeiou.negotiation_state import offer_state
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package
from aeiou.runner import RecordingTable
from aeiou.store import get_run, new_run

CASE = json.loads((Path(__file__).parent / "fixtures/sandbox-cases.json").read_text())[0]
SID = CASE["suppliers"][0]
TERMS = {"unit_price": 49.6, "payment_days": 30, "contract_months": 24, "effective_date": "2026-12-01"}


def quote(number=0):
    return {
        "from": "supplier", "status": "final_offer", "message_id": f"msg:{number}",
        "body": "A main offer and a quoted alternative.", "attachments": [],
        "term_sheets": [
            {"term_sheet_id": f"offer:{number}", "terms": {**TERMS, "unit_price": 49.8}},
            {"term_sheet_id": f"offer:{number}-a", "terms": TERMS.copy()},
        ],
    }


def transport(accepts, *, ended=False, reject_all=False, status=422):
    events, calls = [quote()], []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(200, json={"session_id": "session-test", "case": CASE, "threads": {SID: events}})
        if request.method == "GET" and request.url.path == "/v1/sessions/session-test":
            return httpx.Response(200, json={
                "status": "ended" if ended else "open", "result": {"score": 0.0, "deal": False},
            })
        if request.method == "GET":
            return httpx.Response(200, json={
                # Simulate a race: the initially registered alternative is withdrawn before POST.
                "messages": events, "offer_id": "offer:0-a" if not accepts else
                events[-1]["term_sheets"][0]["term_sheet_id"],
            })
        message = json.loads(request.content)["message"]
        if action := message.get("action"):
            accepts.append(action["term_sheet_id"])
            if reject_all or action["term_sheet_id"] != events[-1]["term_sheets"][0]["term_sheet_id"]:
                return httpx.Response(status, json={"detail": "not a standing offer in this session"})
            return httpx.Response(200, json={"result": {"score": 0.96, "deal": True}})
        events.append({**message, "from": "buyer"})
        confirmed = quote(1)
        confirmed["term_sheets"] = [{"term_sheet_id": "offer:confirmed", "terms": TERMS.copy()}]
        events.append(confirmed)
        return httpx.Response(200, json=confirmed)

    return httpx.MockTransport(handler), calls


def test_rejected_alternative_refreshes_then_confirms_exact_terms_before_accepting(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    accepts, observed = [], []
    http, calls = transport(accepts)

    class Buyer:
        model = "test"

        def complete(self, system, user):
            state = json.loads(user)["protocol_state"]["suppliers"][SID]
            observed.append(state)
            if len(observed) == 1:
                return json.dumps({"supplier_id": SID, "action": "accept", "term_sheet_id": "offer:0-a"})
            if len(observed) == 2:
                assert state["rounds_left"] == CASE["round_limit"]
                assert state["acceptance_rejection"]["rejected_offer_id"] == "offer:0-a"
                assert state["eligible_offer_ids"] == ["offer:0"]
                return json.dumps({
                    "supplier_id": SID, "action": "send", "body": "Please confirm this exact package as standing.",
                    "term_sheets": [TERMS],
                })
            assert state["rounds_left"] == CASE["round_limit"] - 1
            return json.dumps({"supplier_id": SID, "action": "accept", "term_sheet_id": "offer:confirmed"})

    remote = ArenaClient(base_url="https://arena.test", transport=http, rate=100000, retries=0)
    run = new_run(CASE["case_id"], "hosted", "prompt-eval")
    try:
        agent = PromptLabAgent(package(), Buyer(), PromptSpec("eval", "Accept a satisfactory offer."))
        result = play(RecordingTable(remote, run), CASE["case_id"], agent)
    finally:
        remote.http.close()
    assert result == {"score": 0.96, "deal": True}
    assert accepts == ["offer:0-a", "offer:confirmed"]  # Never substitute the more expensive main offer.
    assert calls.count(("POST", "/v1/sessions")) == 1
    assert len(observed) == 3
    saved = get_run(run["id"])
    assert len(saved["rejected_actions"]) == len(saved["acceptance_recoveries"]) == 1
    buyer_messages = [m for m in saved["threads"][SID] if m.get("from") == "buyer"]
    assert len(buyer_messages) == 2  # The rejected acceptance stays in the audit, not the round history.
    assert buyer_messages[0]["term_sheets"] == [{"terms": TERMS}]


@pytest.mark.parametrize("ended", [False, True])
def test_recovery_is_bounded_and_ended_session_uses_official_result(monkeypatch, tmp_path, ended):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    accepts = []
    http, calls = transport(accepts, ended=ended, reject_all=True)

    class Stubborn:
        def decide(self, case, threads):
            return {"supplier_id": SID, "message": {"action": {"type": "accept", "term_sheet_id": "offer:0-a"}}}

    remote = ArenaClient(base_url="https://arena.test", transport=http, rate=100000)
    table = RecordingTable(remote, new_run(CASE["case_id"], "hosted", "test"))
    try:
        if ended:
            assert play(table, CASE["case_id"], Stubborn()) == {"score": 0.0, "deal": False}
            assert len(accepts) == 1
        else:
            with pytest.raises(ArenaError):
                play(table, CASE["case_id"], Stubborn())
            assert len(accepts) == 3
            assert table.acceptance_recoveries == 2
        assert calls.count(("POST", "/v1/sessions")) == 1
    finally:
        remote.http.close()


def test_other_422_errors_and_cancel_requests_are_not_recovered(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    table = RecordingTable(None, new_run(CASE["case_id"], "hosted", "test"))
    accept = {"action": {"type": "accept", "term_sheet_id": "bad"}}
    assert table.recover_acceptance(SID, accept, ArenaError(422, "invalid terms")) is None
    assert table.recover_acceptance(SID, {"body": "Hello"}, ArenaError(422, "not a standing offer")) is None

    class Cancelled:
        def session(self, *args):
            raise NegotiationBlocked("Stopped")

    table.client = Cancelled()
    with pytest.raises(NegotiationBlocked):
        table.recover_acceptance(SID, accept, ArenaError(422, "not a standing offer"))


def test_server_rejection_cannot_be_repaired_by_reusing_same_alternative():
    events = [quote()]
    events[0]["standing_offer_id"] = "offer:0"
    events[0]["acceptance_rejection"] = {"rejected_offer_id": "offer:0-a"}
    offers = offer_state(CASE, events)
    assert offers[0]["acceptance_eligible"]
    assert not offers[1]["acceptance_eligible"]
    agent = PromptLabAgent(package(), None, PromptSpec("eval", "Accept."))
    with pytest.raises(ContractError, match="standing offer"):
        agent.contract(CASE, {SID: events}, {"supplier_id": SID, "action": "accept", "term_sheet_id": "offer:0-a"})
    assert events[0]["term_sheets"][1]["terms"] == copy.deepcopy(TERMS)


def test_each_reissued_alternative_is_checked_again_before_model_acceptance(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    events, accepted = [quote(0)], []
    events[0]["term_sheets"] = events[0]["term_sheets"][:1]

    def handler(request):
        if request.url.path == "/v1/sessions":
            return httpx.Response(200, json={"session_id": "test", "case": CASE, "threads": {SID: events}})
        if request.method == "GET":
            return httpx.Response(200, json={
                "messages": events, "offer_id": events[-1]["term_sheets"][0]["term_sheet_id"],
            })
        message = json.loads(request.content)["message"]
        if message.get("action"):
            accepted.append(message["action"]["term_sheet_id"])
            return httpx.Response(200, json={"result": {"deal": True, "score": 0.9}})
        events.append(quote(len(events)))
        return httpx.Response(200, json=events[-1])

    class Buyer:
        model = "test"
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            state = json.loads(user)["protocol_state"]["suppliers"][SID]
            if self.calls > 1:
                assert len(state["offers"]) == 2
                assert state["offers"][0]["server_acceptance_available"]
                assert not state["offers"][1]["server_acceptance_available"]
            if self.calls < 4:
                return json.dumps({"supplier_id": SID, "action": "send", "body": "Please confirm the package."})
            return json.dumps({"supplier_id": SID, "action": "accept",
                               "term_sheet_id": state["eligible_offer_ids"][0]})

    remote = ArenaClient(base_url="https://arena.test", transport=httpx.MockTransport(handler), rate=100000)
    record = new_run(CASE["case_id"], "hosted", "test")
    try:
        result = play(RecordingTable(remote, record), CASE["case_id"],
                      PromptLabAgent(package(), Buyer(), PromptSpec("eval", "Choose a valid offer.")))
    finally:
        remote.http.close()
    assert result["deal"]
    assert accepted == ["offer:3"]
    assert not record.get("rejected_actions")
