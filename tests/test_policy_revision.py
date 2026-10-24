import json
from pathlib import Path

from arena_kit.cost import total_cost

from aeiou.grounding import next_evidence_ids, rival_quote_cards
from aeiou.research import package
from aeiou.scenario_planner import equivalent_probe
from aeiou.strategic import StrategicNegotiator
from aeiou.supplier_signals import validated_interpretation


def fixture(name):
    return json.loads((Path(__file__).parent / "fixtures" / name).read_text())


def test_d01_strategy_switch_preserves_previous_concession_and_horizon():
    data = fixture("d01_public_feedback.json")
    agent = StrategicNegotiator(package())
    move = agent.decide(data["case"], data["threads"])
    events = data["threads"][move["supplier_id"]]
    previous = events[-2]["term_sheets"][0]["terms"]
    plans = agent.support[move["supplier_id"]]["plan"]["packages"]
    base = next(p for p in plans if p["id"] == "A")
    assert base["terms"]["unit_price"] >= previous["unit_price"]
    assert all(p["terms"]["contract_months"] == 24 for p in plans)
    closing = next(p for p in plans if p.get("closing_probe"))
    assert closing["cost"] < closing["standing_cost"]
    assert closing["terms"]["effective_date"] == "2027-04-01"
    assert closing["terms"]["unit_price"] < events[-1]["term_sheets"][0]["terms"]["unit_price"]


def test_higher_price_can_pay_for_later_start_without_increasing_total_cost():
    case = fixture("d01_public_opening.json")["case"]
    base = {"unit_price": 51.1, "payment_days": 30, "contract_months": 24, "effective_date": "2026-12-01"}
    probe = equivalent_probe(case, base, "effective_date", "2027-04-01", lambda t: total_cost(case, t))
    assert probe["unit_price"] > base["unit_price"]
    assert total_cost(case, probe) <= total_cost(case, base)


def test_interpretation_requires_known_kind_and_exact_quote():
    body = "Cash flow matters, but we can wait."
    assert validated_interpretation({"signals": [{"kind": "liquidity", "quote": "Cash flow matters"}]}, body, 2)
    assert not validated_interpretation({"signals": [{"kind": "liquidity", "quote": "Our floor is 30"}]}, body, 2)
    assert not validated_interpretation({"signals": [{"kind": "accept", "quote": body}]}, body, 2)
    assert not validated_interpretation({"signals": [{"kind": {}, "quote": body}]}, body, 2)


def test_real_rival_offer_can_be_cited_once_per_recipient_without_equivalence_claim():
    data = fixture("public_supplier_feedback.json")["d02"]
    cards = rival_quote_cards(data["case"], data["threads"], {}, "sup:DEMO-C1")
    assert cards and cards[0]["source_id"].startswith("offer:")
    assert "quoted conditions" in cards[0]["text"]
    assert next_evidence_ids(cards, data["threads"], "sup:DEMO-C1") == [cards[0]["id"]]
    data["threads"]["sup:DEMO-C1"].append({"from": "buyer", "body": cards[0]["text"]})
    assert not rival_quote_cards(data["case"], data["threads"], {}, "sup:DEMO-C1")


def test_supplier_interpretation_is_cached_and_does_not_choose_actions():
    class Backend:
        def __init__(self):
            self.interpretations = 0

        def interpret_supplier(self, body):
            self.interpretations += 1
            return {"signals": [{"kind": "continuity", "quote": "our 12-year relationship"}], "action": "walk_away"}

        def complete(self, system, user):
            return json.dumps(
                {**json.loads(user)["planned_action"], "opening": "warm", "question": "cash", "evidence_ids": []}
            )

    data = fixture("d01_public_feedback.json")
    backend = Backend()
    agent = StrategicNegotiator(package(), backend)
    for _ in range(2):
        move = agent.decide(data["case"], data["threads"])
        assert move["message"].get("action", {}).get("type") != "walk_away"
    assert backend.interpretations == 1
    assert any(s["source"] == "llm_interpretation" for s in agent.support[move["supplier_id"]]["state"]["signals"])
