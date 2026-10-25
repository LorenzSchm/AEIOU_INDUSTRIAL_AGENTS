import copy
import hashlib
import json
import sqlite3

import pytest
from arena_kit.data import DataPackage
from assessment_helpers import assessment_fields

from aeiou.config import ROOT
from aeiou.decision_memory import build_context
from aeiou.evidence_transport import (
    MAX_PAGE_CHARS,
    encode_payload,
    prepare_evidence_payload,
    record_text,
    register_read,
    render_attachments,
    restore_reads,
    retrieve,
    source_records,
)
from aeiou.prompt_protocol import PromptLabAgent, PromptSpec
from aeiou.research import ResearchPackage, package, research


@pytest.fixture
def large_package(tmp_path):
    db = sqlite3.connect(tmp_path / "world.sqlite")
    db.execute("CREATE TABLE negotiation_history (event_id TEXT, supplier_id TEXT, part_id TEXT, note TEXT)")
    db.executemany(
        "INSERT INTO negotiation_history VALUES (?,?,?,?)",
        [(f"event:{i:04}", "sup:1", "part:1", f"Negotiation {i}") for i in range(451)]
        + [("event:unrelated", "sup:2", "part:2", "Unrelated")],
    )
    db.execute("CREATE TABLE documents (id TEXT, supplier_id TEXT, date TEXT, body TEXT)")
    db.executemany(
        "INSERT INTO documents VALUES (?,?,?,?)",
        [(f"doc:{i:02}", "sup:1", "2026-01-01", f"Document {i}") for i in range(12)]
        + [("doc:other", "sup:2", "2026-01-01", "Unrelated")],
    )
    db.commit()
    db.close()
    (tmp_path / "facts.json").write_text(json.dumps({"sup:1": {"name": "Supplier"}, "part:1": {"unit": "kg"}}))
    return ResearchPackage(tmp_path)


def test_collection_pages_all_matching_rows_and_reads_more_than_eight_documents(large_package):
    case = {"case_id": "case:test", "suppliers": ["sup:1"], "parts": ["part:1"], "refs": []}
    data = research(case, large_package)
    assert len(data["tables"]["negotiation_history"]) == 451
    assert len({r["event_id"] for r in data["tables"]["negotiation_history"]}) == 451
    assert {d["text"] for d in data["documents"]} == {f"Document {i}" for i in range(12)}
    coverage = data["collection"]["tables"]["negotiation_history"]
    assert coverage["status"] == "complete"
    assert coverage["expected_rows"] == coverage["loaded_rows"] == coverage["retained_rows"] == 451
    assert "doc:other" not in data["facts"]


def test_collection_detects_short_reads_and_missing_references(large_package, monkeypatch):
    case = {"case_id": "case:test", "suppliers": ["sup:1"], "parts": ["part:1"], "refs": ["doc:missing"]}
    data = research(case, large_package)
    assert any(i["source_ref"] == "doc:missing" for i in data["collection"]["issues"])
    original = large_package.query

    def broken(sql, params=(), limit=200):
        rows = original(sql, params, limit)
        return rows[:1] if 'SELECT * FROM "negotiation_history"' in sql else rows

    monkeypatch.setattr(large_package, "query", broken)
    with pytest.raises(ValueError, match="Incomplete buyer data: negotiation_history"):
        research(case, large_package)


def test_part_master_is_not_replaced_by_a_foreign_key_in_the_kit_index():
    pkg = package()
    part = pkg.lookup("part:370010")
    assert part["table"] == "parts"
    assert part["id"] == "part:370010"
    assert part["uom"] == "kg" and part["price_unit"] == 1
    assert "description" in part and "quota_pct" not in part
    assert pkg.lookup("part:does-not-exist") is None
    # The base class is also used by arena-data MCP, outside our Python research wrapper.
    connector = DataPackage(pkg.root)
    assert connector.lookup("part:370010") == part
    assert connector.lookup("sup:70740")["table"] == "suppliers"


def test_sunday_release_cannot_leak_into_base_only_practice_cases(large_package):
    release = large_package.root / "release-sunday"
    release.mkdir()
    (release / "facts.json").write_text(
        json.dumps({"news:sunday": {"supplier": "sup:1", "body": "New Sunday development"}})
    )
    pkg = ResearchPackage(large_package.root)
    case = {"case_id": "case:test", "set": "practice", "suppliers": ["sup:1"], "parts": ["part:1"], "refs": []}
    assert pkg.release
    practice = research(case, pkg)
    hidden = research({**case, "set": "hidden"}, pkg)
    assert "news:sunday" not in practice["facts"]
    assert not practice["collection"]["sunday_release_loaded"]
    assert "news:sunday" in hidden["facts"]
    assert hidden["collection"]["sunday_release_loaded"]
    assert "news:sunday" in pkg.facts  # Case views do not mutate the shared package.


def transport_fixture():
    case = {"case_id": "case:test", "suppliers": ["sup:1"], "parts": [], "refs": []}
    threads = {
        "sup:1": [
            {
                "from": "sales",
                "body": "See attachment",
                "status": "open",
                "attachments": [{"name": "long.txt", "text": "a" * 40000 + "TAIL_EVIDENCE"}],
            },
            {"from": "buyer", "body": "Our sheet", "attachments": [{"name": "ours", "text": "ok"}]},
        ]
    }
    info = {
        "facts": {"sup:1": {"name": "Supplier"}},
        "documents": [{"id": "doc:long", "text": "d" * 3000 + "DOCUMENT_TAIL"}],
        "tables": {},
    }
    context = build_context(case, threads, info, {})
    payload = {
        "case": case,
        "research": copy.deepcopy(info),
        "decision_context": context,
        "threads": {
            sid: {
                "messages": [
                    {"source_ref": f"message:{sid}:{i}", **message, "attachments": render_attachments(sid, i, message)}
                    for i, message in enumerate(events)
                ]
            }
            for sid, events in threads.items()
        },
    }
    records = prepare_evidence_payload(payload, info, threads, "")
    return payload, records


def test_truncated_sources_are_partial_and_preserve_party_provenance():
    payload, records = transport_fixture()
    sources = payload["decision_context"]["sources"]
    for ref in ("doc:long", "attachment:sup:1:0:0"):
        assert sources[ref]["available"]
        assert sources[ref]["delivery"] == "partial"
        assert not sources[ref]["complete_record"]
        assert sources[ref]["retrievable"]
    assert sources["attachment:sup:1:0:0"]["kind"] == "supplier_claim"
    assert sources["attachment:sup:1:1:0"]["kind"] == "buyer_message"
    assert sources["attachment:sup:1:1:0"]["delivery"] == "full"
    page = retrieve(records, {"source_ref": "attachment:sup:1:0:0", "find": "TAIL_EVIDENCE"})
    assert "TAIL_EVIDENCE" in page["text"]
    assert page["offset"] > 2 * MAX_PAGE_CHARS
    assert page["complete_record"] is False
    payload["retrieved_evidence"] = [page]
    register_read(payload, page)
    assert sources[page["source_ref"]]["delivery"] == "partial"  # Finding the tail did not read the whole file.
    assert payload["evidence_transport"]["unretrievable_refs"] == []


def test_document_record_preserves_both_canonical_metadata_and_full_text():
    info = {
        "facts": {"doc:1": {"asked_pct": 12, "date": "2026-06-01"}},
        "documents": [{"id": "doc:1", "text": "Full document"}],
    }
    record = source_records(info, {})["doc:1"]
    assert record["metadata"] == info["facts"]["doc:1"]
    assert record["text"] == "Full document"


@pytest.mark.parametrize(
    "fields", [{"find": ""}, {"find": "absent"}, {"find": "d", "offset": 0}, {"find": "x" * 201}, {"find": True}]
)
def test_local_find_rejects_invalid_or_missing_patterns(fields):
    _, records = transport_fixture()
    with pytest.raises(ValueError):
        retrieve(records, {"source_ref": "doc:long", **fields})


def test_page_coverage_requires_every_byte_and_cache_rejects_changed_records():
    payload, records = transport_fixture()
    ref = "attachment:sup:1:0:0"
    pages, offset = [], 0
    while offset is not None:
        page = retrieve(records, {"source_ref": ref, "offset": offset})
        pages.append(page)
        offset = page["next_offset"]
    payload["retrieved_evidence"] = pages
    register_read(payload, pages[-1])
    assert payload["decision_context"]["sources"][ref]["complete_record"]
    assert "".join(p["text"] for p in pages) == record_text(records[ref])
    fresh, _ = transport_fixture()
    restored = restore_reads(fresh, records, pages)
    assert restored == pages
    assert fresh["decision_context"]["sources"][ref]["complete_record"]
    missing, _ = transport_fixture()
    restore_reads(missing, records, pages[1:])
    assert not missing["decision_context"]["sources"][ref]["complete_record"]
    changed, changed_records = transport_fixture()
    changed_records[ref]["text"] = "changed"
    assert restore_reads(changed, changed_records, pages) == []


def test_p18_collection_has_no_inaccessible_source_or_unexplained_row_loss():
    pkg = package()
    case = json.loads((ROOT / "data/package/cases/practice/P18.json").read_text())
    agent = PromptLabAgent(pkg, None, PromptSpec("audit", "Use only sourced facts", assessment_enabled=True))
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, {}))
    records = source_records(agent.intelligence, {})
    sources = payload["decision_context"]["sources"]
    assert set(records) <= sources.keys()
    assert all(s["delivery"] == "full" or s["retrievable"] for s in sources.values())
    assert payload["evidence_transport"]["unretrievable_refs"] == []
    for name in (
        "po_lines",
        "goods_receipts",
        "invoices",
        "quality_notifications",
        "forecast_history_lines",
        "index_values",
        "negotiation_history",
        "contracts",
    ):
        coverage = payload["research"]["collection"]["tables"][name]
        assert coverage["expected_rows"] == coverage["loaded_rows"] == len(records[f"table:{name}"])
    quotes = payload["research"]["collection"]["tables"]["quotes"]
    assert quotes["status"] == "filtered_by_case" and quotes["retained_rows"] == 1
    assert all(ref not in records for ref in quotes["excluded_refs"])


def test_old_messages_and_attachments_remain_retrievable_outside_display_window():
    case = json.loads((ROOT / "data/package/cases/practice/P01.json").read_text())
    sid = case["suppliers"][0]
    threads = {
        sid: [
            {
                "from": "supplier",
                "body": f"Message {i}",
                "status": "open",
                "attachments": [{"name": "note", "text": f"Full note {i}"}],
            }
            for i in range(18)
        ]
    }
    agent = PromptLabAgent(package(), None, PromptSpec("test", "Use evidence", assessment_enabled=True))
    agent.prepare(case)
    payload = json.loads(agent.render_lab(case, threads))
    records = source_records(agent.intelligence, threads)
    for ref in (f"message:{sid}:0", f"attachment:{sid}:0:0"):
        assert payload["decision_context"]["sources"][ref]["delivery"] == "deferred"
        assert retrieve(records, {"source_ref": ref})["complete_record"]


def test_actual_backend_receives_reads_and_carries_them_into_next_turn_with_exact_receipts():
    case = json.loads((ROOT / "data/package/cases/practice/P01.json").read_text())
    sid, ref = case["suppliers"][0], "table:po_lines"
    threads = {sid: [{"from": "supplier", "body": "Please review our terms.", "status": "open"}]}

    class Backend:
        requests = []

        def complete(self, system, user):
            self.requests.append(user)
            payload = json.loads(user)
            assert payload["evidence_transport"]["delivered_payload_chars"] == len(user)
            if len(self.requests) == 1:
                return json.dumps({"action": "retrieve_evidence", "source_ref": ref})
            assert payload["decision_context"]["sources"][ref]["available"]
            assert payload["retrieved_evidence"][0]["source_ref"] == ref
            return json.dumps(
                {
                    "supplier_id": sid,
                    "action": "send",
                    "body": "Thank you. Please clarify.",
                    "term_sheets": [],
                    **assessment_fields(payload, sid),
                }
            )

    backend = Backend()
    agent = PromptLabAgent(package(), backend, PromptSpec("test", "Use evidence", assessment_enabled=True))
    move = agent.decide(case, threads)
    threads[sid].extend(
        [{**move["message"], "from": "buyer"}, {"from": "supplier", "body": "We can review this.", "status": "open"}]
    )
    agent.decide(case, threads)
    assert len(backend.requests) == 3  # The second turn reuses the local read.
    receipts = agent.audit["model_requests"]
    for request, receipt in zip(backend.requests, receipts, strict=True):
        assert receipt["payload_sha256"] == hashlib.sha256(request.encode()).hexdigest()
        assert receipt["payload_chars"] == len(request)
    assert json.loads(backend.requests[-1])["evidence_transport"]["retained_pages"] == 1
    fresh = PromptLabAgent(package(), None, agent.spec)
    fresh.prepare(case)
    assert fresh.evidence_cache == []
    other = json.loads((ROOT / "data/package/cases/practice/P02.json").read_text())
    agent.prepare(other)
    assert agent.evidence_cache == []


def test_payload_size_is_recomputed_after_a_read_and_repair():
    payload, records = transport_fixture()
    first = encode_payload(payload)
    page = retrieve(records, {"source_ref": "doc:long"})
    payload["retrieved_evidence"] = [page]
    register_read(payload, page)
    payload["action_repair"] = {"error": "Test rejection", "instruction": "Use the actual available evidence"}
    second = encode_payload(payload)
    assert len(second) > len(first)
    assert json.loads(second)["evidence_transport"]["delivered_payload_chars"] == len(second)
