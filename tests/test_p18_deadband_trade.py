"""Economic and execution checks for the proposed trade, not acceptance predictions."""

import copy
import json

import pytest
from arena_kit.cost import score, total_cost

from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package

SID = "sup:70740"
POLICY = json.loads((ROOT / "config/p18-deadband-trade.json").read_text())["case:P18"]
STRATEGY = (ROOT / "prompts/p18-diagnostic-v24.txt").read_text()


class NoModel:
    def complete(self, *args):
        raise AssertionError("Menu, exact acceptance and final stop must not call the model")


def recorded(through):
    run = json.loads((ROOT / "tests/fixtures/p18-v19-verified-offer.json").read_text())
    return run["case"], {SID: run["threads"][SID][:through]}


def agent():
    return PromptLabAgent(
        package(),
        NoModel(),
        PromptSpec("v24-offline", STRATEGY, assessment_enabled=True, execution_policy=copy.deepcopy(POLICY)),
    )


def test_menu_transports_different_clauses_with_checked_evidence_and_qualifying_costs():
    case, threads = recorded(3)
    a = agent()
    move = a.decide(case, threads)
    terms = [s["terms"] for s in move["message"]["term_sheets"]]
    assert [(t["unit_price"], t["payment_days"], t["index_clause"]["deadband_pct"]) for t in terms] == [
        (5.07, 30, 5),
        (5.00, 14, 3),
        (5.10, 60, 5),
    ]
    assert [total_cost(case, t) for t in terms] == [244082.98, 243688.67, 243915.66]
    assert all(a.acceptance_floor.allows(total_cost(case, t), t) for t in terms)
    assert score(295173.23, total_cost(case, terms[1]), 222651.27) * 100 == pytest.approx(70.99168307)
    option_b = next(line for line in move["message"]["body"].splitlines() if line.startswith("Option B:"))
    assert "5.00/kg" in option_b and "14 days" in option_b and "3% deadband" in option_b
    assert "Option B pairs earlier payment with a narrower adjustment buffer" in move["message"]["body"]
    assert a.decisions[-1]["execution_evidence"]["status"] == "included"
    assert a.decisions[-1]["numeric_checks"][0]["calculation_id"] == "p18:ppi_recent_change_pct"


@pytest.mark.parametrize("price", [5.00, 5.01])
def test_synthetic_binding_trade_is_accepted_unchanged_at_both_qualifying_cent_prices(price):
    case, threads = recorded(7)
    sheet = threads[SID][-1]["term_sheets"][0]
    sheet["terms"] = {
        **copy.deepcopy(POLICY["early_package_alternatives"]["packages"][1]),
        "unit_price": price,
        "contract_months": 36,
    }
    before = copy.deepcopy(threads)
    a = agent()
    assert a.decide(case, threads)["message"]["action"] == {"type": "accept", "term_sheet_id": sheet["term_sheet_id"]}
    assert threads == before
    assert a.decisions[-1]["source"] == "policy:accept"


@pytest.mark.parametrize("price", [5.02, 5.10])
def test_price_in_another_clause_cannot_be_accepted_under_the_three_percent_trade(price):
    case, threads = recorded(7)
    terms = {
        **copy.deepcopy(POLICY["early_package_alternatives"]["packages"][1]),
        "unit_price": price,
        "contract_months": 36,
    }
    threads[SID][-1]["term_sheets"][0]["terms"] = terms
    a = agent()
    assert total_cost(case, terms) > 244400.60
    with pytest.raises(NegotiationBlocked, match="FINAL_OFFER_OUTSIDE_LIMITS"):
        a.decide(case, threads)
    assert not a.audit["model_requests"]
