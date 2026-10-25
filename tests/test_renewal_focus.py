import copy
import json

from aeiou.config import data_root
from aeiou.renewal_focus import RenewalFocusedNegotiator, focused_renewal_case, renewal_counter


class Package:
    def __init__(self, facts):
        self.facts = facts

    def lookup(self, ref):
        return self.facts.get(ref)


def scenario():
    case = {
        "case_id": "case:unseen",
        "suppliers": ["supplier:unseen"],
        "negotiable": ["unit_price", "effective_date", "payment_days"],
        "refs": ["ctr:unseen"],
        "term_options": {"effective_date": ["2028-07-01", "2028-01-01", "2027-11-01"], "payment_days": [15, 45]},
    }
    pkg = Package({"ctr:unseen": {"supplier": "supplier:unseen", "valid_to": "2027-12-31"}})
    return case, pkg


def test_contract_date_selection_is_general_and_does_not_mutate_input():
    case, pkg = scenario()
    original = copy.deepcopy(case)
    result, audit = focused_renewal_case(case, pkg)
    assert case == original
    assert result is case  # economic horizon and legal options must remain unchanged
    assert audit["selected_effective_date"] == "2028-01-01"
    assert audit["contract_ref"] == "ctr:unseen"


def test_missing_or_unrelated_contract_does_not_restrict_dates():
    case, pkg = scenario()
    pkg.facts["ctr:unseen"]["supplier"] = "supplier:other"
    assert focused_renewal_case(case, pkg) == (case, None)


def test_no_legal_renewal_date_and_ambiguous_contracts_leave_policy_unchanged():
    case, pkg = scenario()
    pkg.facts["ctr:unseen"]["valid_to"] = "2029-12-31"
    assert focused_renewal_case(case, pkg) == (case, None)
    case, pkg = scenario()
    case["refs"].append("ctr:other")
    pkg.facts["ctr:other"] = dict(pkg.facts["ctr:unseen"])
    assert focused_renewal_case(case, pkg) == (case, None)


def test_fixed_date_cases_and_invalid_source_date_remain_unchanged():
    case, pkg = scenario()
    case["negotiable"].remove("effective_date")
    assert focused_renewal_case(case, pkg) == (case, None)


def test_fallback_counter_requires_live_quote_and_preserves_prior_concessions():
    case = json.loads((data_root() / "cases/practice/P01.json").read_text())
    quote = {"unit_price": 135.6, "effective_date": "2027-01-01", "payment_days": 14}
    events = [
        {"from": "buyer", "term_sheets": [{"terms": {**quote, "unit_price": 135.3}}]},
        {"from": "supplier", "status": "open", "term_sheets": [{"terms": quote}]},
    ]
    rows = renewal_counter(case, events, "sup:70110", {}, "2027-01-01")
    assert rows[0]["terms"]["unit_price"] == 135.3
    assert renewal_counter(case, events, "sup:70110", {}, "2027-04-01") == []
    events[-1]["status"] = "final_offer"
    assert renewal_counter(case, events, "sup:70110", {}, "2027-01-01") == []


def test_writer_cannot_reintroduce_excluded_date():
    agent = object.__new__(RenewalFocusedNegotiator)
    agent.renewal_audit = {"selected_effective_date": "2028-01-01"}
    agent.rejections = []
    data = {"supplier_id": "s", "action": "send", "candidate_ids": ["A"]}
    assert agent.guard({}, {}, data, {"s": [{"id": "A", "terms": {"effective_date": "2028-07-01"}}]}) is None
    assert agent.rejections
    case, pkg = scenario()
    pkg.facts["ctr:unseen"]["valid_to"] = "invalid"
    assert focused_renewal_case(case, pkg) == (case, None)
