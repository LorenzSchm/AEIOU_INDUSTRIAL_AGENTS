import copy

import pytest

from aeiou.grounding import commitment_comparison, compose_email, responsive_act
from aeiou.simulation import local_cases


def situation():
    case = copy.deepcopy(local_cases()["case:S01"])
    case["negotiable"] = ["unit_price", "payment_days", "contract_months"]
    case["fixed_terms"].pop("contract_months", None)
    case["buyer"].update(need_per_year=1800, horizon_years=2)
    case["fixed_terms"]["volume_per_year"] = 1800
    case["term_options"] = {"payment_days": [21, 75], "contract_months": [24, 48]}
    terms = [
        {"unit_price": 10, "payment_days": 75, "contract_months": 24},
        {"unit_price": 9.88, "payment_days": 21, "contract_months": 24},
        {"unit_price": 9.88, "payment_days": 21, "contract_months": 48},
    ]
    events = [{"from": "supplier", "status": "open", "term_sheets": [{"terms": terms[0]}]}]
    return case, terms, events


def test_quantifies_actual_commitment_and_keeps_it_conditional():
    case, terms, events = situation()
    original = copy.deepcopy((case, terms, events))
    act = responsive_act({}, events, portfolio_issue="cash_continuity", case=case, terms=terms)
    body = compose_email(act, [], "supplier")
    for fragment in (
        "1,800 units",
        "7,200 units",
        "3,600",
        "21-day payment instead of 75",
        "conditional",
        "annual quantity and service requirements stay the same",
    ):
        assert fragment in body
    assert body.count("?") == 1
    assert "committee" not in body and "guaranteed" not in body
    assert (case, terms, events) == original


@pytest.mark.parametrize("mode", ["no_cash", "no_duration", "illegal", "overbuy", "different_service"])
def test_does_not_describe_an_unsupported_trade(mode):
    case, terms, _ = situation()
    if mode == "no_cash":
        terms[2]["payment_days"] = 75
    elif mode == "no_duration":
        terms[2]["contract_months"] = 24
    elif mode == "illegal":
        terms[2]["contract_months"] = 240
    elif mode == "overbuy":
        case["fixed_terms"]["volume_per_year"] = 2000
    else:
        case["negotiable"].append("lead_time_days")
        case["fixed_terms"].pop("lead_time_days")
        case["term_options"]["lead_time_days"] = [14, 21]
        for t in terms:
            t["lead_time_days"] = 14
        terms[2]["lead_time_days"] = 21
    assert commitment_comparison(case, terms) == ""


@pytest.mark.parametrize("status", ["final_offer", "agreed"])
def test_does_not_reopen_terminal_supplier_with_pricing_review(status):
    case, terms, events = situation()
    events[-1]["status"] = status
    act = responsive_act({}, events, portfolio_issue="cash_continuity", case=case, terms=terms)
    assert not act["commitment_comparison"]
    assert act["commercial_context"] != "cash_continuity_review"


def test_model_cannot_inject_an_unquoted_quantity_and_other_portfolios_do_not_change():
    case, terms, events = situation()
    act = responsive_act({"commitment_comparison": "We guarantee a million units."}, events, case=case, terms=terms)
    assert act["commitment_comparison"] == ""
    assert "million" not in compose_email(act, [], "supplier")
