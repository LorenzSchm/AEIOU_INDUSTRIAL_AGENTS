import copy
import json

import pytest

from aeiou.closing_probe import ClosingProbeMixin, closing_probe
from aeiou.config import data_root


def setup():
    case = json.loads((data_root() / "cases/practice/P07.json").read_text())
    case.update(case_id="case:unseen", suppliers=["sup:new"])
    quote = {"unit_price": 5.5, "payment_days": 90, "contract_months": 24}
    events = [
        {
            "from": "seller",
            "status": "open",
            "term_sheets": [{"term_sheet_id": "q0", "terms": {**quote, "unit_price": 6}}],
        },
        {"from": "buyer", "term_sheets": [{"terms": {**quote, "unit_price": 5.3}}]},
        {"from": "seller", "status": "open", "term_sheets": [{"term_sheet_id": "q1", "terms": quote}]},
    ]
    return case, events


def test_small_counter_preserves_structure_and_inputs():
    c, e = setup()
    original = copy.deepcopy((c, e))
    p = closing_probe(c, e, "q1", None, 0.005)
    assert p["terms"] == {"unit_price": 5.47, "payment_days": 90, "contract_months": 24}
    assert p["direct_saving"] > 0
    assert p["modeled_cost_delta"] < 0
    assert (c, e) == original


@pytest.mark.parametrize("status", ["final_offer", "agreed", "walked_away"])
def test_never_reopens_terminal_status(status):
    c, e = setup()
    e[-1]["status"] = status
    assert closing_probe(c, e, "q1", None, 0.005) is None


def test_stale_ids_explicit_close_and_deadline_are_respected():
    c, e = setup()
    assert closing_probe(c, e, "q0", None, 0.005) is None
    e[-1]["body"] = "This is our final offer."
    assert closing_probe(c, e, "q1", None, 0.005) is None
    e[-1]["body"] = ""
    c["round_limit"] = 2
    assert closing_probe(c, e, "q1", None, 0.005) is None


def test_never_retracts_or_repeats_buyer_concession():
    c, e = setup()
    e[1]["term_sheets"][0]["terms"]["unit_price"] = 5.49
    assert closing_probe(c, e, "q1", None, 0.005) is None


def test_mixin_uses_only_one_extra_turn_and_records_actual_action():
    c, e = setup()

    class Base:
        def __init__(self):
            self.intelligence = {}
            self.decisions = []

        def decide(self, case, threads):
            self.decisions.append({"action": "accept"})
            return {"supplier_id": "sup:new", "message": {"action": {"type": "accept", "term_sheet_id": "q1"}}}

    class Probe(ClosingProbeMixin, Base):
        pass

    a = Probe()
    assert a.decide(c, {"sup:new": e})["message"].get("term_sheets")
    assert a.decisions[-1]["action"] == "send"
    assert a.decide(c, {"sup:new": e})["message"]["action"]["type"] == "accept"
