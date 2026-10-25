import copy
import json
from decimal import Decimal

import pytest
from arena_kit.cost import total_cost
from assessment_helpers import assessment_fields
from test_negotiation_data_pipeline import NeverCall, scenario

from aeiou.config import ROOT
from aeiou.execution_policy import controlled_action
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package

POLICY = json.loads((ROOT / "config/p18-above-70.json").read_text())["case:P18"]
STRATEGY = (ROOT / "prompts/p18-diagnostic-v15.txt").read_text()


def agent(backend=None):
    return PromptLabAgent(package(), backend or NeverCall(), PromptSpec(
        "p18-above-70", STRATEGY, assessment_enabled=True, execution_policy=copy.deepcopy(POLICY)))


def offer(price, deadband=0, days=14, status="open"):
    case, sid, threads = scenario(price, status)
    terms = copy.deepcopy(threads[sid][-1]["term_sheets"][0]["terms"])
    terms["index_clause"]["deadband_pct"] = deadband
    terms["payment_days"] = days
    threads[sid][-1]["term_sheets"][0]["terms"] = terms
    return case, sid, threads


def test_strict_floor_matches_config_and_old_598_offer_continues():
    case, sid, threads = offer(5.10)

    class Buyer:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            assert payload["protocol_state"]["suppliers"][sid]["legal_actions"] == ["send"]
            terms = copy.deepcopy(threads[sid][-1]["term_sheets"][0]["terms"])
            terms["unit_price"] = 4.83
            return json.dumps({"supplier_id": sid, "action": "send", "body": "Thank you. Please consider this package.",
                               "term_sheets": [terms], **assessment_fields(payload, sid)})

    buyer = Buyer()
    a = agent(buyer)
    assert a.acceptance_floor.max_cost == Decimal("244400.60")
    assert a.acceptance_floor.minimum_score == Decimal("70.01")
    assert a.acceptance_floor.allows_cost(244400.60)
    assert not a.acceptance_floor.allows_cost(244400.61)
    assert a.decide(case, threads)["message"]["term_sheets"][0]["terms"]["unit_price"] == 4.83
    assert buyer.calls == 1
    with pytest.raises(ContractError, match="execution controls"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:current"})


@pytest.mark.parametrize("price,deadband,days", [(4.94, 0, 14), (5.01, 3, 14), (5.05, 5, 14),
                                               (5.10, 5, 60), (5.10, 5, 90)])
def test_qualifying_package_accepts_exact_offer_without_model(price, deadband, days):
    case, sid, threads = offer(price, deadband, days)
    a = agent()
    move = a.decide(case, threads)
    assert move["message"]["action"] == {"type": "accept", "term_sheet_id": "offer:current"}
    cost = total_cost(case, threads[sid][-1]["term_sheets"][0]["terms"])
    assert (295173.23 - cost) / (295173.23 - 222651.27) * 100 > 70


@pytest.mark.parametrize("price,deadband", [(4.95, 0), (5.02, 3), (5.06, 5), (5.10, 0)])
def test_one_cent_above_package_limit_cannot_be_accepted_or_proposed(price, deadband):
    case, sid, threads = offer(price, deadband)
    a = agent()
    with pytest.raises(ContractError, match="execution controls"):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:current"})
    terms = threads[sid][-1]["term_sheets"][0]["terms"]
    with pytest.raises(ContractError, match="complete-cost"):
        a.contract(case, threads, {"supplier_id": sid, "action": "send", "body": "Our proposal.",
                                   "term_sheets": [terms]})


def test_final_with_zero_deadband_gets_affordable_counter_not_509():
    case, sid, threads = offer(5.30, status="final_offer")
    a = agent()
    move = a.decide(case, threads)
    assert move["message"]["term_sheets"][0]["terms"] == {
        **threads[sid][-1]["term_sheets"][0]["terms"], "unit_price": 4.94}
    plan = a.decisions[-1]["price_plan"]["offers"][0]
    assert plan["budget_ceiling_package"]["estimated_score"] > 70
    assert {70, 70.01} <= {b["score"] for b in plan["benchmark_price_limits"]}


def test_final_menu_uses_higher_affordable_price_and_never_repeats_counter():
    case, sid, threads = offer(5.10, status="final_offer")
    alternative = copy.deepcopy(threads[sid][-1]["term_sheets"][0])
    alternative["term_sheet_id"] = "offer:alternative"
    alternative["terms"]["unit_price"] = 5.20
    alternative["terms"]["index_clause"]["deadband_pct"] = 5
    threads[sid][-1]["term_sheets"].append(alternative)
    threads[sid][-1]["standing_offer_id"] = "offer:current"
    a = agent()
    state = a.decision_protocol_state(case, threads)
    assert not state["suppliers"][sid]["offers"][1]["server_acceptance_available"]
    with pytest.raises(ContractError):
        a.contract(case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": "offer:alternative"})
    move = a.decide(case, threads)
    assert move["message"]["term_sheets"][0]["terms"] == {**alternative["terms"], "unit_price": 5.05}
    repeated = copy.deepcopy(threads[sid][-1])
    threads[sid].extend([{**move["message"], "from": "buyer"}, repeated])
    reconstructed = agent()
    state = reconstructed.decision_protocol_state(case, threads)
    assert controlled_action(case, threads, state, reconstructed.acceptance_floor, POLICY) is None
    # A menu change must not reset the one-counter authorization.
    threads[sid][-1]["term_sheets"] = [repeated["term_sheets"][0]]
    state = reconstructed.decision_protocol_state(case, threads)
    assert controlled_action(case, threads, state, reconstructed.acceptance_floor, POLICY) is None


def test_adaptive_setting_is_explicit_and_typed():
    spec = PromptSpec("bad", STRATEGY, execution_policy={**POLICY, "adapt_closing_price_to_budget": "true"})
    with pytest.raises(ValueError, match="must be a boolean"):
        PromptLabAgent(package(), None, spec)
