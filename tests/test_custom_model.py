import copy
import json

import pytest
from test_response_value import fixture

from aeiou.custom_model import (
    VERSION,
    CustomResponseModel,
    fit,
    model_status,
    portable,
    predict_one,
    register,
    split_sessions,
)
from aeiou.custom_model_data import SCHEMA, extract, live_features, trace_threads


def recorded():
    run, case, seller, buyer, reply = fixture()
    case["case_id"] = "case:P18"
    run["created_at"] = "2026-10-24T12:00:00Z"
    return run, case, seller, buyer, reply


def test_custom_training_and_live_vectors_are_identical_without_future_data():
    run, case, seller, buyer, reply = recorded()
    rows, skipped = extract(run)
    live = live_features(case, "sup:A", [seller], buyer)
    assert not skipped and live["v"] == rows[0]["v"]
    assert len(live["v"]) == len(SCHEMA)
    changed = copy.deepcopy(run)
    changed["result"] = {"score": 1, "private_supplier_cost": 999999}
    changed["threads"]["sup:A"][-1]["body"] = "FUTURE SECRET OUTCOME"
    changed["threads"]["sup:A"][-1]["term_sheets"][0]["terms"]["unit_price"] = 110
    modified, _ = extract(changed)
    assert modified[0]["v"] == rows[0]["v"]
    assert modified[0]["y"]["quoted_cost_gain"] != rows[0]["y"]["quoted_cost_gain"]
    assert all(v is None for v in live["y"].values())


def test_final_counters_learn_observed_withdrawal_without_imitation_or_fake_gain():
    run, case, seller, buyer, reply = recorded()
    seller["status"] = "final_offer"
    reply["status"] = "walked_away"
    rows, skipped = extract(run)
    assert not skipped and len(rows) == 1
    assert rows[0]["v"][-2:] == [1, 0]
    assert rows[0]["y"]["supplier_exit"] == 1
    assert rows[0]["y"]["quoted_cost_gain"] is None
    assert rows[0]["y"]["package_matched"] == 0
    assert live_features(case, "sup:A", [seller], buyer)["v"] == rows[0]["v"]
    seller["status"] = "no_bid"
    assert extract(run)[1]["already_closed"] == 1


def test_trace_reconstruction_drops_hidden_reasons_and_keeps_public_moves():
    snapshot = {
        "result": {
            "private": "TOP SECRET",
            "trace": [
                {
                    "actor": "sup:A",
                    "move": {"offer_id": "one", "terms": {"unit_price": 100}, "status": "open", "text": "Public offer"},
                    "reasons": ["TOP SECRET"],
                },
                {
                    "actor": "buyer",
                    "move": {
                        "supplier_id": "sup:A",
                        "type": "message",
                        "body": "Counter",
                        "term_sheets": [{"terms": {"unit_price": 95}}],
                        "interpreted": {"secret": 123},
                    },
                },
            ],
        }
    }
    result = trace_threads(snapshot, {})
    assert len(result["sup:A"]) == 2
    assert "TOP SECRET" not in json.dumps(result) and "interpreted" not in json.dumps(result)
    assert result["sup:A"][0]["term_sheets"][0]["terms"]["unit_price"] == 100


def test_whole_sessions_stay_disjoint_and_chronological():
    runs = {}
    rows = []
    for cid in ("case:P10", "case:P18"):
        for i in range(20):
            sid = f"{cid}:{i}"
            runs[sid] = {
                "case": {"case_id": cid},
                "created_at": f"2026-10-24T12:{i:02}:00Z",
                "threads": {"s": [{"from": "buyer", "body": str(i)}]},
            }
            rows.extend([{"case_id": cid, "session_id": sid}] * 2)
    splits, audit = split_sessions(rows, runs)
    groups = [{r["session_id"] for r in splits[name]} for name in ("train", "validation", "test")]
    assert all(not a & b for i, a in enumerate(groups) for b in groups[i + 1 :])
    assert max(runs[s]["created_at"] for s in groups[0]) < min(runs[s]["created_at"] for s in groups[1])
    assert max(runs[s]["created_at"] for s in groups[1]) < min(runs[s]["created_at"] for s in groups[2])
    runs["case:P18:19"]["threads"] = copy.deepcopy(runs["case:P18:0"]["threads"])
    splits, audit = split_sessions(rows, runs)
    assert {"case:P18:0", "case:P18:19"} <= {r["session_id"] for r in splits["refit_only"]}


def test_portable_inference_matches_training_library():
    pytest.importorskip("sklearn")
    import numpy as np

    rows = []
    for i in range(60):
        rows.append(
            {
                "v": [float(i % 7) / 7] + [0.0] * (len(SCHEMA) - 1),
                "group": "case:P18",
                "case_id": "case:P18",
                "session_id": f"s{i // 3}",
                "y": {"supplier_exit": int(i % 7 > 3)},
                "past_targets": {"supplier_exit": {"sum": 0, "count": 0}},
            }
        )
    trained = fit(rows, "supplier_exit")
    artifact = portable(trained, {"method": "forest", "strength": 1.0})
    restored = json.loads(json.dumps(artifact))
    x = np.asarray([r["v"] for r in rows])
    for method in ("forest", "ridge"):
        expected = trained[method].predict(x if method == "forest" else (x - trained["center"]) / trained["scale"])
        np.testing.assert_allclose(
            [predict_one(restored, r, method, 1.0) for r in rows], np.clip(expected, 0, 1), atol=1e-12
        )


def test_registered_artifact_detects_changed_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    path = tmp_path / "model.json"
    artifact = {"version": VERSION, "schema": list(SCHEMA), "training_examples": 1, "training_sessions": 1}
    path.write_text(json.dumps(artifact))
    register(path)
    assert model_status()["available"]
    artifact["training_examples"] = 2
    path.write_text(json.dumps(artifact))
    assert not model_status()["available"]
    with pytest.raises(ValueError, match="changed"):
        CustomResponseModel()


def test_custom_buyer_preserves_saved_p18_acceptance_and_case_scope(tmp_path, monkeypatch):
    from test_negotiation_data_pipeline import NeverCall, scenario

    from aeiou.config import ROOT
    from aeiou.custom_agent import CustomBuyer
    from aeiou.research import package

    class Forecast:
        sha256 = "test-model"
        artifact = {"version": VERSION}

        def forecast(self, *args):
            return {"predictions": {"quoted_cost_gain": 1, "supplier_exit": 0}, "status": "advisory_only"}

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    policy = json.loads((ROOT / "config/p18-evidence-menu.json").read_text())
    (tmp_path / "negotiation-policies.json").write_text(json.dumps(policy))
    buyer = CustomBuyer(package(), NeverCall(), model=Forecast())
    assert buyer.spec.response_support_mode == "advisory"
    case, sid, threads = scenario(5.05, "open")
    threads[sid][-1]["term_sheets"][0]["terms"] = {
        **policy["case:P18"]["early_package_alternatives"]["packages"][0],
        "contract_months": 36,
    }
    move = buyer.decide(case, threads)
    assert move["message"]["action"]["type"] == "accept"
    assert buyer.execution_policy == policy["case:P18"]
    assert buyer.audit["custom_forecasts"][-1]["forecasts"][sid] == []
    assert buyer.decisions[-1]["learned_response_support"]["suppliers"][sid]["status"] == "skipped"
    assert buyer.decisions[-1]["source"] == "policy:accept"
    buyer.prepare(package().cases["case:P10"])
    assert buyer.execution_policy is None and buyer.acceptance_floor is None
    with pytest.raises(ValueError, match="P10 and case:P18 only"):
        buyer.prepare({"case_id": "case:S01"})


def test_custom_launch_rejects_wrong_case_and_missing_artifact_before_dispatch(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aeiou.service import app

    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    client = TestClient(app)
    request = {"mode": "hosted", "agent": "custom-p10-p18", "case_id": "case:S01"}
    assert client.post("/api/play", json=request).status_code == 422
    request["case_id"] = "case:P18"
    response = client.post("/api/play", json=request)
    assert response.status_code == 409 and "Train and register" in response.json()["detail"]
