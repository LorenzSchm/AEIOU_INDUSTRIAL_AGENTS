import base64
import copy
import hashlib
import json

import httpx
import pytest
from arena_kit import ArenaClient
from assessment_helpers import assessment_fields

from aeiou.config import ROOT
from aeiou.evidence_transport import (
    MAX_SOURCE_ATTACHMENT_BYTES,
    outgoing_source_attachments,
    render_attachments,
    source_attachment_catalog,
    source_records,
)
from aeiou.prompt_protocol import ContractError, PromptLabAgent, PromptSpec
from aeiou.research import package

SID = "sup:70410"
NOTE = "doc:20260310-70410-M1"


@pytest.fixture
def prepared():
    case = json.loads((ROOT / "data/package/cases/practice/P10.json").read_text())
    threads = {SID: [{"from": "sales@70410", "status": "open", "body": "Please provide the March note.",
                      "term_sheets": [{"term_sheet_id": "offer:70410-0", "terms": {
                          "unit_price": 32.2, "volume_per_year": 19890,
                          "contract_months": 12, "lead_time_days": 54}}]}]}
    agent = PromptLabAgent(package(), None, PromptSpec("attachment-test", "Negotiate P10.", assessment_enabled=True))
    agent.prepare(case)
    return agent, case, threads


def action():
    return {"action": "send", "supplier_id": SID, "subject": "Recorded approval process",
            "body": "Attached is the historical March meeting note. Please review our proposal.",
            "term_sheets": [{"unit_price": 31, "volume_per_year": 23400,
                             "contract_months": 36, "lead_time_days": 54}], "attach_sources": [NOTE]}


def test_exact_source_reaches_http_request_and_is_audited(prepared):
    agent, case, threads = prepared
    request = action()

    class Backend:
        def complete(self, system, user):
            payload = json.loads(user)
            assert NOTE in payload["source_attachments"][SID]
            assert payload["decision_context"]["sources"][NOTE]["complete_record"]
            return json.dumps({**request, **assessment_fields(payload, SID)})

    agent.backend = Backend()
    move = agent.decide(case, threads)
    original = agent.pkg.document(NOTE).encode("utf-8")
    sent = []

    def receive(req):
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"status": "open"})

    client = ArenaClient("https://arena.test", api_key="fixture", transport=httpx.MockTransport(receive))
    client.send_message("fixture-session", SID, move["message"])
    message = sent[0]["message"]
    assert message["body"] == request["body"]
    assert message["term_sheets"][0]["terms"]["lead_time_days"] == 54
    attachment, = message["attachments"]
    assert set(attachment) == {"name", "type", "content_b64"}
    assert attachment["name"] == "doc-20260310-70410-M1.md"
    assert attachment["type"] == "md"
    assert base64.b64decode(attachment["content_b64"], validate=True) == original
    receipt, = agent.decisions[-1]["outgoing_source_attachments"]
    assert receipt == {"source_ref": NOTE, "name": attachment["name"], "bytes": len(original),
                       "content_sha256": hashlib.sha256(original).hexdigest()}
    # The following decision sees what was actually attached, not an empty file placeholder.
    threads[SID].append({"from": "buyer", **message})
    payload = json.loads(agent.render_lab(case, threads))
    rendered, = payload["threads"][SID]["messages"][-1]["attachments"]
    assert rendered["text"] == original.decode("utf-8")
    assert not rendered["truncated"]
    record = source_records(agent.intelligence, threads)[rendered["source_ref"]]
    assert record["text"] == original.decode("utf-8")
    assert payload["decision_context"]["sources"][rendered["source_ref"]]["kind"] == "buyer_message"


def test_partial_document_must_be_read_before_it_can_be_sent(prepared):
    agent, case, threads = prepared
    doc = next(d for d in agent.intelligence["documents"] if d["id"] == NOTE)
    doc["text"] = "Meeting note. " * 300 + "END OF COMPLETE NOTE"

    class Backend:
        calls = 0

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            if self.calls == 2:
                assert "Read the complete document" in payload["action_repair"]["error"]
                return json.dumps({"action": "retrieve_evidence", "source_ref": NOTE, "offset": 0})
            if self.calls == 3:
                assert payload["decision_context"]["sources"][NOTE]["complete_record"]
            return json.dumps({**action(), **assessment_fields(payload, SID)})

    agent.backend = Backend()
    move = agent.decide(case, threads)
    assert agent.backend.calls == 3
    assert len(agent.audit["validation_failures"]) == 1
    assert len(agent.decisions) == 1
    assert base64.b64decode(move["message"]["attachments"][0]["content_b64"]).decode() == doc["text"]
    # A cached read of different content cannot authorize sending a changed, unread source.
    doc["text"] += " NEW UNREAD TEXT"
    with pytest.raises(ContractError, match="Read the complete document"):
        agent.contract(case, threads, action())


@pytest.mark.parametrize("refs", [NOTE, None, [1], [NOTE, NOTE], [NOTE] * 6,
                                  ["table:po_lines"], ["p10:ordered_last_12m_units"],
                                  ["doc:missing"], ["/etc/passwd"]])
def test_invalid_attachment_selections_are_rejected_without_changing_the_action(prepared, refs):
    agent, case, threads = prepared
    request = {**action(), "attach_sources": refs}
    original = copy.deepcopy(request)
    with pytest.raises(ContractError):
        agent.contract(case, threads, request)
    assert request == original


def test_different_supplier_and_model_authored_contents_are_not_sent(prepared):
    agent, case, threads = prepared
    agent.intelligence["facts"]["doc:foreign"] = {"supplier": "sup:999"}
    agent.intelligence["documents"].append({"id": "doc:foreign", "text": "Other supplier's document"})
    with pytest.raises(ContractError, match="for this recipient"):
        agent.contract(case, threads, {**action(), "attach_sources": ["doc:foreign"]})
    with pytest.raises(ContractError, match="raw attachments are unsupported"):
        agent.contract(case, threads, {**action(), "attachments": [{"name": "invented.md", "text": "Invented"}]})
    with pytest.raises(ContractError, match="only with action: send"):
        agent.contract(case, threads, {"supplier_id": SID, "action": "accept",
                                      "term_sheet_id": "offer:70410-0", "attach_sources": [NOTE]})


def test_size_limits_use_utf8_bytes_and_never_truncate_documents():
    text = "ü" * (MAX_SOURCE_ATTACHMENT_BYTES // 2)
    info = {"facts": {NOTE: {"supplier": SID}}, "documents": [{"id": NOTE, "text": text}]}
    encoded, = outgoing_source_attachments({"attach_sources": [NOTE]}, SID, info)
    assert len(base64.b64decode(encoded["content_b64"])) == MAX_SOURCE_ATTACHMENT_BYTES
    assert source_attachment_catalog(info)[SID][NOTE]["bytes"] == MAX_SOURCE_ATTACHMENT_BYTES
    rendered, = render_attachments(SID, 0, {"attachments": [encoded]})
    assert rendered["truncated"] and rendered["text"] == text[:3500]
    info["documents"][0]["text"] += "ü"
    with pytest.raises(ValueError, match="32 KiB"):
        outgoing_source_attachments({"attach_sources": [NOTE]}, SID, info)


def test_no_attachment_is_added_without_selection(prepared):
    agent, case, threads = prepared
    request = action()
    request.pop("attach_sources")
    assert "attachments" not in agent.contract(case, threads, request)["message"]


def test_workbench_exposes_attachment_text_without_changing_stored_wire_request(prepared, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from aeiou.service import app
    from aeiou.store import get_run, save_run

    agent, case, threads = prepared
    message = agent.contract(case, threads, action())["message"]
    stored = {"id": "attachmentfixture", "threads": {SID: [{"from": "buyer", **message}]}}
    monkeypatch.setenv("AEIOU_STATE", str(tmp_path))
    save_run(stored)
    response = TestClient(app).get("/api/runs/attachmentfixture")
    assert response.status_code == 200
    assert response.json()["threads"][SID][0]["attachments"][0]["text"] == agent.pkg.document(NOTE)
    assert get_run("attachmentfixture") == stored
