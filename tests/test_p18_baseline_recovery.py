"""Replay routing and acceptance checks, not supplier-performance predictions."""

import copy
import json

import pytest
from arena_kit.cost import score, total_cost
from assessment_helpers import assessment_fields

from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package

SID = "sup:70740"
POLICY = json.loads((ROOT / "config/p18-baseline-recovery.json").read_text())["case:P18"]
STRATEGY = (ROOT / "prompts/p18-diagnostic-v23.txt").read_text()


def recorded(through):
    fixture = json.loads((ROOT / "tests/fixtures/p18-v19-verified-offer.json").read_text())
    return fixture["case"], {SID: fixture["threads"][SID][:through]}


class Model:
    def __init__(self, terms=None):
        self.terms = terms
        self.payloads = []

    def complete(self, system, user):
        assert self.terms is not None, "Acceptance, early menu and final stop must not call the model"
        payload = json.loads(user)
        self.payloads.append(payload)
        return json.dumps(
            {
                "supplier_id": SID,
                "action": "send",
                "body": "Thank you. Could you approve the attached complete proposal?",
                "term_sheets": [self.terms],
                **assessment_fields(payload, SID),
            }
        )


def agent(backend):
    return PromptLabAgent(
        package(),
        backend,
        PromptSpec("v23-recovery-offline", STRATEGY, assessment_enabled=True, execution_policy=copy.deepcopy(POLICY)),
    )


@pytest.mark.parametrize("through", [1, 5])
def test_opening_and_post_menu_response_reach_model_instead_of_fixed_sequence(through):
    case, threads = recorded(through)
    terms = {**POLICY["early_package_alternatives"]["packages"][0], "contract_months": 36}
    if through == 1:
        terms.update(unit_price=4.60, index_clause={**terms["index_clause"], "deadband_pct": 0})
    backend = Model(terms)
    a = agent(backend)
    move = a.decide(case, threads)
    assert len(backend.payloads) == 1
    assert move["message"]["term_sheets"] == [{"terms": terms}]
    assert len(a.decisions[-1]["model_requests"]) == 1
    assert not a.decisions[-1]["source"].startswith("policy:")
    if through == 5:
        # Both the quoted 60-day primary and 30-day alternative reach the model.
        offers = backend.payloads[0]["protocol_state"]["suppliers"][SID]["offers"]
        assert {o["terms"]["payment_days"] for o in offers} == {30, 60}


def test_checked_early_menu_survives_recovery():
    case, threads = recorded(3)
    a = agent(Model())
    move = a.decide(case, threads)
    assert [s["terms"] for s in move["message"]["term_sheets"]] == [
        {**p, "contract_months": 36} for p in POLICY["early_package_alternatives"]["packages"]
    ]
    assert a.decisions[-1]["source"] == "policy:package-alternatives"
    assert a.decisions[-1]["execution_evidence"]["status"] == "included"
    assert a.decisions[-1]["numeric_checks"][0]["calculation_id"] == "p18:ppi_recent_change_pct"


def test_best_historical_final_is_still_below_floor_and_stops_without_email():
    case, threads = recorded(7)
    terms = threads[SID][-1]["term_sheets"][0]["terms"]
    cost = total_cost(case, terms)
    assert cost == 245527.26
    assert score(295173.23, cost, 222651.27) * 100 == pytest.approx(68.4564648)
    a = agent(Model())
    with pytest.raises(NegotiationBlocked, match="FINAL_OFFER_OUTSIDE_LIMITS"):
        a.decide(case, threads)
    assert not a.audit["model_requests"] and not a.audit["walk_aways"]


@pytest.mark.parametrize("through", [3, 7])
def test_synthetic_qualifying_offer_is_accepted_before_menu_or_final_stop(through):
    case, threads = recorded(through)
    offer = threads[SID][-1]["term_sheets"][0]
    offer["terms"] = {**POLICY["early_package_alternatives"]["packages"][2], "contract_months": 36}
    a = agent(Model())
    assert a.decide(case, threads)["message"]["action"] == {"type": "accept", "term_sheet_id": offer["term_sheet_id"]}
    assert a.decisions[-1]["source"] == "policy:accept"
