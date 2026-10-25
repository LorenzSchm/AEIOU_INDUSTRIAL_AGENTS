import copy
import json

import pytest

from aeiou.config import ROOT
from aeiou.optimizer import case_with_supplier_defaults, valid_terms
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package


def fixture():
    case = json.loads((ROOT / "data/package/cases/practice/P10.json").read_text())
    events = [
        {
            "from": "sales@70410",
            "status": "open",
            "body": "Our quoted delivery time is 54 days.",
            "term_sheets": [
                {
                    "term_sheet_id": "offer:70410-0",
                    "terms": {
                        "unit_price": 32.2,
                        "volume_per_year": 19890,
                        "contract_months": 12,
                        "lead_time_days": 54,
                    },
                }
            ],
        }
    ]
    return case, events


def test_supplier_opening_default_augments_options_without_mutating_case():
    case, events = fixture()
    original = copy.deepcopy((case, events))
    expanded = case_with_supplier_defaults(case, events)
    assert expanded["term_options"]["lead_time_days"] == [35, 42, 54, 56]
    assert expanded["fixed_terms"] == case["fixed_terms"]
    assert expanded["buyer"]["must_haves"] == case["buyer"]["must_haves"]
    expanded["term_options"]["lead_time_days"].append(99)
    assert (case, events) == original


def test_buyer_text_and_later_counter_do_not_redefine_the_opening_default():
    case, events = fixture()
    events[0]["body"] += " A hypothetical 53 days is under discussion."
    events.insert(0, {"from": "buyer", "term_sheets": [{"terms": {"lead_time_days": 53}}]})
    events.append({"from": "sales@70410", "term_sheets": [{"terms": {"lead_time_days": 52}}]})
    assert case_with_supplier_defaults(case, events)["term_options"]["lead_time_days"] == [35, 42, 54, 56]
    assert case_with_supplier_defaults(case, events[:1])["term_options"] == case["term_options"]


@pytest.mark.parametrize("value", [True, -1, 54.5, "54", float("nan")])
def test_malformed_defaults_cannot_add_an_option(value):
    case, events = fixture()
    events[0]["term_sheets"][0]["terms"]["lead_time_days"] = value
    assert case_with_supplier_defaults(case, events)["term_options"] == case["term_options"]


def test_observed_default_does_not_override_must_haves_or_leak_to_another_supplier():
    case, events = fixture()
    other = copy.deepcopy(events)
    other[0]["term_sheets"][0]["terms"]["lead_time_days"] = 42
    assert 54 not in case_with_supplier_defaults(case, other)["term_options"]["lead_time_days"]
    case["buyer"]["must_haves"].append({"field": "lead_time_days", "op": "<=", "value": 42})
    expanded = case_with_supplier_defaults(case, events)
    assert not valid_terms(
        expanded, {"unit_price": 31, "volume_per_year": 23400, "contract_months": 24, "lead_time_days": 54}
    )


def test_default_reaches_actual_model_input_and_send_validation():
    case, events = fixture()
    threads = {"sup:70410": events}
    agent = PromptLabAgent(package(), None, PromptSpec("test", "Negotiate P10.", assessment_enabled=True))
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, threads))
    assert 54 in payload["protocol_state"]["suppliers"]["sup:70410"]["term_options"]["lead_time_days"]
    rows = payload["price_plan"]["suppliers"]["sup:70410"]["capacity_opening_tradeoffs"]
    assert any(r["terms"]["lead_time_days"] == 54 for r in rows)
    terms = {"unit_price": 31, "volume_per_year": 23400, "contract_months": 24, "lead_time_days": 54}
    action = {
        "supplier_id": "sup:70410",
        "action": "send",
        "body": "Please retain the quoted 54 days.",
        "term_sheets": [terms],
    }
    sent = agent.contract(case, threads, action)
    assert sent["message"]["term_sheets"][0]["terms"]["lead_time_days"] == 54
    action["term_sheets"][0] = {**terms, "lead_time_days": 53}
    with pytest.raises(ContractError, match="valid must-have values"):
        agent.contract(case, threads, action)


def test_candidate_selector_uses_the_same_supplier_options_as_the_rendered_menu(monkeypatch):
    case, events = fixture()
    threads = {"sup:70410": events}
    agent = PromptLabAgent(package(), None, PromptSpec("test", "Negotiate P10."))
    agent.prepare(case)

    def candidates(supplier_case, *args):
        days = 54 if 54 in supplier_case["term_options"]["lead_time_days"] else 56
        return [
            {
                "id": "A",
                "terms": {"unit_price": 31, "volume_per_year": 23400, "contract_months": 24, "lead_time_days": days},
            }
        ]

    monkeypatch.setattr("aeiou.prompt_protocol.candidate_packages", candidates)
    shown = json.loads(agent.render_lab(case, threads))["candidate_packages"]["sup:70410"][0]["terms"]
    sent = agent.contract(
        case,
        threads,
        {"supplier_id": "sup:70410", "action": "send", "body": "We propose package A.", "candidate_ids": ["A"]},
    )
    assert shown == sent["message"]["term_sheets"][0]["terms"]
    assert shown["lead_time_days"] == 54
