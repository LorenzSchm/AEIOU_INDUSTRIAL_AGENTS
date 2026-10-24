"""Compose a controlled strategic act using approved, verbatim evidence cards."""

import re
from datetime import date


def material_cover_evidence(cards, case, events, sid):
    """Prioritize a supplier's dated material cover when opening against inflation.

    Only a cover period overlapping the new contract qualifies. Quote the exact
    source, without claiming it covers the whole contract or all supplier costs.
    """
    if (
        case["buyer"].get("category_goal") != "leverage"
        or "index_clause" not in case["negotiable"]
        or any(m.get("from") == "buyer" for m in events)
    ):
        return None
    reply = events[-1].get("body", "") if events else ""
    if not re.search(r"\b(?:rose|risen|increased|rising|increase)\b", reply, re.I):
        return None
    start = case.get("fixed_terms", {}).get("effective_date")
    if not start:
        return None
    months = "January February March April May June July August September October November December".split()
    for card in cards:
        if not card.get("shareable") or card.get("supplier_id") != sid or card.get("kind") != "verbatim_source":
            continue
        text = card.get("text", "")
        if not re.search(r"(?:material|nickel|alloy|steel|energy|copper|alumin)", text, re.I):
            continue
        match = re.search(r"(?:covered|hedged|secured) at fixed prices until (\w+) (\d{4})\b", text, re.I)
        if not match or match[1].capitalize() not in months:
            continue
        # Conservative: require the first day of the named month to overlap,
        # rather than guessing a later expiry date within that month.
        until = date(int(match[2]), months.index(match[1].capitalize()) + 1, 1)
        if date.fromisoformat(start) <= until:
            return card["id"]
    return None


OPENINGS = {
    "warm": "Thank you for your offer. We would like to find terms that work well for both sides.",
    "concise": "Thank you for your response. Please review the complete conditional packages below.",
    "firm": "We can move conditionally on a complete package that works for both parties.",
}
QUESTIONS = {
    "priorities": "Which package best fits your operating needs, and which term prevents agreement?",
    "cash": "Would earlier payment allow a reciprocal improvement in the complete package?",
    "continuity": "Would a longer commitment support better pricing or delivery terms?",
    "authority": "Which complete package could you approve, and would any part need a further review?",
    "costs": "Could you share a cost breakdown and explain which drivers affect this quotation?",
    "delivery": "What delivery flexibility would allow you to improve the overall package?",
    "quality": "What quality commitment and complaint compensation could you approve together?",
    "tradeoff": ("Could you review the complete options below and help us identify a workable exchange?"),
    "exchange": ("Which of these complete packages would work best for you, and what would need to change?"),
    "settlement": (
        "One option builds on your quoted conditions with a reciprocal improvement. "
        "Could you approve that complete package?"
    ),
    "price_response": (
        "We have considered your feedback. What adjustment to the complete package would help us move forward?"
    ),
}

COMMERCIAL_CONTEXT = {
    "date_transition": (
        "The later-start option treats the transition to the new price as part of the exchange. "
        "Please consider its price and payment terms together when reviewing what works for your business.",
        "Which complete price-and-start-date combination could you support?",
    ),
    "payment_review": (
        "For your internal review, the alternatives below set out payment terms alongside the complete "
        "price and contract commitment. We recognize the pricing restriction you described; "
        "we are asking you to assess a contract-term exchange within that restriction.",
        "Could you review a payment-term adjustment within one of these packages "
        "and identify the terms you could approve?",
    ),
    "continuity_review": (
        "The longer-duration option offers a longer contractual commitment at the stated annual quantity. "
        "This is part of that complete package, with no additional volume commitment outside its terms.",
        "Which complete package could you approve in exchange for that longer commitment?",
    ),
}


def commercial_context(case, events, terms, signals):
    """Explain only executable date/duration trades; never infer an extra promise."""
    from arena_kit.cost import closing_terms
    from arena_kit.docs import standard_terms

    latest = next((m for m in reversed(events) if m.get("from") != "buyer"), {})
    sheets = latest.get("term_sheets", [])
    if latest.get("status") != "open" or not sheets or not terms:
        return None
    std = standard_terms(events)
    quoted = [closing_terms(case, s.get("terms", {}), std) for s in sheets]
    proposed = [closing_terms(case, t, std) for t in terms]
    fields = case["negotiable"]
    # Require improvement over every live option, not merely the first sheet.
    dates = [t.get("effective_date") for t in quoted]
    if "effective_date" in fields and all(isinstance(d, str) for d in dates):
        if any(isinstance(t.get("effective_date"), str) and t["effective_date"] > max(dates) for t in proposed):
            return "date_transition"
    if "contract_months" in fields:
        if "price_freeze" in signals and "payment_days" in fields:
            if any(t["payment_days"] not in {q["payment_days"] for q in quoted} for t in proposed):
                return "payment_review"
        if any(
            t["contract_months"] > max(q["contract_months"] for q in quoted)
            and all(t["volume_per_year"] == q["volume_per_year"] for q in quoted)
            for t in proposed
        ):
            return "continuity_review"
    return None


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


def evidence_requested(events):
    """Positive index cards require a specific index request, not a generic challenge."""
    reply = next((m.get("body", "") for m in reversed(events) if m.get("from") != "buyer"), "")
    return any(
        re.search(
            r"(?:support|substantiate|verify|back up|provide|share|cite).{0,80}\b(?:index|indices|benchmarks?)\b",
            sentence,
            re.I,
        )
        and not re.search(r"(?:do not|don't|no need to).{0,20}(?:provide|share|cite|verify)", sentence, re.I)
        for sentence in re.split(r"[.!?\n]", reply)
    )


def communication_evidence(cards, events):
    """Keep inflation in research; do not volunteer it as a standalone discount claim."""
    if evidence_requested(events):
        return cards
    return [c for c in cards if not (c.get("kind") == "computed_index" and (c.get("change_pct") or 0) > 0)]


def responsive_act(
    data,
    events,
    feedback_probe=False,
    exchange_probe=False,
    closing_probe=False,
    interpretations=(),
    portfolio_issue=None,
    case=None,
    terms=(),
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
    supplier_messages = [m for m in events if m.get("from") != "buyer"]
    reply = supplier_messages[-1].get("body", "") if supplier_messages else ""
    refused_costs = any(
        re.search(
            r"(?:do not|don't|not to|cannot|won't).{0,35}share.{0,50}(?:cost|breakdown|calculation)|"
            r"(?:cost|breakdown|calculation).{0,60}(?:stay\w* internal|confidential|not.{0,12}shar)|"
            r"more detail we do not share",
            m.get("body", ""),
            re.I,
        )
        for m in supplier_messages
    )
    data["acknowledgment"] = "review" if any(m.get("from") == "buyer" for m in events) else "initial"
    if re.search(r"long[- ]standing|partnership|cooperation", reply, re.I):
        data["acknowledgment"] = "relationship"
    if refused_costs:
        data["acknowledgment"] = "confidential"
    if "price_freeze" in signals:
        data["acknowledgment"] = "price_freeze"
        data["question"] = "exchange"
    elif "closing_warning" in signals or "approval_required" in signals:
        data["question"] = "authority"
    elif closing_probe:
        data["question"] = "settlement"
    elif feedback_probe:
        data["question"] = "tradeoff"
    elif portfolio_issue in {"quality", "index", "delivery", "delivery_flex"}:
        data["question"] = {
            "quality": "quality",
            "index": "exchange",
            "delivery": "delivery",
            "delivery_flex": "delivery",
        }[portfolio_issue]
    elif "price_resistance" in signals and not feedback_probe:
        data["question"] = "price_response"
    elif exchange_probe and not feedback_probe:
        data["question"] = "exchange"
    elif data.get("question", "costs") == "costs":
        prior = " ".join(m.get("body", "").lower() for m in events if m.get("from") == "buyer")
        if refused_costs:
            data["question"] = "exchange"
        elif "cost breakdown" in prior:
            data["question"] = "authority" if QUESTIONS["priorities"].lower() in prior else "priorities"
    if case and data.get("question") == "continuity" and "contract_months" not in case["negotiable"]:
        data["question"] = "exchange"
    # These fields are code-owned, even if a model supplies values with the same names.
    data["commercial_context"] = commercial_context(case, events, terms, signals) if case and terms else None
    prior = " ".join(m.get("body", "") for m in events if m.get("from") == "buyer")
    context = COMMERCIAL_CONTEXT.get(data["commercial_context"])
    data["repeat_context"] = bool(context and context[0] in prior)
    return data


def package_rationale(terms):
    """Explain only trades actually present in the outgoing executable packages."""
    explanations = []
    indexed = [t for t in terms if isinstance(t.get("index_clause"), dict)]
    for a in indexed:
        for b in indexed:
            ac, bc = a["index_clause"], b["index_clause"]
            if (
                ac.get("deadband_pct", 0) < bc.get("deadband_pct", 0)
                and a["unit_price"] < b["unit_price"]
                and {k: v for k, v in ac.items() if k != "deadband_pct"}
                == {k: v for k, v in bc.items() if k != "deadband_pct"}
                and {k: v for k, v in a.items() if k not in {"unit_price", "index_clause"}}
                == {k: v for k, v in b.items() if k not in {"unit_price", "index_clause"}}
            ):
                explanations.append(
                    "The lower-deadband alternative passes through more index movement "
                    "in exchange for a lower base price, with the same payment and contract duration. "
                    "Please price that risk transfer as a complete exchange."
                )
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
    requested_evidence = evidence_requested(events)
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
            # Retain it for internal analysis and explicit supplier requests.
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
    acknowledgment = data.get("acknowledgment", "initial")
    context = {
        "initial": "",
        "review": "",
        "relationship": "We appreciate the cooperation and would like to plan the next agreement together.",
        "confidential": (
            "We respect that your detailed cost calculation is confidential. Let us work with the terms you can offer."
        ),
        "price_freeze": (
            "You have explained the restriction on price changes. "
            "Let us focus on a workable exchange of contract terms."
        ),
    }
    if acknowledgment not in context:
        raise ValueError("Unknown acknowledgment")
    greeting = OPENINGS[opening]
    if opening == "warm" and acknowledgment != "initial":
        greeting = "Thank you for considering our proposal and explaining your position."
    body = [greeting]
    if context[acknowledgment]:
        body.append(context[acknowledgment])
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
    context_key = data.get("commercial_context")
    if context_key is not None and context_key not in COMMERCIAL_CONTEXT:
        raise ValueError("Unknown commercial context")
    if context_key:
        brief, diagnostic = COMMERCIAL_CONTEXT[context_key]
        if not data.get("repeat_context"):
            body.append(brief)
    else:
        diagnostic = QUESTIONS[question]
    body.extend([diagnostic, "Any movement is conditional on the executable terms below."])
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
