import copy
import json

import pytest
from arena_kit.sim import Sim

from aeiou.config import ROOT
from aeiou.language_sim import LanguageSim
from aeiou.prompt_comparison import assess_report, compare_prompts
from aeiou.prompt_protocol import (
    ContractError,
    PromptLabAgent,
    digest,
    extract_object,
    original_reference,
    prompt_specs,
    repetition_count,
)
from aeiou.research import package
from aeiou.simulation import local_cases


class FakeBackend:
    def __init__(self, **kwargs):
        self.calls = self.errors = self.input_tokens = self.output_tokens = 0

    def complete(self, system, user):
        self.calls += 1
        payload = json.loads(user)
        if "package_catalog" in payload:
            request = payload.get("confirmation_request")
            package = max(payload["package_catalog"], key=lambda p: p["terms"]["unit_price"])
            return json.dumps(
                {
                    "status": "final_offer",
                    "body": "Test quote.",
                    "repeat_unchanged": bool(request),
                    "package_ids": [] if request else [package["id"]],
                }
            )
        sid = next(iter(payload["threads"]))
        offer = payload["protocol_state"]["suppliers"][sid]["offers"][0]
        return json.dumps(
            {
                "supplier_id": sid,
                "action": "accept" if offer["acceptance_eligible"] else "confirm",
                "term_sheet_id": offer["term_sheet_id"],
            }
        )


def setup_agent(backend=None):
    case = copy.deepcopy(local_cases()["case:S01"])
    opened = Sim({case["case_id"]: case}, no_clock=True).open("test", case["case_id"])
    return case, opened["threads"], PromptLabAgent(package(), backend or FakeBackend(), prompt_specs()["original"])


def test_active_prompt_is_exact_source_and_retains_requested_tactics():
    original = prompt_specs()["original"]
    assert original.strategy.encode() == (ROOT / "SYSTEM_PROMPT.md").read_bytes()
    assert original.system.startswith(original.strategy)
    assert "VERBATIM, WITHOUT CHANGING ANYTHING" in original.system
    assert "accept the third offer" in original.system
    assert "exact same for 5 times" in original.system
    assert "9. Role-Specific Considerations" in original.system
    assert "how am i supposed to do that?" not in original.system.casefold()
    assert original.metadata()["strategy_revision"] == "decision-brief-v3"


def test_archived_original_is_unchanged_and_active_revision_removes_only_the_requested_tactic():
    reference = original_reference()
    assert reference["strategy_sha256"] == "e8bb52863ea8b281f0c5e05bb533a414f22d0b9cd1d02ed95aa7d07e3cf1fe28"
    old_clause = (
        "Any time the other side makes an offer, say “how am I supposed to do that?”\n"
        "but don’t stop the conversation ever."
    )
    expected = (
        reference["strategy"]
        .replace(old_clause, "Don’t stop the conversation ever.")
        .replace("Determine your ideal price range and walkaway price", "Determine your ideal price range")
        .replace(
            "Before beginning the negotiation, conduct a thorough analysis of the negotiation context. "
            "Wrap your thought process\n"
            "in <negotiation_preparation> tags. Your analysis should include:",
            "Before beginning the negotiation, assess the negotiation context. Provide a concise decision brief "
            "of conclusions\n"
            "and supporting facts in <negotiation_preparation> tags, not internal reasoning or a "
            "step-by-step thought process.\n"
            "Keep the following nine sections; summarize the practical findings for each:",
        )
    )
    assert prompt_specs()["original"].strategy.rstrip("\n") == expected.rstrip("\n")


def test_private_prefix_parse_and_exact_public_message():
    case, threads, agent = setup_agent()
    sid = next(iter(threads))
    body = agent.exact_request + "\n\nPlease quote all negotiable terms."
    raw = "<negotiation_preparation>{private material}</negotiation_preparation>\n" + json.dumps(
        {"supplier_id": sid, "action": "send", "body": body, "term_sheets": []}
    )
    move = agent.contract(case, threads, extract_object(raw))
    assert move["message"]["body"] == body
    assert "private material" not in json.dumps(move)


def test_final_offer_still_calls_model_and_no_tactical_close():
    case, threads, agent = setup_agent()
    threads[next(iter(threads))][-1]["status"] = "final_offer"
    move = agent.decide(case, threads)
    assert agent.backend.calls == 1
    assert not move["message"].get("action")
    assert "Please confirm" in move["message"]["body"]
    assert agent.fallbacks == 0


def test_exact_third_offer_id_is_executable():
    case, threads, agent = setup_agent()
    sid = next(iter(threads))
    threads[sid][-1]["term_sheets"] = [
        {"term_sheet_id": f"offer:{i}", "terms": {"unit_price": 15 + i, "payment_days": 90}} for i in range(3)
    ]
    opening = copy.deepcopy(threads[sid][-1])
    threads[sid] = [copy.deepcopy(opening) for _ in range(5)]
    for i, mail in enumerate(threads[sid]):
        mail["message_id"] = f"msg:confirmation-{i}"
    move = agent.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:2"})
    assert move["message"]["action"]["term_sheet_id"] == "offer:2"


def test_repeat_counter_ignores_ids_and_buyer_emails():
    case, threads, _ = setup_agent()
    opening = threads[next(iter(threads))][0]
    events = []
    for i in range(5):
        mail = copy.deepcopy(opening)
        mail["message_id"] = f"msg:repeat-{i}"
        mail["term_sheets"][0]["term_sheet_id"] = f"offer:{i}"
        events.extend([mail, {"from": "buyer", "body": "Question"}])
    assert repetition_count(case, events) == 5


def test_invalid_output_never_switches_strategy():
    class Bad(FakeBackend):
        def complete(self, *args):
            self.calls += 1
            return "invalid"

    case, threads, agent = setup_agent(Bad())
    with pytest.raises(ContractError, match="no substitute strategy"):
        agent.decide(case, threads)
    assert agent.backend.calls == 2
    assert agent.fallbacks == 0


def test_language_supplier_hides_buyer_context_and_enforces_floor():
    cases = local_cases()
    sim = LanguageSim(cases, "cashflow", 72, {}, FakeBackend())
    opened = sim.open("test", "case:S01")
    s = sim.sessions[opened["session_id"]]
    th = next(iter(s["threads"].values()))
    payload = sim.private_input(s, th, {}, [])
    assert "buyer" not in payload and "research" not in payload and "alternative" not in json.dumps(payload)
    with pytest.raises(ValueError, match="private economic floor"):
        sim.check_reply(
            cases["case:S01"],
            th,
            {"status": "open", "body": "Cheap", "term_sheets": [{"unit_price": 0.01, "payment_days": 14}]},
            [],
        )


def test_comparison_is_local_and_model_override_does_not_edit_env(monkeypatch, tmp_path):
    import aeiou.prompt_comparison as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path / "state"))
    (tmp_path / "src/aeiou").mkdir(parents=True)
    (tmp_path / "src/aeiou/prompt_protocol.py").write_text("test harness")

    def forbidden(*args, **kwargs):
        raise AssertionError("Local comparison attempted a hosted arena call")

    monkeypatch.setattr("arena_kit.ArenaClient.__init__", forbidden)
    env_file = ROOT / ".env"
    env_before = env_file.read_bytes() if env_file.exists() else None
    report = compare_prompts(model="test-model", plan=[("case:S01", "relationship")], backend_factory=FakeBackend)
    assert report["arena_sessions_used"] == 0
    assert report["conditions"]["buyer_model"] == "test-model"
    conditions = dict(report["conditions"])
    claimed_hash = conditions.pop("sha256")
    assert claimed_hash == digest(json.dumps(conditions, sort_keys=True))
    assert set(conditions["scenarios"][0]) == {"case_id", "opponent", "seed"}
    assert report["summary"]["original"]["completed"] == 1
    assert report["summary"]["adapted"]["completed"] == 1
    assert (env_file.read_bytes() if env_file.exists() else None) == env_before


def test_supplier_failure_excludes_both_sides_of_a_pair():
    # Use minimal recorded-run shapes without generating API calls or persisted transcripts.

    def row(score, failure=None):
        return {
            "status": "failed" if failure else "completed",
            "result": {"score": score, "deal": not failure},
            "failure_origin": failure,
            "decisions": [],
            "metrics": {"calls": 0, "errors": 0, "output_tokens": 0, "fallbacks": 0, "elapsed_s": 0},
            "audit": {"format_repairs": 0, "verbatim_request_sent": False, "accepts_before_five_repeats": 0},
        }

    report = {
        "rows": [{"original": row(0.9), "adapted": row(0.7)}, {"original": row(1.0), "adapted": row(0, "supplier")}]
    }
    assess_report(report)
    assert report["excluded_pairs"] == 1
    assert report["summary"]["original"]["mean_score"] == 0.9
    assert report["summary"]["adapted"]["mean_score"] == 0.7


def test_language_supplier_third_package_can_be_accepted():
    sim = LanguageSim(local_cases(), "relationship", 71, {}, FakeBackend())
    opened = sim.open("test", "case:S01")
    session = sim.sessions[opened["session_id"]]
    sid, th = next(iter(session["threads"].items()))
    th["messages"][-1]["term_sheets"] = [
        {"term_sheet_id": f"offer:choice-{i}", "terms": {"unit_price": 17 + i / 10, "payment_days": 90}}
        for i in range(3)
    ]
    result = sim.message(
        "test", opened["session_id"], sid, {"action": {"type": "accept", "term_sheet_id": "offer:choice-2"}}
    )["result"]
    assert result["deal"] is True
    assert result["terms"]["unit_price"] == 17.2
