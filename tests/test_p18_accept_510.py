"""The user's relaxed acceptance authorization must reach execution unchanged."""

import copy
import json

import pytest
from arena_kit.cost import score, total_cost

from aeiou.action_policy import NegotiationBlocked
from aeiou.config import ROOT
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import package

SID = "sup:70740"
POLICY = json.loads((ROOT / "config/p18-accept-510.json").read_text())["case:P18"]
STRATEGY = (ROOT / "prompts/p18-diagnostic-v25.txt").read_text()


class NoModel:
    def complete(self, *args):
        raise AssertionError("An authorized exact acceptance or final stop must not call the model")


def scenario():
    fixture = json.loads((ROOT / "tests/fixtures/p18-v19-verified-offer.json").read_text())
    return fixture["case"], {SID: fixture["threads"][SID][:7]}


def agent():
    return PromptLabAgent(package(), NoModel(), PromptSpec(
        "v25-offline", STRATEGY, assessment_enabled=True, execution_policy=copy.deepcopy(POLICY)))


@pytest.mark.parametrize("days", [14, 30, 45, 60, 90])
@pytest.mark.parametrize("deadband", [None, 0, 3, 5])
@pytest.mark.parametrize("duration", [24, 36])
def test_authorized_510_accepts_each_permitted_complete_package(days, deadband, duration):
    case, threads = scenario()
    sheet = threads[SID][-1]["term_sheets"][0]
    sheet["terms"] = {
        "unit_price": 5.10, "payment_days": days, "contract_months": duration,
        "index_clause": None if deadband is None else {
            "series": "AS-14404", "material_share": .45,
            "deadband_pct": deadband, "base_month": "2026-08"},
    }
    before = copy.deepcopy(threads)
    a = agent()
    assert a.decide(case, threads)["message"]["action"] == {
        "type": "accept", "term_sheet_id": sheet["term_sheet_id"]}
    assert a.decisions[-1]["source"] == "policy:accept"
    assert threads == before


def test_last_three_percent_counter_no_longer_triggers_the_old_seventy_point_floor():
    case, threads = scenario()
    terms = threads[SID][-1]["term_sheets"][0]["terms"]
    terms.update(unit_price=5.10, payment_days=14)
    terms["index_clause"]["deadband_pct"] = 3
    assert score(295173.23, total_cost(case, terms), 222651.27) * 100 == pytest.approx(64.27127728)
    a = agent()
    assert a.decide(case, threads)["message"]["action"]["type"] == "accept"


@pytest.mark.parametrize("price,duration", [(5.11, 36), (5.10, 12)])
def test_nominal_cap_and_minimum_coverage_remain_enforced(price, duration):
    case, threads = scenario()
    threads[SID][-1]["term_sheets"][0]["terms"].update(unit_price=price, contract_months=duration)
    with pytest.raises(NegotiationBlocked, match="FINAL_OFFER_OUTSIDE_LIMITS"):
        agent().decide(case, threads)
