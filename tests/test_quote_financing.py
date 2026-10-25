import copy

import pytest
from arena_kit.cost import closing_terms, total_cost

from aeiou.quote_financing import implied_rate, observed_financing_close
from aeiou.simulation import local_cases


def setup_quotes(rate=0.04):
    case = copy.deepcopy(local_cases()["case:S01"])
    case["buyer"].update(
        need_per_year=1000, category_goal="stabilise", relationship_eur_per_point=1000, sustainable_margin_pct=8
    )
    case["fixed_terms"]["volume_per_year"] = 1000
    model = {
        "full_cost": 95,
        "interest_rate": 0.12,
        "standard": closing_terms(case, {"unit_price": 100, "payment_days": 30}),
    }
    # Unrelated synthetic quotes: supplier value is constant at a supplied rate.
    terms = [
        closing_terms(case, {"unit_price": round(100 / (1 - rate * (d - 30) / 360), 2), "payment_days": d})
        for d in (30, 45, 60)
    ]

    def event(sheets):
        return {"from": "supplier", "status": "open", "term_sheets": [{"terms": t} for t in sheets]}

    events = [event([terms[0]]), event([terms[1]]), event(terms[1:])]
    rows = [
        {"terms": t, "cost": total_cost(case, t, supplier=model), "sheet_id": str(t["payment_days"])} for t in terms[1:]
    ]
    baseline = min(rows, key=lambda r: r["cost"])
    return case, events, model, rows, baseline


@pytest.mark.parametrize("rate", [0.02, 0.04, 0.06])
def test_consistent_distinct_quotes_can_improve_close_without_changing_model(rate):
    case, events, model, rows, baseline = setup_quotes(rate)
    original = copy.deepcopy((case, events, model, rows))
    choice = observed_financing_close(case, events, model, 5, rows, baseline)
    assert baseline["sheet_id"] == "45" and choice["sheet_id"] == "60"
    assert choice["closing_sensitivity"]["quoted_rate"] == pytest.approx(rate, abs=0.003)
    assert choice["closing_sensitivity"]["worst_cost_delta"] < 0
    assert choice["cost"] <= baseline["cost"] * 1.0025
    assert (case, events, model, rows) == original


@pytest.mark.parametrize("mode", ["single_menu", "repeated_menu", "buyer_claim", "inconsistent", "changed_scope"])
def test_insufficient_or_contradictory_evidence_keeps_baseline(mode):
    case, events, model, rows, baseline = setup_quotes()
    if mode == "single_menu":
        events = events[-1:]
    elif mode == "repeated_menu":
        events = [events[-1]] * 10
    elif mode == "buyer_claim":
        events[0]["from"] = "buyer"
    elif mode == "inconsistent":
        events[0]["term_sheets"][0]["terms"]["unit_price"] = 99
    else:
        events[0]["term_sheets"][0]["terms"]["volume_per_year"] = 1200
    assert observed_financing_close(case, events, model, 5, rows, baseline) is baseline


@pytest.mark.parametrize("mode", ["point_risk", "no_alternative", "routine", "no_model", "no_band", "malformed"])
def test_safety_limits_and_unsupported_cases_preserve_existing_close(mode):
    case, events, model, rows, baseline = setup_quotes()
    confidence = 5
    if mode == "point_risk":
        rows[1]["cost"] = baseline["cost"] * 1.003
    elif mode == "no_alternative":
        rows = [baseline]
    elif mode == "routine":
        case["buyer"]["category_goal"] = "leverage"
    elif mode == "no_model":
        model = None
    elif mode == "no_band":
        confidence = None
    else:
        events[0]["term_sheets"] = [{"terms": {"unit_price": "bad"}}]
    assert observed_financing_close(case, events, model, confidence, rows, baseline) is baseline


def test_different_contracts_do_not_imply_a_financing_rate():
    case, _, model, rows, _ = setup_quotes()
    first, second = [r["terms"] for r in rows]
    assert implied_rate(case, first, {**second, "contract_months": 24}, model) is None
