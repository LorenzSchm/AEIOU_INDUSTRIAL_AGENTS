import copy
import json

from test_response_value import fixture

from aeiou.corpus_training import fold_data
from aeiou.response_learning import assign_groups
from aeiou.training_corpus import build_corpus, decision_examples, identity, normalize, records


def test_simulator_session_ids_do_not_collapse_independent_runs(tmp_path):
    run, *_ = fixture()
    for i in range(2):
        local = {**run, "mode": "local", "session_id": "sim1", "id": f"run-{i}"}
        (tmp_path / f"{i}.json").write_text(json.dumps(local))
    (tmp_path / "copy.json").write_text((tmp_path / "0.json").read_text())
    result = build_corpus([tmp_path], tmp_path / "out", public_cases={})
    assert result["sessions"] == 2 and result["duplicate_copies"] == 1
    exported = [json.loads(line) for line in (tmp_path / "out/conversations.jsonl").read_text().splitlines()]
    assert len({r["session_id"] for r in exported}) == 2
    assert len({r["split"] for r in exported}) == 1  # identical trajectories stay together


def test_future_responses_outcomes_and_private_diagnostics_are_not_inputs():
    run, *_ = fixture()
    run.update(result={"score": 1, "secret": "PRIVATE"}, decisions=[{"reasoning": "PRIVATE"}])
    normalized, labels = normalize([(run, {"path": "one"})], {})
    normalized["case_id"] = run["case"]["case_id"]
    before = list(decision_examples(normalized, identity(run), "train"))
    changed = copy.deepcopy(run)
    changed["threads"]["sup:A"][-1]["body"] = "FUTURE"
    changed["result"]["score"] = 0
    updated, _ = normalize([(changed, {"path": "one"})], {})
    after = list(decision_examples(updated, identity(run), "train"))
    assert before == after
    assert "PRIVATE" not in json.dumps(normalized) + json.dumps(labels)
    assert labels[0]["value"]["score"] == 1


def test_nested_report_runs_and_longer_copies_are_recovered():
    run, *_ = fixture()
    short = copy.deepcopy(run)
    short["threads"]["sup:A"] = short["threads"]["sup:A"][:1]
    assert len(list(records({"archive": [run, short]}))) == 2
    combined, _ = normalize([(short, {"path": "old"}), (run, {"path": "new"})], {})
    assert len(combined["threads"]["sup:A"]) == 3
    assert len(combined["corpus_sources"]) == 2


def test_complete_server_transcript_wins_over_unsent_local_request():
    run, *_ = fixture()
    local = copy.deepcopy(run)
    local["threads"]["sup:A"].append({"from": "buyer", "body": "Never delivered"})
    local.update(model="recorded-model", prompt={"strategy_sha256": "abc"})
    remote = {**run, "remote_status": "ended", "provenance": "owned Arena session, GET only"}
    combined, _ = normalize([(local, {"path": "local"}), (remote, {"path": "remote"})], {})
    assert len(combined["threads"]["sup:A"]) == 3
    assert combined["model"] == "recorded-model" and combined["prompt"]["strategy_sha256"] == "abc"


def test_mixed_training_excludes_connected_local_supplier_from_hosted_holdout():
    rows = [
        {"case_id": "a", "supplier_id": "same", "session_id": "host", "source_kind": "hosted"},
        {"case_id": "b", "supplier_id": "same", "session_id": "local", "source_kind": "local"},
        {"case_id": "c", "supplier_id": "other", "session_id": "host2", "source_kind": "hosted"},
    ]
    assign_groups(rows)
    train, test = fold_data(rows, rows[0]["group"])
    assert [r["session_id"] for r in train] == ["host2"]
    assert [r["session_id"] for r in test] == ["host"]


def test_trace_recovers_lost_messages_and_only_past_other_supplier_context():
    run, case, *_ = fixture()
    run.update(provenance="owned Arena session, GET only", remote_status="ended")
    run["threads"] = {}
    run["result"] = {
        "trace": [
            {"actor": "sup:A", "move": {"text": "Opening A", "terms": {"unit_price": 100}}},
            {"actor": "sup:B", "move": {"text": "Opening B", "terms": {"unit_price": 99}}},
            {
                "actor": "buyer",
                "move": {
                    "type": "message",
                    "supplier_id": "sup:A",
                    "body": "Counter",
                    "term_sheets": [{"terms": {"unit_price": 95}}],
                },
                "reasons": ["PRIVATE"],
            },
            {"actor": "sup:B", "move": {"text": "FUTURE", "terms": {"unit_price": 90}}},
            {"actor": "buyer", "move": {"type": "timeout", "supplier_id": "sup:A"}},
        ]
    }
    normalized, _ = normalize([(run, {"path": "server"})], {})
    decisions = list(decision_examples(normalized, identity(run), "train"))
    assert len(normalized["timeline"]) == 5
    assert len(decisions) == 1  # timeout was not a buyer-authored action
    assert decisions[0]["input"]["threads"]["sup:B"][0]["body"] == "Opening B"
    assert "FUTURE" not in json.dumps(decisions) and "PRIVATE" not in json.dumps(normalized)


def test_verification_fixtures_are_archived_but_excluded_from_training(tmp_path):
    run, *_ = fixture()
    run.update(mode="local", id="verification", session_id="sim1")
    (tmp_path / "run.json").write_text(json.dumps(run))
    result = build_corpus([tmp_path], tmp_path / "out", public_cases={}, fixture_run_ids=["verification"])
    assert result["sessions"] == 1 and result["by_source"] == {"verification_fixture": 1}
    assert result["decision_examples"] == result["response_examples"] == 0
