from fastapi.testclient import TestClient

from aeiou.pipeline import summarize
from aeiou.service import app

client = TestClient(app)
CASES = {
    "case:X": {"title": "X", "round_limit": 10, "buyer": {"current_unit_price": 15.0, "fallback_unit_price": 18.0}}
}


def supplier(price, status="open"):
    return {"from": "sales@1", "status": status, "term_sheets": [{"terms": {"unit_price": price}}]}


def buyer(price):
    return {"from": "buyer", "term_sheets": [{"terms": {"unit_price": price}}, {"terms": {"unit_price": price - 1}}]}


def run(status="running", threads=None, result=None):
    return {"id": "r", "case_id": "case:X", "status": status, "threads": threads or {}, "result": result}


def test_stages_follow_the_transcript():
    assert summarize(run(), CASES)["stage"] == "briefing"
    assert summarize(run(threads={"sup:1": [supplier(17.5)]}), CASES)["stage"] == "opening"
    talking = summarize(run(threads={"sup:1": [supplier(17.5), buyer(14.0), supplier(17.0)]}), CASES)
    assert talking["stage"] == "negotiating"
    assert (talking["start"], talking["now"], talking["bid"], talking["rounds"]) == (17.5, 17.0, 14.0, 1)
    final = run(threads={"sup:1": [supplier(17.5), buyer(14.0), supplier(16.5, "final_offer")]})
    assert summarize(final, CASES)["stage"] == "final_offer"
    deal = run(
        "completed",
        {"sup:1": [supplier(17.5), supplier(16.0, "agreed")]},
        {"deal": True, "terms": {"unit_price": 16.0}},
    )
    assert summarize(deal, CASES)["stage"] == "closed"
    assert summarize(deal, CASES)["now"] == 16.0
    assert summarize(run("completed", result={"deal": False}), CASES)["stage"] == "no_deal"
    assert summarize(run("failed"), CASES)["stage"] == "no_deal"


def test_dashboard_model_choice_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    assert client.post("/api/settings/model", json={"model": "claude-opus-5-5"}).status_code == 200
    assert client.get("/api/status").json()["model"] == "claude-opus-5-5"
    assert client.post("/api/settings/model", json={"model": "gpt-x"}).status_code == 422
    assert (
        client.post(
            "/api/settings/model", json={"model": "claude-haiku-5-5"}, headers={"Origin": "https://example.org"}
        ).status_code
        == 403
    )


def test_hosted_comparison_falls_back_to_committed_report(tmp_path, monkeypatch):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    import json

    import aeiou.service as service

    monkeypatch.setattr(service, "ROOT", tmp_path)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/hosted-practice.json").write_text(json.dumps({"paired_cases": 10}))
    report = client.get("/api/hosted-evaluation").json()
    assert report["source"] == "snapshot"
    assert report["paired_cases"] == 10


def test_pipeline_lists_local_runs(tmp_path, monkeypatch, model_supplier):
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    from aeiou.simulation import local_play

    local_play("case:S01", "adaptive", "hardliner", 1)
    deals = client.get("/api/pipeline").json()["deals"]
    assert deals[0]["stage"] == "closed"
    assert deals[0]["start"] > deals[0]["now"]
