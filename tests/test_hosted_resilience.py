import copy

import pytest
from arena_kit.client import ArenaError
from arena_kit.play import play

from aeiou.action_policy import NegotiationBlocked
from aeiou.optimizer import safe_close
from aeiou.runner import RecordingTable
from aeiou.simulation import local_cases
from aeiou.store import new_run


class ChangingArena:
    def __init__(self, *, race=0, expensive=False, error=None):
        self.case = copy.deepcopy(local_cases()["case:S01"])
        self.case["buyer"]["alternative"] = {"kind": "quote", "terms": {"unit_price": 20, "payment_days": 30}}
        self.sid = self.case["suppliers"][0]
        self.version, self.opens, self.accepts, self.reads = 0, 0, [], 0
        self.race, self.expensive, self.error = race, expensive, error
        self.ended = False

    def quote(self):
        return {
            "from": "seller",
            "status": "agreed",
            "body": "Current binding offer.",
            "term_sheets": [
                {
                    "term_sheet_id": f"offer:{self.version}",
                    "terms": {"unit_price": 30 if self.expensive and self.version else 15, "payment_days": 30},
                }
            ],
        }

    def open_session(self, case_id):
        self.opens += 1
        return {"session_id": "single-session", "case": self.case, "threads": {self.sid: [self.quote()]}}

    def session(self, session_id):
        assert session_id == "single-session"
        self.reads += 1
        self.version = max(1, self.version)
        return {
            "status": "open",
            "threads": {self.sid: {"offer_id": f"offer:{self.version}", "status": "agreed", "seconds_left": 50}},
        }

    def thread(self, session_id, supplier_id):
        return {"messages": [self.quote()]}

    def send_message(self, session_id, supplier_id, message):
        action = message["action"]
        if action["type"] == "accept":
            self.accepts.append(action["term_sheet_id"])
            if self.error:
                raise ArenaError(422, self.error)
            if self.race:
                self.race -= 1
                self.version += 1
                raise ArenaError(422, "not a standing offer in this session")
            assert action["term_sheet_id"] == f"offer:{self.version}"
        self.ended = True
        return {"result": {"ended": True, "deal": action["type"] == "accept", "score": 0.5}}


class ClosingAgent:
    def decide(self, case, threads):
        return self.close(case, threads)

    def close(self, case, threads):
        return safe_close(case, threads, {})


def run_fake(client, tmp_path, monkeypatch, *, blocked=False):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    run = new_run(client.case["case_id"], "hosted", "test")
    table = RecordingTable(client, run)
    if blocked:
        with pytest.raises(NegotiationBlocked, match="NO_ELIGIBLE_OFFER"):
            play(table, client.case["case_id"], ClosingAgent())
        assert not client.ended  # No buyer walk-away is sent in this workbench.
        return {"deal": False}, run
    return play(table, client.case["case_id"], ClosingAgent()), run


@pytest.mark.parametrize("race", [0, 1, 2])
def test_refreshes_and_recovers_offer_race_without_extra_session(race, tmp_path, monkeypatch):
    client = ChangingArena(race=race)
    result, run = run_fake(client, tmp_path, monkeypatch)
    assert result["deal"] and client.opens == 1
    assert client.accepts == [f"offer:{i}" for i in range(1, race + 2)]
    assert sum(e["type"] == "send_failed" for e in run["transport_events"]) == race
    # Failed requests do not become fictitious buyer concessions in the transcript.
    assert sum(m.get("from") == "buyer" for m in run["threads"][client.sid]) == 1


def test_reprice_is_economically_rechecked_not_accepted_by_new_id(tmp_path, monkeypatch):
    client = ChangingArena(expensive=True)
    result, _ = run_fake(client, tmp_path, monkeypatch, blocked=True)
    assert not result["deal"] and client.accepts == [] and client.opens == 1


@pytest.mark.parametrize("client", [ChangingArena(race=3), ChangingArena(error="invalid terms")])
def test_recovery_is_bounded_and_does_not_hide_other_errors(client, tmp_path, monkeypatch):
    with pytest.raises(ArenaError):
        run_fake(client, tmp_path, monkeypatch)
    assert client.opens == 1 and len(client.accepts) <= 3


class AdvertisedAlternativeArena(ChangingArena):
    def quote(self):
        message = super().quote()
        message["term_sheets"].append(
            {
                "term_sheet_id": f"offer:{self.version}-alt",
                "terms": {"unit_price": 14, "payment_days": 30},
            }
        )
        return message

    def send_message(self, session_id, supplier_id, message):
        action = message["action"]
        if action.get("term_sheet_id", "").endswith("-alt"):
            self.accepts.append(action["term_sheet_id"])
            raise ArenaError(422, "not a standing offer in this session")
        return super().send_message(session_id, supplier_id, message)


@pytest.mark.parametrize("expensive", [False, True])
def test_rejected_advertised_alternative_is_not_retried_and_primary_is_revalued(expensive, tmp_path, monkeypatch):
    client = AdvertisedAlternativeArena(expensive=expensive)
    result, run = run_fake(client, tmp_path, monkeypatch, blocked=expensive)
    assert client.opens == 1
    assert result["deal"] is (not expensive)
    assert client.accepts == (["offer:1-alt"] if expensive else ["offer:1-alt", "offer:1"])
    exclusions = [e for e in run["transport_events"] if e["type"] == "rejected_alternative_excluded"]
    assert len(exclusions) == 1
    assert exclusions[0]["primary_offer_id"] == "offer:1"
    assert exclusions[0]["rejected_term_sheets"][0]["term_sheet_id"] == "offer:1-alt"
    assert sum(m.get("from") == "buyer" for m in run["threads"][client.sid]) == (0 if expensive else 1)


def test_deadline_protects_existing_offer_before_slow_rfq_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    client = ChangingArena()
    table = RecordingTable(client, new_run(client.case["case_id"], "hosted", "test"))
    case, threads = table.open(client.case["case_id"])
    threads[client.sid].insert(0, {"from": "buyer", "body": "A prior counter."})
    threads["other"] = [
        dict(
            client.quote(),
            term_sheets=[{"term_sheet_id": "offer:other", "terms": {"unit_price": 18, "payment_days": 30}}],
        )
    ]
    monkeypatch.setattr("aeiou.runner.time.monotonic", lambda: 100)
    table.slowest_reply = 35
    table.deadlines = {client.sid: 140, "other": 155}
    choice = table.deadline_close(ClosingAgent(), threads)
    assert choice["supplier_id"] == client.sid and choice["message"]["action"]["type"] == "accept"
    table.deadlines = {client.sid: 160, "other": 160}
    assert table.deadline_close(ClosingAgent(), threads) is None


def test_closed_supplier_is_removed_even_when_server_keeps_last_status(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    client = ChangingArena()
    table = RecordingTable(client, new_run(client.case["case_id"], "hosted", "test"))
    _, threads = table.open(client.case["case_id"])
    client.session = lambda _: {
        "status": "open",
        "threads": {client.sid: {"offer_id": "offer:0", "status": "agreed", "closed": "timeout"}},
    }
    table.refresh(threads)
    assert threads[client.sid][-1]["status"] == "walked_away"
    assert threads[client.sid][-1]["term_sheets"] == []


def test_already_ended_session_returns_result_without_another_action(tmp_path, monkeypatch):
    client = ChangingArena()
    client.session = lambda _: {"status": "ended", "result": {"deal": True, "score": 0.6}}
    result, run = run_fake(client, tmp_path, monkeypatch)
    assert result["score"] == 0.6 and run["status"] == "completed"
    assert client.accepts == [] and client.opens == 1


def test_deadline_hook_also_supports_agents_without_close_method(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    client = ChangingArena()
    table = RecordingTable(client, new_run(client.case["case_id"], "hosted", "baseline"))
    _, threads = table.open(client.case["case_id"])
    threads[client.sid].insert(0, {"from": "buyer", "body": "Counter."})
    monkeypatch.setattr("aeiou.runner.time.monotonic", lambda: 100)
    table.deadlines = {client.sid: 105}
    assert table.deadline_close(object(), threads)["message"]["action"]["type"] == "accept"


@pytest.mark.parametrize("explored", [False, True])
@pytest.mark.parametrize("progressing", [False, True])
@pytest.mark.parametrize("reply_seconds", [35, 55])
def test_deadline_continuation_balances_first_contact_and_incumbent(
    tmp_path, monkeypatch, explored, progressing, reply_seconds
):
    from aeiou.research import package
    from aeiou.strategic import StrategicNegotiator

    class NoCalls:
        def complete(self, *args):
            pytest.fail("Urgent continuation must not wait for the model")

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    client = ChangingArena()
    table = RecordingTable(client, new_run(client.case["case_id"], "hosted", "test"))
    case, threads = table.open(client.case["case_id"])
    threads[client.sid][-1]["status"] = "open"
    threads[client.sid].insert(0, {"from": "buyer", "body": "A prior counter."})
    if progressing:
        initial = copy.deepcopy(threads[client.sid][-1])
        initial["term_sheets"][0]["terms"]["unit_price"] = 18
        threads[client.sid].insert(0, initial)
    threads["other"] = [
        {
            **client.quote(),
            "status": "open",
            "term_sheets": [{"term_sheet_id": "offer:other", "terms": {"unit_price": 18, "payment_days": 30}}],
        }
    ]
    if explored:
        threads["other"].insert(0, {"from": "buyer", "body": "Already explored."})
    agent = StrategicNegotiator(package(), NoCalls())
    monkeypatch.setattr("aeiou.runner.time.monotonic", lambda: 100)
    table.slowest_reply = reply_seconds
    table.deadlines = {client.sid: 140, "other": 155}
    assert table.deadline_close(agent, threads) is None
    decision = agent.decide(case, threads)
    assert decision["supplier_id"] == (client.sid if explored or progressing else "other")
    assert decision["message"].get("term_sheets")
    assert agent.fallbacks == 0
    assert agent.controller["timing"]["policy_only"]
    assert table.run["transport_events"][-1]["type"] == (
        "deadline_focus" if explored or progressing else "deadline_explore"
    )
    # After one unanswered supplier update, stop exploration and protect the
    # incumbent; a first-contact policy must not spend all three timeouts.
    threads[client.sid].append(copy.deepcopy(threads[client.sid][-1]))
    assert table.deadline_close(agent, threads) is None
    assert agent.runtime_context["focus_supplier"] == client.sid
    table.deadlines[client.sid] = 105
    assert table.deadline_close(agent, threads)["message"]["action"]["type"] == "accept"
    assert not agent.runtime_context


def test_terminal_offer_is_not_reopened_by_deadline_focus(tmp_path, monkeypatch):
    from aeiou.research import package
    from aeiou.strategic import StrategicNegotiator

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    client = ChangingArena()
    table = RecordingTable(client, new_run(client.case["case_id"], "hosted", "test"))
    _, threads = table.open(client.case["case_id"])
    threads[client.sid].insert(0, {"from": "buyer", "body": "Counter"})
    monkeypatch.setattr("aeiou.runner.time.monotonic", lambda: 100)
    table.deadlines[client.sid] = 110
    agent = StrategicNegotiator(package())
    assert table.deadline_close(agent, threads)["message"]["action"]["type"] == "accept"
    assert not agent.runtime_context
