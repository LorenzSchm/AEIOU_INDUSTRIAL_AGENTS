"""Compose a controlled strategic act using approved, verbatim evidence cards."""

import re

OPENINGS = {
    "warm": "Thank you for working through the options with us. We value a reliable agreement.",
    "concise": "Thank you for your response. Please review the complete conditional packages below.",
    "firm": "We can move conditionally on a complete package that works for both parties.",
}
QUESTIONS = {
    "priorities": "Which package best fits your operating needs, and which term prevents agreement?",
    "cash": "Would earlier payment allow a reciprocal improvement in the complete package?",
    "continuity": "Would a longer commitment support better pricing or delivery terms?",
    "authority": "Which complete package could your decision-maker authorize?",
    "costs": "Could you share a cost breakdown and explain which drivers affect this quotation?",
    "delivery": "What delivery flexibility would allow you to improve the overall package?",
    "quality": "What quality commitment and complaint compensation could you approve together?",
}


def next_evidence_ids(cards, threads, sid):
    """Use a relevant, approved source once; do not spend turns repeating claims."""
    from .agent import ID_RE

    events = threads.get(sid, [])
    prices = [
        sheet["terms"]["unit_price"]
        for message in events
        if message.get("from") != "buyer"
        for sheet in message.get("term_sheets", [])[:1]
        if sheet.get("terms", {}).get("unit_price")
    ]
    # Let a productive concession continue. Introduce a fresh cost driver when
    # public price movement stalls or reverses, rather than unloading all evidence
    # in the opening or repeating a claim the supplier has already addressed.
    if len(prices) >= 2 and prices[-1] < prices[-2]:
        return []
    used = {
        ref
        for events in threads.values()
        for message in events
        if message.get("from") == "buyer"
        for ref in ID_RE.findall(message.get("body", ""))
    }
    for card in cards:
        refs = card.get("source_ids") or [card.get("source_id")]
        if (
            card.get("shareable")
            and card.get("supplier_id") in {sid, None}
            and refs
            and all(ref and ref not in used for ref in refs)
        ):
            return [card["id"]]
    return []


def validate_claims(claims, cards, sid):
    """Arbitrary paraphrases are not automatically certified by a valid source ID."""
    known = {c["id"]: c for c in cards if c.get("shareable") and c.get("supplier_id") in {sid, None}}
    if not isinstance(claims, list) or len(claims) > 2:
        raise ValueError("Select at most two supported claims")
    approved = []
    for claim in claims:
        if not isinstance(claim, dict) or claim.get("evidence_id") not in known:
            raise ValueError("Unknown, private or wrong-supplier evidence")
        card = known[claim["evidence_id"]]
        text = claim.get("text", "")
        if not isinstance(text, str) or not text or text != card["text"]:
            raise ValueError("Claim must use the complete approved excerpt without changing its qualifications")
        approved.append(card)
    return approved


def compose_email(data, cards, sid, strategy="balanced"):
    opening = data.get("opening", "warm")
    if opening not in OPENINGS:
        raise ValueError("Unknown opening style")
    default_question = {"cash_exchange": "cash", "continuity": "continuity", "trade_terms": "priorities"}.get(
        strategy, "costs"
    )
    question = data.get("question", default_question)
    if question not in QUESTIONS:
        raise ValueError("Question must identify a supported diagnostic act")
    selected = data.get("evidence_ids", [])
    if not isinstance(selected, list) or len(selected) > 2 or any(not isinstance(i, str) for i in selected):
        raise ValueError("Select at most two evidence IDs")
    if len(set(selected)) != len(selected):
        raise ValueError("Evidence IDs must be distinct")
    by_id = {c["id"]: c for c in cards}
    claims = [{"evidence_id": i, "text": by_id.get(i, {}).get("text", "")} for i in selected]
    approved = validate_claims(claims, cards, sid)
    body = [OPENINGS[opening]]
    for card in approved:
        refs = card.get("source_ids") or [card["source_id"]]
        if card["kind"] == "computed_index":
            body.append(card["text"])
            body.append(
                "This driver alone does not establish your total cost. "
                "Please reconcile it with the other cost movements."
            )
        else:
            body.append(f"Your supplied document states: {card['text']} ({', '.join(refs)}).")
    body.extend([QUESTIONS[question], "Any movement is conditional on the executable terms below."])
    return "\n\n".join(body)


def safe_excerpt(text):
    paragraphs = re.split(r"\n\s*\n", text)
    return next(
        (
            p.strip()
            for p in paragraphs
            if 30 <= len(p.strip()) <= 450
            and not p.lstrip().startswith("#")
            and " - " not in p[:80]
            and p.strip()[-1] in '.!?"”'
        ),
        None,
    )
