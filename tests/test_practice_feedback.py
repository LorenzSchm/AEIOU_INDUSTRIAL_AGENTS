import json
from pathlib import Path

from arena_kit.cost import total_cost

from aeiou.grounding import next_evidence_ids, safe_excerpt
from aeiou.optimizer import valid_terms
from aeiou.research import package
from aeiou.scenario_planner import distinct_packages
from aeiou.strategic import StrategicNegotiator
from aeiou.strategic_planner import admit_proposals


def opening(name):
    return json.loads((Path(__file__).parent / "fixtures/practice_public_openings.json").read_text())[name]


def test_same_terms_never_offer_supplier_an_optional_price_increase():
    a = {"id": "A", "terms": {"unit_price": 10, "payment_days": 30}, "cost": 100}
    b = {"id": "B", "terms": {"unit_price": 9, "payment_days": 14}, "cost": 99}
    c = {"id": "C", "terms": {"unit_price": 12, "payment_days": 30}, "cost": 120}
    assert distinct_packages([a, b, c]) == [a, b]


def test_quality_opening_explores_compensation_within_required_quality():
    data = opening("P11")
    agent = StrategicNegotiator(package())
    move = agent.decide(data["case"], data["threads"])
    packages = agent.support[move["supplier_id"]]["plan"]["packages"]
    base = packages[0]
    assert base["terms"]["ppm_commitment"] == 1500  # published must-have, not an inferred floor
    assert len(packages) >= 2
    assert len(
        {json.dumps({k: v for k, v in p["terms"].items() if k != "unit_price"}, sort_keys=True) for p in packages}
    ) == len(packages)
    for p in packages:
        assert valid_terms(data["case"], p["terms"])
        assert p["cost"] <= base["cost"] + 0.01
    assert any(
        p["terms"]["chargeback_eur_per_complaint"] < base["terms"]["chargeback_eur_per_complaint"]
        and p["terms"]["unit_price"] < base["terms"]["unit_price"]
        for p in packages
    )
    old = {**base["terms"], "ppm_commitment": 300}
    assert total_cost(data["case"], base["terms"]) == total_cost(data["case"], old)


def test_writer_receives_only_eligible_cards_and_can_explicitly_choose_none():
    class Writer:
        def complete(self, system, user):
            payload = json.loads(user)
            sid = payload["planned_action"]["supplier_id"]
            cards = payload["research"]["evidence_cards"]
            assert all(c["shareable"] and c.get("supplier_id") in {None, sid} for c in cards)
            assert payload["available_evidence_ids"] == [c["id"] for c in cards]
            assert "estimates" not in payload["research"]
            return json.dumps({**payload["planned_action"], "evidence_ids": []})

    data = opening("P01")
    agent = StrategicNegotiator(package(), Writer())
    move = agent.decide(data["case"], data["threads"])
    assert agent.fallbacks == 0
    assert "document states" not in move["message"]["body"]
    assert "idx:" not in move["message"]["body"]


def test_raw_material_risk_is_explored_only_with_price_compensation():
    data = opening("P18")
    agent = StrategicNegotiator(package())
    move = agent.decide(data["case"], data["threads"])
    packages = agent.support[move["supplier_id"]]["plan"]["packages"]
    assert packages[0]["terms"]["index_clause"] is None
    indexed = [p for p in packages if p["terms"]["index_clause"] is not None]
    assert indexed
    assert all(p["cost"] <= packages[0]["cost"] + 0.01 for p in indexed)
    assert all(valid_terms(data["case"], p["terms"]) for p in packages)


def test_model_cannot_undo_improved_opening_with_same_terms_and_higher_price():
    data = opening("P02")
    agent = StrategicNegotiator(package())
    move = agent.decide(data["case"], data["threads"])
    sid = move["supplier_id"]
    packages = agent.support[sid]["plan"]["packages"]
    proposal = {
        "supplier_id": sid,
        "action": "send",
        "proposals": [
            {
                "base_id": "A",
                "changes": {
                    "unit_price": packages[0]["uncertainty_anchor"]["original_price"],
                },
            }
        ],
    }
    result, trace = admit_proposals(
        data["case"],
        data["threads"],
        sid,
        packages,
        agent.intelligence,
        proposal,
        agent.support[sid]["belief"],
    )
    assert result == packages
    assert trace["rejected"]


def test_initial_portfolio_does_not_give_away_savings_through_index_risk():
    data = opening("P02")
    agent = StrategicNegotiator(package())
    move = agent.decide(data["case"], data["threads"])
    packages = agent.support[move["supplier_id"]]["plan"]["packages"]
    anchor = packages[0]
    audit = anchor["uncertainty_anchor"]
    assert anchor["terms"]["unit_price"] < audit["original_price"]
    assert anchor["cost"] <= audit["point_cost_limit"]
    assert audit["expected_cost"] < audit["original_expected_cost"]
    assert all(p["terms"]["index_clause"] is None for p in packages)
    assert all(valid_terms(data["case"], p["terms"]) for p in packages)


def test_automatic_evidence_does_not_use_rising_index_as_discount_argument():
    card = {
        "id": "index:TEST",
        "kind": "computed_index",
        "change_pct": 12,
        "shareable": True,
        "source_ids": ["idx:TEST@2026-09"],
    }
    assert next_evidence_ids([card], {}, "supplier") == []
    request = {"supplier": [{"from": "seller", "body": "Please substantiate your argument with actual data IDs."}]}
    assert next_evidence_ids([card], request, "supplier") == [card["id"]]
    assert next_evidence_ids([{**card, "change_pct": -12}], {}, "supplier") == [card["id"]]
    assert safe_excerpt("To Our Important Supplier Company.\n\nOur price is fixed for this quarter only.") == (
        "Our price is fixed for this quarter only."
    )
