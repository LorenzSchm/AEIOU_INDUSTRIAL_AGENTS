"""Use dated own-supplier evidence and focus counters on an observed constraint."""

import re
from datetime import date

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms

from .retrieval import evidence_cutoff


def own_quote_cards(case, events, sid, intelligence):
    if (
        case["buyer"].get("category_goal") != "leverage"
        or "index_clause" not in case["negotiable"]
        or len(case["parts"]) != 1
        or any(e.get("from") == "buyer" for e in events)
    ):
        return []
    opening = min(
        (
            s["terms"]["unit_price"]
            for e in events
            for s in e.get("term_sheets", [])
            if s.get("terms", {}).get("unit_price")
        ),
        default=0,
    )
    candidates = []
    for q in intelligence.get("tables", {}).get("quotes", []):
        if (
            q.get("supplier_id") != sid
            or q.get("part_id") != case["parts"][0]
            or q.get("id") not in intelligence.get("facts", {})
            or q.get("price_unit") != 1
            or q.get("currency") != "EUR"
        ):
            continue
        try:
            age = (date.fromisoformat(evidence_cutoff(case)) - date.fromisoformat(q["quote_date"])).days
            eligible = 0 <= age <= 90 and 0 < q["unit_price"] < opening
        except (KeyError, TypeError, ValueError):
            continue
        if eligible and all(q.get(k) is not None for k in ("payment_days", "lead_time_days", "incoterm")):
            candidates.append(q)
    if not candidates:
        return []
    q = max(candidates, key=lambda q: q["quote_date"])
    return [
        {
            "id": "own-quote:" + q["id"],
            "source_id": q["id"],
            "supplier_id": sid,
            "shareable": True,
            "kind": "historical_quote",
            "date": q["quote_date"],
            "text": (
                f"Your quotation dated {q['quote_date']} for this same part ({q['id']}) listed "
                f"EUR {q['unit_price']:.2f} per unit, payment {q['payment_days']} days, "
                f"delivery {q['lead_time_days']} days, {q['incoterm']}. "
                "This is a dated price reference; duration, index and other conditions may differ."
            ),
        }
    ]


def focused_index_terms(case, events, sheets, cost):
    """Select an already-validated outgoing package; never create or reprice one."""
    if (
        case["buyer"].get("category_goal") != "leverage"
        or "index_clause" not in case["negotiable"]
        or not events
        or events[-1].get("status") != "open"
        or not any(e.get("from") == "buyer" for e in events)
    ):
        return None
    suppliers = [e for e in events if e.get("from") != "buyer"]
    required = (
        r"(?:must include|must retain|need to include|we require).{0,45}(?:index|price adjustment)|"
        r"(?:index|price adjustment).{0,45}(?:essential|required|requirement|only viable)|"
        r"fixed price.{0,65}(?:not viable|not possible|cannot|can't)"
    )
    if not any(
        re.search(required, s, re.I) and not re.search(r"(?:not|never)\s+(?:required|essential)", s, re.I)
        for e in suppliers[-2:]
        for s in re.split(r"[.!?\n]", e.get("body", ""))
    ):
        return None
    std = standard_terms(events)
    quoted = [closing_terms(case, s.get("terms", {}), std) for s in suppliers[-1].get("term_sheets", [])]
    if not quoted or any(q.get("index_clause") is None for q in quoted):
        return None
    fields = [f for f in case["negotiable"] if f != "unit_price"]
    eligible = [
        s["terms"]
        for s in sheets
        if s.get("terms", {}).get("index_clause") is not None
        and any(all(closing_terms(case, s["terms"], std).get(f) == q.get(f) for f in fields) for q in quoted)
    ]
    return min(eligible, key=cost) if eligible else None
