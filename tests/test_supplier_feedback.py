import json
from pathlib import Path

from aeiou.grounding import fresh_evidence_cards, responsive_act
from aeiou.optimizer import valid_terms
from aeiou.research import package
from aeiou.strategic import StrategicNegotiator


def public_fixture(name):
    data = json.loads((Path(__file__).parent / "fixtures/public_supplier_feedback.json").read_text(encoding="utf-8"))
    return data[name]["case"], data[name]["threads"]


def test_p18_counter_produces_compensated_index_package_without_repeating_evidence():
    case, threads = public_fixture("p18")
    agent = StrategicNegotiator(package())
    move = agent.decide(case, threads)
    sid = move["supplier_id"]
    plan = agent.support[sid]["plan"]["packages"]
    protected = next(p for p in plan if p["id"] == "A")
    probe = next(p for p in plan if p.get("feedback_probe"))
    assert protected["terms"]["index_clause"] is None
    assert probe["terms"]["index_clause"] is not None
    assert probe["cost"] <= protected["cost"] + 0.01
    assert probe["terms"]["unit_price"] < protected["terms"]["unit_price"]
    assert probe["id"] in agent.controller["candidate_ids"]
    assert all(valid_terms(case, p["terms"]) for p in plan)
    assert "idx:AS-14404@2025-09" not in move["message"]["body"]
    assert "cost breakdown" not in move["message"]["body"]
    assert "workable exchange" in move["message"]["body"]
    # A supplier repeating the indexed structure after seeing our new probe
    # must not cause the controller to revert to fixed-price-only proposals.
    reply = dict(threads[sid][-1])
    threads[sid].extend([{**move["message"], "from": "buyer"}, reply])
    followup = agent.decide(case, threads)
    assert any(s["terms"]["index_clause"] is not None for s in followup["message"]["term_sheets"])


def test_d02_uses_needed_delivery_date_and_offers_the_supplier_counter_structure():
    case, threads = public_fixture("d02")
    agent = StrategicNegotiator(package())
    agent.decide(case, threads)
    for support in agent.support.values():
        base = next(p for p in support["plan"]["packages"] if p["id"] == "A")
        assert base["terms"]["lead_time_days"] == case["buyer"]["need_lead_time_days"] == 35
    plan = agent.support["sup:DEMO-C1"]["plan"]["packages"]
    probe = next(p for p in plan if p.get("feedback_probe"))
    assert probe["terms"]["lead_time_days"] == 28
    assert probe["terms"]["payment_days"] == 14
    assert probe["cost"] <= plan[0]["cost"] + 0.01


def test_deduplication_uses_sources_not_chunk_ids():
    cards = [
        {"id": "new-chunk", "source_id": "doc:20260922-70740-P18"},
        {"id": "fresh", "source_ids": ["idx:LCI-C@2026-06"]},
    ]
    threads = {"seller": [{"from": "buyer", "body": "See doc:20260922-70740-P18."}]}
    assert fresh_evidence_cards(cards, threads) == [cards[1]]


def test_writer_cannot_repeat_a_source_that_was_removed_from_its_context():
    class RepeatingWriter:
        def complete(self, system, user):
            payload = json.loads(user)
            return json.dumps({**payload["planned_action"], "action": "send", "evidence_ids": ["index:AS-14404"]})

    case, threads = public_fixture("p18")
    agent = StrategicNegotiator(package(), RepeatingWriter())
    move = agent.decide(case, threads)
    assert agent.fallbacks == 1
    assert "idx:AS-14404@2025-09" not in move["message"]["body"]
    assert agent.decisions[-1]["guard_rejections"]


def test_repeated_breakdown_question_moves_to_actionable_trade():
    events = [{"from": "buyer", "body": "Could you share a cost breakdown?"}]
    assert responsive_act({"question": "costs"}, events)["question"] == "priorities"
    assert responsive_act({"question": "costs"}, events, feedback_probe=True)["question"] == "tradeoff"
