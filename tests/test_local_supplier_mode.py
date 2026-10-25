import copy
import json

import pytest
from arena_kit.sim import Sim
from fastapi.testclient import TestClient

from aeiou.hosted_replay import public_setup
from aeiou.language_sim import LanguageSim
from aeiou.prompt_comparison import lab_play
from aeiou.prompt_protocol import prompt_specs
from aeiou.service import app
from aeiou.simulation import OPPONENTS, local_cases


def test_scripted_supplier_cannot_be_launched(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    assert "scripted" not in OPPONENTS
    response = TestClient(app).post(
        "/api/play",
        json={
            "case_id": "case:S01",
            "mode": "local",
            "opponent": "scripted",
        },
    )
    assert response.status_code == 422
    with pytest.raises(ValueError, match="Only model-driven"):
        lab_play("case:S01", "scripted", 1, prompt_specs()["original"], "test", supplier_kind="scripted")
    assert not (tmp_path / "runs").exists()


def test_recorded_opening_terms_ids_and_private_boundary(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    case = copy.deepcopy(local_cases()["case:S01"])
    case["case_id"] = "case:PTEST"
    case["buyer"]["private_marker"] = "BUYER-ONLY-ALTERNATIVE"
    opening = Sim({case["case_id"]: case}, no_clock=True).open("fixture", case["case_id"])
    setup = public_setup({"id": "recorded", "mode": "hosted", "case": case, "threads": opening["threads"]})
    (tmp_path / "public-setups.json").write_text(json.dumps({case["case_id"]: setup}))
    sim = LanguageSim(local_cases(), "relationship", 1, {}, None)
    actual = sim.open("test", case["case_id"])
    assert actual["case"] == case
    assert actual["threads"] == opening["threads"]
    session = sim.sessions[actual["session_id"]]
    sid, th = next(iter(session["threads"].items()))
    payload = sim.private_input(session, th, {}, [])
    assert "BUYER-ONLY-ALTERNATIVE" not in json.dumps(payload)
    offer = actual["threads"][sid][-1]["term_sheets"][0]
    result = sim.message(
        "test",
        actual["session_id"],
        sid,
        {
            "action": {"type": "accept", "term_sheet_id": offer["term_sheet_id"]},
        },
    )["result"]
    assert result["deal"]
    assert result["terms"]["unit_price"] == offer["terms"]["unit_price"]


def test_model_controls_withdrawal_after_early_final(monkeypatch, tmp_path):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))

    class Hold:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            package = max(payload["package_catalog"], key=lambda p: p["terms"]["unit_price"])
            return json.dumps(
                {
                    "status": "final_offer",
                    "body": "Our offer remains open.",
                    "package_ids": [package["id"]],
                    "repeat_unchanged": False,
                }
            )

    backend = Hold()
    sim = LanguageSim(local_cases(), "hardliner", 1, {}, backend)
    opened = sim.open("test", "case:S01")
    sid = next(iter(opened["threads"]))
    for _ in range(6):
        reply = sim.message("test", opened["session_id"], sid, {"body": "Please discuss terms."})
        assert reply["status"] == "final_offer"
        assert "result" not in reply
    assert backend.calls == 6
