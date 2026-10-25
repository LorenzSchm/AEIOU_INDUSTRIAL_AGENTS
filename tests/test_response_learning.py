import copy
import json

import pytest

from aeiou.response_learning import FEATURES, _date_delta, assign_groups, extract_run, features, load_dataset
from aeiou.response_training import evaluate, fit, predict


def example():
    case = {
        "case_id": "case:A",
        "round_limit": 10,
        "suppliers": ["sup:A"],
        "negotiable": ["unit_price", "payment_days"],
        "fixed_terms": {"contract_months": 12, "volume_per_year": 1000, "lead_time_days": 30, "incoterm": "FCA"},
        "buyer": {
            "need_per_year": 1000,
            "horizon_years": 1,
            "reference_payment_days": 30,
            "need_lead_time_days": 30,
            "fallback_unit_price": 120,
            "interest_rate": 0.08,
            "freight_eur_per_unit": {"FCA": 0},
            "must_haves": [],
        },
    }
    seller = {
        "from": "seller",
        "status": "open",
        "body": "Please consider our quotation.",
        "term_sheets": [{"terms": {"unit_price": 100, "payment_days": 30}}],
    }
    outgoing = {
        "from": "buyer",
        "body": "Thank you",
        "term_sheets": [
            {"terms": {"unit_price": 95, "payment_days": 30}},
            {"terms": {"unit_price": 98, "payment_days": 60}},
        ],
    }
    response = {"from": "seller", "status": "open", "term_sheets": [copy.deepcopy(outgoing["term_sheets"][1])]}
    run = {"mode": "hosted", "session_id": "s1", "case": case, "threads": {"sup:A": [seller, outgoing, response]}}
    return run, case, seller, outgoing, response


def test_menu_labels_do_not_turn_unchosen_packages_into_rejections():
    run, *_ = example()
    rows, excluded = extract_run(run)
    assert len(rows) == 1
    assert rows[0]["y"] == {"package_matched": 1, "direct_cost_improved": 1}
    assert not excluded


def test_absent_date_serialized_by_quote_parser_is_not_an_invalid_transition():
    assert _date_delta("none", "none") == 0
    assert _date_delta("2027-01-01", "none") == 0
    assert _date_delta("2027-01-02", "2027-01-01") == pytest.approx(1 / 365)


def test_features_cannot_see_final_score_future_text_or_case_identity():
    run, case, seller, outgoing, _ = example()
    original = features(case, [seller], outgoing)
    modified = copy.deepcopy(run)
    modified.update(result={"score": 1, "cost_breakdown": {"relationship": 99999}})
    modified["case"]["case_id"] = "case:NEVER_SEEN"
    modified["threads"]["sup:A"][-1]["body"] = "Private full cost is 500 and we reject you"
    rows, _ = extract_run(modified)
    assert rows[0]["x"] == original
    assert len(original) == len(FEATURES)


@pytest.mark.parametrize("status", ["final_offer", "final", "agreed", "no_bid"])
def test_protocol_errors_are_excluded_from_training(status):
    run, *_ = example()
    run["threads"]["sup:A"][0]["status"] = status
    rows, audit = extract_run(run)
    assert not rows
    assert audit["proposal_after_terminal"] == 1


def test_transport_recovery_is_not_a_supplier_rejection():
    run, *_ = example()
    run["threads"]["sup:A"][-1]["from"] = "arena"
    rows, audit = extract_run(run)
    assert not rows and audit["transport_or_unpaired_response"] == 1


def test_withdrawal_with_stale_sheet_is_not_acceptance():
    run, *_ = example()
    run["threads"]["sup:A"][-1]["status"] = "walked_away"
    rows, _ = extract_run(run)
    assert rows[0]["y"] == {"package_matched": 0, "direct_cost_improved": 0}


def test_session_deduplication_and_local_exclusion(tmp_path):
    run, *_ = example()
    (tmp_path / "first.json").write_text(json.dumps(run))
    (tmp_path / "duplicate.json").write_text(json.dumps(run))
    (tmp_path / "simulation.json").write_text(json.dumps({**run, "mode": "local", "session_id": "local"}))
    rows, audit = load_dataset(tmp_path)
    assert len(rows) == audit["hosted_sessions"] == 1
    assert audit["excluded"]["duplicate_session"] == 1
    assert audit["excluded"]["not_hosted_or_missing_case"] == 1


def test_shared_suppliers_connect_cases_transitively():
    rows = [dict(case_id=c, supplier_id=s) for c, s in [("A", "x"), ("B", "x"), ("B", "y"), ("C", "y"), ("D", "z")]]
    assign_groups(rows)
    assert len({r["group"] for r in rows[:4]}) == 1
    assert rows[-1]["group"] != rows[0]["group"]


def synthetic_rows():
    return [
        {
            "x": [float(i % 2)] + [0.0] * (len(FEATURES) - 1),
            "y": {"package_matched": i % 2, "direct_cost_improved": i % 2},
            "session_id": f"s{g}-{i}",
            "case_id": f"c{g}",
            "supplier_id": f"p{g}",
            "group": f"g{g}",
        }
        for g in range(3)
        for i in range(8)
    ]


def test_fit_learns_signal_and_serializes_for_identical_predictions():
    rows = synthetic_rows()
    model = fit(rows, "package_matched")
    assert predict(model, rows[0]["x"]) < 0.2
    assert predict(model, rows[1]["x"]) > 0.8
    assert predict(json.loads(json.dumps(model)), rows[1]["x"]) == predict(model, rows[1]["x"])
    with pytest.raises(ValueError):
        predict(model, [])


def test_evaluation_is_grouped_and_small_samples_cannot_pass_gate():
    rows = synthetic_rows()
    report, predictions = evaluate(rows)
    assert len(report["folds"]) == 3
    assert len(predictions) == len(rows)
    assert all(f["train_examples"] == 16 and f["test_examples"] == 8 for f in report["folds"])
    assert not any(v["passes_predictive_gate"] for v in report["targets"].values())
    assert all(
        v["methods"]["learned"]["macro_brier"] < v["methods"]["base_rate"]["macro_brier"]
        for v in report["targets"].values()
    )
