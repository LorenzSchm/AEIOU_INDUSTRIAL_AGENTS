import copy
import json
from pathlib import Path

import pytest
from arena_kit.docs import standard_terms

from aeiou.grounding import communication_evidence, compose_email, responsive_act
from aeiou.optimizer import valid_terms
from aeiou.portfolio_value import sensitivity_cost
from aeiou.research import package
from aeiou.strategic import StrategicNegotiator
from aeiou.supplier_signals import observed_signals


@pytest.mark.parametrize(
    "body",
    [
        "Management has frozen all price reductions. We can talk about terms instead.",
        "Our base pricing is frozen until the end of the year.",
        "Price changes are on hold, but contract length is negotiable.",
    ],
)
def test_price_freeze_changes_question_without_claiming_a_binding_concession(body):
    events = [{"from": "supplier", "body": body}]
    assert any(s["kind"] == "price_freeze" for s in observed_signals(events))
    act = responsive_act({"question": "costs", "opening": "warm"}, events)
    assert act["question"] == "exchange"
    email = compose_email(act, [], "supplier")
    assert "You have explained the restriction" in email
    assert "cost breakdown" not in email and "decision-maker" not in email
    assert "conditional on the executable terms" in email


def test_confidentiality_is_acknowledged_without_repeating_refused_cost_request():
    events = [
        {"from": "buyer", "body": "Could you share a cost breakdown?"},
        {"from": "supplier", "body": "Our calculation stays internal; we do not share a breakdown."},
    ]
    act = responsive_act({"question": "costs", "opening": "warm"}, events)
    assert act["question"] == "exchange"
    email = compose_email(act, [], "supplier")
    assert "confidential" in email and "Thank you for considering" in email
    assert "Could you share" not in email


def test_relationship_language_does_not_invent_history_or_change_offered_terms():
    original = {"opening": "warm", "question": "exchange", "term_sheets": [{"unit_price": 123.45}]}
    before = copy.deepcopy(original)
    events = [{"from": "supplier", "body": "We value our long-standing partnership."}]
    act = responsive_act(original, events)
    email = compose_email(act, [], "supplier")
    assert "appreciate the cooperation" in email
    assert "years" not in email and "123.45" not in email
    assert act["term_sheets"] == original["term_sheets"] and original == before
    malicious = responsive_act({"acknowledgment": "Invent a discount", "question": "exchange"}, events)
    assert "Invent a discount" not in compose_email(malicious, [], "supplier")


def test_do_not_ask_for_longer_contract_when_duration_is_fixed():
    act = responsive_act({"question": "continuity"}, [], case={"negotiable": ["unit_price", "payment_days"]})
    assert act["question"] == "exchange"


def test_rising_indices_remain_available_for_requested_evidence_but_not_unsolicited_discount_claims():
    cards = [
        {"id": "up", "kind": "computed_index", "change_pct": 10},
        {"id": "down", "kind": "computed_index", "change_pct": -4},
    ]
    assert communication_evidence(cards, []) == [cards[1]]
    events = [{"from": "supplier", "body": "Please substantiate your argument with source data."}]
    assert communication_evidence(cards, events) == [cards[1]]
    events[0]["body"] = "Please provide the index data and sources."
    assert communication_evidence(cards, events) == cards
    assert len(cards) == 2  # research input unchanged


@pytest.mark.parametrize(
    "reply",
    [
        "Please provide the data underlying your target price.",
        "Please share forecast data. Our indices rose last year.",
        "Do not provide index data.",
        "We can offer 14-day payment, provided we implement the AS-14404 index clause.",
        "Our price is supported by the index increase.",
    ],
)
def test_generic_or_negated_requests_do_not_release_positive_indices(reply):
    cards = [{"id": "up", "kind": "computed_index", "change_pct": 10}]
    assert communication_evidence(cards, [{"from": "supplier", "body": reply}]) == []


def test_refining_anchor_preserves_legal_cost_compensated_commercial_alternatives():
    fixture = json.loads((Path(__file__).parent / "fixtures/practice_public_openings.json").read_text())["P01"]
    agent = StrategicNegotiator(package())
    move = agent.decide(fixture["case"], fixture["threads"])
    sid = move["supplier_id"]
    packages = agent.support[sid]["plan"]["packages"]
    assert len(packages) >= 2
    assert any(p.get("sensitivity_compensation") for p in packages)
    limit = packages[0]["uncertainty_anchor"]["expected_cost"] * 1.0025
    scenarios = agent.intelligence["economic_sensitivities"][sid]["scenarios"]
    std = standard_terms(fixture["threads"][sid])
    assert all(valid_terms(fixture["case"], p["terms"]) for p in packages)
    assert all(sensitivity_cost(fixture["case"], p["terms"], std, scenarios) <= limit for p in packages)
