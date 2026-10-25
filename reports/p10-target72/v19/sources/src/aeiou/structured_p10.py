"""Opt-in P10 experiment using the documented /moves API and typed public claims.

The workbench's default email transport is unchanged. This adapter deliberately
supports one proposal and two literal evidence cards, not arbitrary claim inference.
"""

import copy
import re

from arena_kit.agents.llm import ID_RE


def public_claims(body, facts):
    """Translate only explicitly cited, exact source-backed evidence sentences."""
    text = " ".join(body.split())
    refs = set(ID_RE.findall(body))
    supported = {"cost:340010", "sc:70410@2026Q3"}
    if refs - supported:
        raise ValueError(f"Unsupported source in structured P10 experiment: {sorted(refs - supported)}")
    claims = []
    if "cost:340010" in refs:
        fact = facts["cost:340010"]
        if fact.get("part") != "part:340010" or fact.get("price_unit") != 1:
            raise ValueError("P10 should-cost source has the wrong scope or price unit")
        value, uncertainty = fact["unit_cost"], fact["confidence_pct"]
        required = f"EUR {value:.2f}/unit excluding margin, with {uncertainty:g}% uncertainty (cost:340010)"
        if required not in text:
            raise ValueError("The should-cost sentence must match the actual public value and uncertainty")
        claims.append({"type": "should_cost", "refs": ["cost:340010"], "asserted": {"unit_cost": value}})
    if "sc:70410@2026Q3" in refs:
        fact = facts["sc:70410@2026Q3"]
        if fact.get("supplier") != "sup:70410":
            raise ValueError("P10 service evidence has the wrong supplier")
        value = fact["otd_pct"]
        required = f"Supplier-wide on-time delivery was {value:g}% (sc:70410@2026Q3)"
        if required not in text:
            raise ValueError("The delivery sentence must match the actual public service record")
        claims.append({"type": "delivery_record", "refs": ["sc:70410@2026Q3"], "asserted": {"otd_pct": value}})
    return claims


class StructuredP10Client:
    """Preserve model terms/text and verify the corresponding authoritative reply."""

    def __init__(self, client, pkg):
        self.client, self.pkg = client, pkg
        self._sessions, self.wire = {}, []

    def __getattr__(self, name):
        return getattr(self.client, name)

    def open_session(self, case_id):
        if case_id != "case:P10":
            raise ValueError("Structured P10 transport cannot open another case")
        opened = self.client.open_session(case_id)
        case = opened["case"]
        if case.get("suppliers") != ["sup:70410"] or case.get("parts") != ["part:340010"]:
            raise ValueError("Unexpected P10 supplier/part scope")
        pkg = self.pkg.for_case(case)
        self._sessions[opened["session_id"]] = {
            "case": case, "offer_id": opened["opening"]["sup:70410"]["offer_id"],
            "facts": {ref: pkg.lookup(ref) for ref in ("cost:340010", "sc:70410@2026Q3")},
        }
        return opened

    def send_message(self, session_id, supplier_id, message):
        state = self._sessions.get(session_id)
        if state is None or supplier_id != "sup:70410":
            raise ValueError("Only this adapter's active P10 conversation may use structured sends")
        if message.get("action"):
            # Keep closing requests on the normal endpoint, including exact offer selectors.
            return self.client.send_message(session_id, supplier_id, message)
        body, sheets = message.get("body"), message.get("term_sheets", [])
        if not isinstance(body, str) or not body.strip() or len(body) > 800:
            raise ValueError("Structured P10 text must be 1–800 characters; no silent truncation")
        if len(sheets) != 1 or set(sheets[0].get("terms", {})) != set(state["case"]["negotiable"]):
            raise ValueError("Structured P10 sends require one complete proposal; no silent menu selection")
        if message.get("refs") or message.get("attachments"):
            raise ValueError("Structured P10 does not silently discard separate refs or attachments")
        if re.search(r"<negotiation_(preparation|strategy)>", body):
            raise ValueError("Private preparation cannot be transmitted")
        move = {"type": "offer", "offer_id": state["offer_id"], "terms": copy.deepcopy(sheets[0]["terms"]),
                "claims": public_claims(body, state["facts"]), "text": body}
        receipt = {"session_id": session_id, "supplier_id": supplier_id, "move": copy.deepcopy(move)}
        self.wire.append(receipt)
        reply = self.client.move(session_id, supplier_id, move)
        receipt["reply"] = copy.deepcopy(reply)
        state["offer_id"] = reply.get("offer_id")
        # Read the server's email representation instead of inventing messages or attachments.
        thread = self.client.thread(session_id, supplier_id)
        message = thread["messages"][-1]
        if message.get("from") == "buyer" or message.get("status") != reply.get("status"):
            raise ValueError("Structured reply and authoritative thread are inconsistent")
        if reply.get("status") not in {"walked_away", "no_bid"} and not any(
                sheet.get("term_sheet_id") == reply.get("offer_id") for sheet in message.get("term_sheets", [])):
            raise ValueError("Structured reply ID is not present in the authoritative supplier message")
        return message
