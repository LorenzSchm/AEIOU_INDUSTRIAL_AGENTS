import copy
import json

from test_custom_model import recorded
from test_response_support import constant_artifact

from aeiou.custom_model import CustomResponseModel
from aeiou.custom_model_data import build, live_features
from aeiou.general_context import CONTEXT_FEATURES, context_features, snapshot_context
from aeiou.general_training import GENERAL_VERSION, promotion_gate, relative_improvement, temporal_background
from aeiou.research import package, research


def test_dated_business_records_ignore_future_other_supplier_and_private_values():
    pkg = package()
    case = pkg.cases["case:P18"]
    intelligence = research(case, pkg)
    before = snapshot_context(case, "sup:70740", intelligence)
    changed = copy.deepcopy(intelligence)
    for table, date_field, future in (
        ("po_lines", "order_date", "2027-01-01"),
        ("scorecards", "period_end", "2027-01-01"),
        ("index_values", "month", "2027-01"),
        ("supplier_intel", "filed_fy", 2027),
        ("contracts", "valid_from", "2027-01-01"),
    ):
        row = {**changed["tables"][table][0], date_field: future}
        changed["tables"][table].append(row)
    changed["tables"]["po_lines"].append(
        {**changed["tables"]["po_lines"][-1], "supplier_id": "sup:OTHER", "order_date": "2026-09-01", "price": 999}
    )
    changed["private_supplier_floor"] = 0.01
    assert snapshot_context(case, "sup:70740", changed) == before
    assert before["source_count"] > 0
    assert before["values"]["otd_rate"] == 0.81
    assert before["cutoff"] == "2026-09-30"
    # A visible dated purchase-price correction does change the feature snapshot.
    next(r for r in changed["tables"]["po_lines"] if r["order_date"].startswith("2026-09"))["price"] += 1
    assert snapshot_context(case, "sup:70740", changed)["sha256"] != before["sha256"]


def test_general_history_cutoff_excludes_future_unknown_and_target_sessions():
    rows = [
        {"case_id": "case:P01", "created_at": "2026-10-23", "session_id": "past"},
        {"case_id": "case:P01", "created_at": "2026-10-25", "session_id": "future"},
        {"case_id": "case:P01", "created_at": None, "session_id": "undated"},
        {"case_id": "case:P18", "created_at": "2026-10-23", "session_id": "target"},
    ]
    assert [r["session_id"] for r in temporal_background(rows, "2026-10-24")] == ["past"]


def test_general_corpus_adds_other_hosted_cases_without_counting_simulations_or_copies(tmp_path):
    run, *_ = recorded()
    (tmp_path / "target.json").write_text(json.dumps(run))
    general = copy.deepcopy(run)
    general.update(session_id="general")
    general["case"]["case_id"] = "case:D01"
    for name in ("general", "copy"):
        (tmp_path / f"{name}.json").write_text(json.dumps(general))
    simulation = {**general, "session_id": "sim", "mode": "local"}
    (tmp_path / "simulation.json").write_text(json.dumps(simulation))
    _, focused, _ = build([tmp_path], {})
    _, broad, audit = build([tmp_path], {}, case_ids=None)
    assert len(focused) == 1 and len(broad) == 2
    assert audit["duplicate_log_copies"] == 1
    assert {r["case_id"] for r in broad} == {"case:P18", "case:D01"}


def test_promotion_rejects_regression_and_unsupported_probabilities_without_retuning():
    variants = {
        "focused": {"test": {"macro_mse": 0.01, "macro_mae": 0.05}},
        "context": {"test": {"macro_mse": 0.02, "macro_mae": 0.06}},
        "other": {"test": {"macro_mse": 0.001, "macro_mae": 0.01}},
    }
    # The attractive test-only alternative cannot replace the validation-selected model.
    assert promotion_gate("context", variants, True) == ("focused", "heldout_regression")
    assert promotion_gate("other", variants, False) == ("focused", "insufficient_target_support")
    assert promotion_gate("other", variants, True) == ("other", "passed_development_gate")
    assert relative_improvement(0, 0) == 0
    assert relative_improvement(0, 0.2) is None


def test_context_inference_uses_identical_vector_and_abstains_when_context_missing(tmp_path):
    _, case, seller, buyer, _ = recorded()
    row = live_features(case, "sup:A", [seller], buyer)
    context = snapshot_context(case, "sup:A", {})
    extra = context_features(case, [seller], buyer, context)
    assert len(extra) == len(CONTEXT_FEATURES)
    artifact = constant_artifact(case, "sup:A", row)
    artifact.update(
        version=GENERAL_VERSION,
        context_schema=list(CONTEXT_FEATURES),
        context_support={case["case_id"]: {"feature_min": extra, "feature_max": extra}},
    )
    artifact["models"]["quoted_cost_gain"]["context_enabled"] = True
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact))
    model = CustomResponseModel(path)
    assert model.forecast(case, "sup:A", [seller], buyer)["status"] == "abstained"
    result = model.forecast(case, "sup:A", [seller], buyer, context=context)
    assert result["predictions"] == {"quoted_cost_gain": 0.02}
    assert result["business_context"]["sha256"] == context["sha256"]
