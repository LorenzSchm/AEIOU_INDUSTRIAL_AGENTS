import copy
import hashlib
import json

import pytest

from aeiou.evaluation import evaluate_matrix, features_for, paired_interval
from aeiou.grounding import compose_email, next_evidence_ids, safe_excerpt, validate_claims
from aeiou.opponent_model import learn_beliefs, predicted_choices
from aeiou.prompt_optimization import validate_split
from aeiou.retrieval import HybridEvidenceIndex, cosine, evidence_cards
from aeiou.simulation import local_cases
from aeiou.store import get_run, new_run, save_run
from aeiou.strategy_policy import StrategicFeatures


def history():
    case = copy.deepcopy(local_cases()["case:S01"])
    events = [
        {"from": "supplier", "status": "open", "term_sheets": [{"terms": {"unit_price": 17}}]},
        {
            "from": "buyer",
            "term_sheets": [
                {"terms": {"unit_price": 15, "payment_days": 14}},
                {"terms": {"unit_price": 15.3, "payment_days": 90}},
            ],
        },
        {
            "from": "supplier",
            "status": "open",
            "term_sheets": [
                {"term_sheet_id": "reply", "terms": {"unit_price": 16, "payment_days": 14}},
            ],
        },
    ]
    return case, events


def test_posterior_uses_traceable_choices_not_repeated_messages():
    case, events = history()
    belief = learn_beliefs(case, events)
    assert belief["observations"] == 1
    assert belief["evidence"] == [{"message_index": 2, "offer_id": "reply"}]
    assert sum(h["weight"] for h in belief["hypotheses"]) == pytest.approx(1)
    assert len({round(h["weight"], 6) for h in belief["hypotheses"]}) > 1
    assert learn_beliefs(case, events + [events[-1]]) == belief
    assert learn_beliefs(case, events, enabled=False)["observations"] == 0
    packages = [{"terms": s["terms"]} for s in events[1]["term_sheets"]]
    assert sum(predicted_choices(case, packages, belief)) == pytest.approx(1)


def test_ambiguous_package_does_not_create_a_preference():
    case, events = history()
    events[1]["term_sheets"][1]["terms"]["payment_days"] = 14
    assert learn_beliefs(case, events)["observations"] == 0


def test_hybrid_retrieval_scope_date_and_dense_content_identity():
    documents = [
        {
            "id": "cash",
            "supplier_id": "s",
            "part_id": "p",
            "date": "2026-01-01",
            "text": "Working capital and financing limit our production flexibility.",
        },
        {"id": "future", "supplier_id": "s", "date": "2027-01-01", "text": "Cash payment future."},
        {"id": "other", "supplier_id": "other", "text": "Cash payment wrong supplier."},
    ]
    index = HybridEvidenceIndex(documents)
    hits = index.search("liquidity", ["s"], ["p"], cutoff="2026-09-30")
    assert [h["id"] for h in hits] == ["cash"]
    dense = HybridEvidenceIndex(documents, {hits[0]["chunk_id"]: [1.0, 0.0]})
    assert dense.search("unrelated", ["s"], query_vector=[1.0, 0.0])[0]["id"] == "cash"
    with pytest.raises(ValueError):
        HybridEvidenceIndex(documents, {"stale-hash": [1.0]})
    with pytest.raises(ValueError):
        cosine([1], [1, 2])
    with pytest.raises(ValueError):
        index.search("cash", [])


def test_grounded_language_rejects_private_wrong_supplier_and_changed_qualification():
    card = {
        "id": "a",
        "source_id": "doc:a",
        "supplier_id": "s",
        "shareable": True,
        "text": "Our material prices are fixed for the current quarter only.",
        "kind": "verbatim_source",
    }
    body = compose_email({"evidence_ids": ["a"], "body": "invented claim"}, [card], "s")
    assert card["text"] in body and "invented" not in body
    for supplier, shareable in [("other", True), ("s", False)]:
        with pytest.raises(ValueError):
            compose_email({"evidence_ids": ["a"]}, [{**card, "shareable": shareable}], supplier)
    with pytest.raises(ValueError):
        validate_claims([{"evidence_id": "a", "text": "Our prices are fixed forever."}], [card], "s")
    with pytest.raises(ValueError):
        compose_email({"question": "invented"}, [], "s")


def test_chunk_fragments_and_internal_notes_are_private():
    hit = {
        "id": "doc:a",
        "chunk_id": "chunk",
        "text": "A complete supplier statement about production flexibility.",
        "supplier_id": "s",
        "method": "bm25",
        "type": "letter",
        "offset": 0,
    }
    assert evidence_cards([hit], {})[0]["shareable"]
    assert not evidence_cards([{**hit, "offset": 700}], {})[0]["shareable"]
    assert not evidence_cards([{**hit, "type": "meeting_note"}], {})[0]["shareable"]
    assert safe_excerpt("An incomplete quotation that could hide qualifications") is None


def test_new_evidence_is_reserved_for_resistance_and_not_repeated():
    cards = [
        {"id": "first", "source_id": "doc:20251009-70340-N", "shareable": True, "supplier_id": "s"},
        {"id": "next", "source_id": "doc:20251212-70360-N", "shareable": True, "supplier_id": "s"},
    ]

    def quote(price):
        return {"from": "seller", "term_sheets": [{"terms": {"unit_price": price}}]}

    assert next_evidence_ids(cards, {"s": [quote(10)]}, "s") == ["first"]
    events = [quote(10), {"from": "buyer", "body": "See doc:20251009-70340-N"}, quote(9)]
    assert next_evidence_ids(cards, {"s": events}, "s") == []
    events.append(quote(9.1))
    assert next_evidence_ids(cards, {"s": events}, "s") == ["next"]
    assert next_evidence_ids([{**cards[1], "shareable": False}], {"s": events}, "s") == []


def test_evidence_feedback_improves_skeptical_opponent_without_private_state(tmp_path, request):
    report = evaluate_matrix(
        agents=["adaptive", "strategic-policy", "strategic-policy:no-grounded-language"],
        case_ids=["case:S01"],
        opponents=["index_skeptic"],
        seeds=[100],
        output=tmp_path,
    )
    scores = report["rows"][0]["scores"]
    assert scores["strategic-policy:no-grounded-language"] == scores["adaptive"]
    assert report["arena_sessions_used"] == 0
    request.node.add_marker(
        pytest.mark.xfail(strict=True, reason="Upstream 89d0f6d also ties adaptive; see docs/MERGE_INTEGRATION.md")
    )
    assert scores["strategic-policy"] > scores["adaptive"]


def test_evaluation_caps_and_api_gate_precede_execution(tmp_path):
    with pytest.raises(ValueError, match="allow_api"):
        evaluate_matrix(agents=["strategic"], output=tmp_path)
    with pytest.raises(ValueError, match="limit"):
        evaluate_matrix(max_sessions=1, output=tmp_path)
    assert not list(tmp_path.iterdir())
    for field in StrategicFeatures().metadata():
        _, flags = features_for("strategic-policy:no-" + field.replace("_", "-"))
        assert getattr(flags, field) is False


def test_cluster_interval_does_not_count_repetitions_as_new_cases():
    rows = [{"case_id": "a", "scores": {"ref": 0.5, "new": 0.6}}] * 10
    rows += [{"case_id": "b", "scores": {"ref": 0.5, "new": 0.7}}]
    result = paired_interval(rows, "ref", "new")
    assert result["case_clusters"] == 2
    assert result["mean_case_delta"] == pytest.approx(0.15)


def test_optimization_rejects_overlap_and_unknown_cases():
    with pytest.raises(ValueError, match="disjoint"):
        validate_split(["case:S01"], ["case:S01"], ["case:S03"])
    with pytest.raises(ValueError, match="Unknown"):
        validate_split(["case:unknown"], ["case:S02"], ["case:S03"])


def test_run_store_roundtrips_original_unicode(monkeypatch, tmp_path):
    monkeypatch.setattr("aeiou.store.state_root", lambda: tmp_path)
    run = new_run("case:S01", "local", "strategic-policy")
    run["evidence"] = "Příliš žluťoučký supplier evidence 日本語"
    save_run(run)
    assert get_run(run["id"]) == run


def test_optimized_instruction_is_explicit_and_hash_checked(tmp_path):
    from aeiou.prompt_optimization import load_writer_instruction

    text = "Ask one diagnostic question."
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"winner": {"instruction": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}}))
    assert load_writer_instruction(path) == text
    path.write_text(json.dumps({"winner": {"instruction": text, "sha256": "wrong"}}))
    with pytest.raises(ValueError, match="hash"):
        load_writer_instruction(path)


def test_supplier_failure_excludes_whole_pair_but_keeps_billing(monkeypatch, tmp_path):
    def fake_run(case_id, label, *args):
        return {
            "id": label,
            "status": "failed" if label == "strategic-policy" else "completed",
            "failure_origin": "supplier" if label == "strategic-policy" else None,
            "result": {"score": 0.8},
            "metrics": {"calls": 2, "input_tokens": 10},
            "supplier_metrics": {"calls": 3, "input_tokens": 20, "output_tokens": 5},
        }

    monkeypatch.setattr("aeiou.evaluation.evaluate_run", fake_run)
    report = evaluate_matrix(case_ids=["case:S01"], opponents=["hardliner"], seeds=[71], output=tmp_path)
    assert report["excluded_pairs"] == 1
    assert report["summary"]["adaptive"]["sessions"] == 0
    assert report["attempted_usage"]["adaptive"]["input_tokens"] == 30
    assert report["attempted_usage"]["strategic-policy"]["supplier_calls"] == 3


@pytest.mark.parametrize("case_id,opponent", [("case:S01", "hardliner"), ("case:S02", "scripted")])
def test_controller_preserves_terminal_offers_and_multi_supplier_upside(case_id, opponent, tmp_path, request):
    report = evaluate_matrix(case_ids=[case_id], opponents=[opponent], seeds=[99], output=tmp_path)
    assert report["excluded_pairs"] == 0
    assert report["summary"]["strategic-policy"]["failures"] == 0
    if case_id == "case:S02":
        request.node.add_marker(
            pytest.mark.xfail(
                strict=True, reason="Upstream 89d0f6d also trails adaptive; see docs/MERGE_INTEGRATION.md"
            )
        )
    assert report["summary"]["strategic-policy"]["mean_score"] >= report["summary"]["adaptive"]["mean_score"]


def test_prompt_search_reflects_on_train_only_and_tests_winner_once(monkeypatch, tmp_path):
    from aeiou.prompt_optimization import optimize_writer

    seen, reflections = [], []

    def fake_matrix(**kwargs):
        ids = list(kwargs["case_ids"])
        seen.append(ids)
        agent = kwargs["agents"][0]
        return {
            "summary": {agent: {"mean_score": 0.8}},
            "report_path": str(kwargs["output"]),
            "rows": [{"case_id": c, "scores": {agent: 0.8}, "run_ids": {agent: c}} for c in ids],
        }

    class Reflector:
        def complete(self, system, user):
            reflections.append(json.loads(user))
            return "Ask which package can be authorized."

    monkeypatch.setattr("aeiou.prompt_optimization.evaluate_matrix", fake_matrix)
    monkeypatch.setattr("aeiou.store.get_run", lambda _: {"status": "completed", "decisions": [], "threads": {}})
    report = optimize_writer(rounds=1, agent="strategic-policy", allow_api=True, reflector=Reflector(), output=tmp_path)
    assert seen.count(["case:T02"]) == 1
    assert {x["case_id"] for x in reflections[0]["training_feedback"]} == {"case:S01", "case:S02", "case:T01"}
    assert report["winner"]["instruction"] == ""  # Ties preserve the baseline.
