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
    "tradeoff": (
        "One option follows your counteroffer's commercial terms. "
        "What reciprocal price improvement can you authorize for that package?"
    ),
    "exchange": (
        "The alternative trades a commercial term against its quoted price. "
        "Both changes are conditional on accepting the complete package. "
        "Which package can you authorize?"
    ),
    "settlement": (
        "One option builds on your quoted conditions with a reciprocal improvement. "
        "Could you approve that complete package?"
    ),
    "price_response": (
        "We understand that our earlier price was not workable for you. "
        "We have revised the complete options; what reciprocal movement can you authorize?"
    ),
}


def fresh_evidence_cards(cards, threads):
    """Hide already-cited sources from the writer, including new chunks of a used source."""
    from .agent import ID_RE

    used = {
        ref
        for events in threads.values()
        for m in events
        if m.get("from") == "buyer"
        for ref in ID_RE.findall(m.get("body", ""))
    }
    return [c for c in cards if not used.intersection(c.get("source_ids") or [c.get("source_id")])]


def responsive_act(
    data,
    events,
    feedback_probe=False,
    exchange_probe=False,
    closing_probe=False,
    interpretations=(),
    portfolio_issue=None,
):
    """Do not ask repeatedly for a breakdown; ask for the next actionable trade."""
    data = dict(data)
    from .supplier_signals import observed_signals, recent_kinds

    signals = recent_kinds(
        {
            "signals": observed_signals(events) + list(interpretations),
            "last_supplier_message_index": max(
                (i for i, m in enumerate(events) if m.get("from") != "buyer"), default=-1
            ),
        }
    )
    if "closing_warning" in signals or "approval_required" in signals:
        data["question"] = "authority"
    elif closing_probe:
        data["question"] = "settlement"
    elif feedback_probe:
        data["question"] = "tradeoff"
    elif portfolio_issue in {"quality", "index", "delivery"}:
        data["question"] = {"quality": "quality", "index": "exchange", "delivery": "delivery"}[portfolio_issue]
    elif "price_resistance" in signals and not feedback_probe:
        data["question"] = "price_response"
    elif exchange_probe and not feedback_probe:
        data["question"] = "exchange"
    elif data.get("question", "costs") == "costs":
        prior = " ".join(m.get("body", "").lower() for m in events if m.get("from") == "buyer")
        if "cost breakdown" in prior:
            data["question"] = "authority" if QUESTIONS["priorities"].lower() in prior else "priorities"
    return data


def package_rationale(terms):
    """Explain only trades actually present in the outgoing executable packages."""
    explanations = []
    for cheap in terms:
        for dear in terms:
            if cheap["unit_price"] >= dear["unit_price"]:
                continue
            if cheap.get("ppm_commitment", 0) >= dear.get("ppm_commitment", 0) and cheap.get(
                "chargeback_eur_per_complaint", 0
            ) < dear.get("chargeback_eur_per_complaint", 0):
                explanations.append(
                    "The lower-price quality option reduces complaint liability "
                    "while retaining our required quality limit."
                )
            if cheap.get("index_clause") and dear.get("index_clause") is None:
                explanations.append(
                    "The indexed option exchanges a lower base price for the specified sharing of material-price risk."
                )
            if cheap.get("lead_time_days", 0) > dear.get("lead_time_days", 0):
                explanations.append("The higher-price delivery option is conditional on the quoted shorter lead time.")
    return " ".join(dict.fromkeys(explanations))


def fixed_commitment_answer(case, events):
    """Answer a requested volume trade when the contract makes it unavailable."""
    if "volume_per_year" in case["negotiable"] or "volume_per_year" not in case.get("fixed_terms", {}):
        return ""
    reply = next((m.get("body", "") for m in reversed(events) if m.get("from") != "buyer"), "")
    if re.search(r"(?:which|what|increase|jump).{0,40}volume|volume.{0,30}(?:increase|jump)", reply, re.I):
        return (
            "The annual volume is fixed for this enquiry. "
            "Please price the complete alternatives below within that commitment."
        )
    return ""


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
    improving = len(prices) >= 2 and prices[-1] < prices[-2]
    reply = next((m.get("body", "") for m in reversed(events) if m.get("from") != "buyer"), "")
    requested_evidence = re.search(
        r"(?:support|substantiate|verify|back up|provide|share|cite).{0,80}(?:data|evidence|sources|indices)",
        reply,
        re.I,
    )
    used = {
        ref
        for events in threads.values()
        for message in events
        if message.get("from") == "buyer"
        for ref in ID_RE.findall(message.get("body", ""))
    }
    for card in cards:
        if card.get("kind") == "computed_index" and (card.get("change_pct") or 0) > 0 and not requested_evidence:
            # Accurate inflation data may support the seller, not a discount.
            # Retain it for analysis/explicit writer selection, not automatic citation.
            continue
        if improving and card.get("kind") not in {"rival_quote", "contract_notice"}:
            continue
        refs = card.get("source_ids") or [card.get("source_id")]
        if (
            card.get("shareable")
            and card.get("supplier_id") in {sid, None}
            and refs
            and all(ref and (ref not in used or card.get("kind") == "rival_quote") for ref in refs)
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
        if card["kind"] == "rival_quote":
            body.append(card["text"])
        elif card["kind"] == "computed_index":
            body.append(card["text"])
            body.append(
                "This driver alone does not establish your total cost. "
                "Please reconcile it with the other cost movements."
            )
        else:
            body.append(f"The referenced document states: {card['text']} ({', '.join(refs)}).")
    body.extend([QUESTIONS[question], "Any movement is conditional on the executable terms below."])
    return "\n\n".join(body)


def rival_quote_cards(case, threads, intelligence, sid):
    """Only real, currently executable competitor offers from this case/session."""
    from .agent import ID_RE
    from .optimizer import evaluate

    rows = evaluate(case, threads, intelligence)
    own = min((r["cost"] for r in rows if r["supplier_id"] == sid), default=float("inf"))
    used = {ref for m in threads[sid] if m.get("from") == "buyer" for ref in ID_RE.findall(m.get("body", ""))}
    for row in rows:
        ref, t = row["sheet_id"], row["terms"]
        if row["supplier_id"] == sid or not row["acceptable"] or row["cost"] >= own or ref in used:
            continue
        return [
            {
                "id": "rival:" + ref,
                "source_id": ref,
                "supplier_id": sid,
                "shareable": True,
                "kind": "rival_quote",
                "text": (
                    f"Another supplier's current offer ({ref}) is EUR {t['unit_price']:.2f} per unit, "
                    f"payment {t['payment_days']} days, delivery {t['lead_time_days']} days, {t['incoterm']}. "
                    "These are its quoted conditions; can you improve your complete package competitively?"
                ),
            }
        ]
    return []


def safe_excerpt(text):
    paragraphs = re.split(r"\n\s*\n", text)
    return next(
        (
            p.strip()
            for p in paragraphs
            if 30 <= len(p.strip()) <= 450
            and not p.lstrip().startswith("#")
            and not re.match(r"(?:to|from|subject|dear|hello|hallo)\b", p.strip(), re.I)
            and " - " not in p[:80]
            and p.strip()[-1] in '.!?"”'
        ),
        None,
    )
