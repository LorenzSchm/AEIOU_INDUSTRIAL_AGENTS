import copy
import json

from aeiou.config import data_root
from aeiou.memory_opening import memory_opening


def example():
    case = json.loads((data_root() / "cases/practice/P07.json").read_text())
    case.update(case_id="case:unseen-label", suppliers=["sup:new"])
    terms = {"unit_price": 5.44, "payment_days": 90, "contract_months": 24}
    memory = {
        "id": "prior-deal",
        "case": copy.deepcopy(case),
        "result": {"deal": True, "must_haves_ok": True, "supplier_id": "sup:new", "terms": terms},
    }
    threads = {
        "sup:new": [{"from": "seller", "status": "open", "term_sheets": [{"terms": {**terms, "unit_price": 5.74}}]}]
    }
    return case, threads, memory


def test_retrieves_terms_not_score_and_preserves_originals():
    c, t, m = example()
    original = copy.deepcopy((c, t, m))
    m["case"]["case_id"] = "other-label"
    result = memory_opening(c, t, m, {}, 0.002)
    assert result["terms"]["unit_price"] == 5.43
    assert result["terms"]["payment_days"] == 90
    assert result["reference_run_id"] == "prior-deal"
    assert c == original[0] and t == original[1] and m["result"] == original[2]["result"]


def test_changed_context_or_non_deal_does_not_transfer():
    for field, value in [("need_per_year", 30001), ("interest_rate", 0.09)]:
        c, t, m = example()
        c["buyer"][field] = value
        assert memory_opening(c, t, m, {}, 0.002) is None
    c, t, m = example()
    m["result"]["deal"] = False
    assert memory_opening(c, t, m, {}, 0.002) is None


def test_only_opening_and_no_terminal_or_illegal_terms():
    c, t, m = example()
    t["sup:new"][-1]["status"] = "final_offer"
    assert memory_opening(c, t, m, {}, 0.002) is None
    c, t, m = example()
    t["sup:new"].append({"from": "buyer", "term_sheets": []})
    assert memory_opening(c, t, m, {}, 0.002) is None
    c, t, m = example()
    m["result"]["terms"]["payment_days"] = 999
    assert memory_opening(c, t, m, {}, 0.002) is None


def test_rejects_more_expensive_memory_when_live_quote_is_better():
    c, t, m = example()
    t["sup:new"][-1]["term_sheets"][0]["terms"]["unit_price"] = 5.2
    assert memory_opening(c, t, m, {}, 0.002) is None


def test_respects_multi_supplier_target_and_does_not_copy_unnegotiable_fields():
    c, t, m = example()
    c["suppliers"].append("sup:other")
    m["case"]["suppliers"].append("sup:other")
    t["sup:other"] = copy.deepcopy(t["sup:new"])
    m["result"]["terms"]["unpriced_side_deal"] = "ignore"
    r = memory_opening(c, t, m, {}, 0.002)
    assert r["supplier_id"] == "sup:new"
    assert set(r["terms"]) == set(c["negotiable"])


def test_confirmed_live_supplier_lead_time_is_preserved_without_changing_cost_case():
    c = json.loads((data_root() / "cases/practice/P10.json").read_text())
    sid = c["suppliers"][0]
    terms = {"unit_price": 31.3, "volume_per_year": 23400, "contract_months": 36, "lead_time_days": 54}
    m = {"case": copy.deepcopy(c), "result": {"deal": True, "must_haves_ok": True, "supplier_id": sid, "terms": terms}}
    t = {sid: [{"from": "seller", "status": "open", "term_sheets": [{"terms": {**terms, "unit_price": 32.2}}]}]}
    original = copy.deepcopy(c)
    out = memory_opening(c, t, m, {}, 0.002)
    assert out["terms"]["lead_time_days"] == 54
    assert out["observed_supplier_terms"] == {"lead_time_days": 54}
    assert c == original and 54 not in c["term_options"]["lead_time_days"]
    t[sid][0]["term_sheets"][0]["terms"]["lead_time_days"] = 56
    assert memory_opening(c, t, m, {}, 0.002) is None
    t[sid][0]["term_sheets"][0]["terms"]["lead_time_days"] = 54
    c["buyer"]["must_haves"].append({"field": "lead_time_days", "op": "<=", "value": 42})
    m["case"] = copy.deepcopy(c)
    assert memory_opening(c, t, m, {}, 0.002) is None
