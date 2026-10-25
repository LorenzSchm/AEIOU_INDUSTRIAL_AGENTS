import copy
import sys

import pytest

from aeiou import cli
from aeiou.hosted_replay import RecordedTable, ReplayDivergenceError, public_setup, verify_replay


def recording():
    return {
        "id": "recorded1",
        "mode": "hosted",
        "status": "completed",
        "case_id": "case:P01",
        "case": {"case_id": "case:P01", "round_limit": 10, "buyer": {"need_per_year": 100}},
        "threads": {
            "sup:1": [
                {
                    "from": "supplier",
                    "status": "open",
                    "term_sheets": [{"term_sheet_id": "live:1", "terms": {"unit_price": 12}}],
                },
                {"from": "buyer", "body": "Please confirm.", "term_sheets": []},
                {
                    "from": "supplier",
                    "status": "final_offer",
                    "term_sheets": [{"term_sheet_id": "live:2", "terms": {"unit_price": 11}}],
                },
                {"from": "buyer", "action": {"type": "accept", "term_sheet_id": "live:2"}},
                {"result": {"deal": True, "score": 0.5, "c_best": 900}},
            ]
        },
        "result": {"deal": True, "score": 0.5, "c_best": 900},
        "private_post_run_analysis": {"supplier_floor": 9},
    }


def test_public_fixture_preserves_opening_without_future_quotes_or_result():
    run = recording()
    fixture = public_setup(run)
    assert fixture["case"] == run["case"]
    assert fixture["threads"]["sup:1"] == run["threads"]["sup:1"][:1]
    assert "result" not in fixture and "private_post_run_analysis" not in fixture
    fixture["case"]["buyer"]["need_per_year"] = 0
    assert run["case"]["buyer"]["need_per_year"] == 100


def test_local_synthetic_run_cannot_be_labelled_a_hosted_fixture():
    run = recording()
    run["mode"] = "local"
    with pytest.raises(ValueError, match="hosted case"):
        public_setup(run)


def test_uncontacted_rfq_thread_stays_at_its_recorded_opening():
    run = recording()
    run["threads"]["sup:2"] = copy.deepcopy(run["threads"]["sup:1"][:1])
    assert verify_replay(run)["exchanges"] == 2


def test_rfq_replay_requires_and_preserves_global_supplier_order():
    run = recording()
    run["threads"]["sup:2"] = copy.deepcopy(run["threads"]["sup:1"])
    with pytest.raises(ValueError, match="global decision order"):
        RecordedTable(run)
    run["decisions"] = [{"supplier_id": sid} for sid in ["sup:1", "sup:2", "sup:1", "sup:2"]]
    table = RecordedTable(run)
    table.open("case:P01")
    with pytest.raises(ReplayDivergenceError, match="Supplier order"):
        table.send("sup:2", {"body": "Please confirm.", "term_sheets": []})
    assert verify_replay(run)["exchanges"] == 4


def test_changed_wording_terms_and_ids_never_receive_a_recorded_counterfactual():
    run = recording()
    table = RecordedTable(run)
    table.open("case:P01")
    request = {"body": "Please confirm.", "term_sheets": []}
    with pytest.raises(ReplayDivergenceError, match="differs"):
        table.send("sup:1", {**request, "body": "Could you discount?"})
    with pytest.raises(ReplayDivergenceError, match="unavailable"):
        table.result()
    assert table.send("sup:1", request) == run["threads"]["sup:1"][2]
    with pytest.raises(ReplayDivergenceError, match="differs"):
        table.close("sup:1", {"action": {"type": "accept", "term_sheet_id": "invented"}})
    assert table.close("sup:1", {"action": {"type": "accept", "term_sheet_id": "live:2"}}) == run["result"]
    assert table.result() == run["result"]


def test_replay_copies_inputs_and_replies_and_rejects_incomplete_recordings():
    run = recording()
    before = copy.deepcopy(run)
    table = RecordedTable(run)
    _, opening = table.open("case:P01")
    opening["sup:1"].clear()
    reply = table.send("sup:1", {"body": "Please confirm.", "term_sheets": []})
    reply["term_sheets"].clear()
    assert run == before
    run["threads"]["sup:1"].pop()
    with pytest.raises(ValueError, match="complete buyer/reply"):
        RecordedTable(run)


def test_verify_cli_uses_zero_network_and_model_calls(monkeypatch, capsys):
    from aeiou import store

    monkeypatch.setattr(sys, "argv", ["aeiou", "verify-replay", "recorded1"])
    monkeypatch.setattr(store, "get_run", lambda run_id: recording())
    monkeypatch.setattr(cli, "ArenaClient", lambda *a, **kw: pytest.fail("Replay must stay offline"))
    cli.main()
    assert '"arena_sessions_used": 0' in capsys.readouterr().out
    report = verify_replay(recording())
    assert report["model_calls"] == 0 and report["exchanges"] == 2
