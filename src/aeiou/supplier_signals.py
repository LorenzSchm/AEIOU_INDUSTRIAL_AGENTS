"""Evidence-linked language signals, not psychological labels or withdrawal probabilities."""

import re

from arena_kit.play import live_threads, moves_left

PATTERNS = {
    "courtesy": r"thank you|appreciate|pleasure working|partnership|vielen dank",
    "final_language": r"\bfinal offer\b|letztes angebot|please decide|letzte[sr]? angebot",
    "withdrawal_language": r"we (?:must .*? )?(?:are withdrawing|withdraw)|walk away|zurückziehen",
    "fixed_price_refused": r"(?:cannot|unable to|not able to).{0,70}(?:fixed price|fixed-price)",
    "adjustment_required": r"(?:must|essential|require|maintain).{0,80}(?:index|adjustment)|"
                           r"(?:index|adjustment).{0,80}(?:essential|required)",
    "base_cost_objection": r"processing|operational costs|base price|grundpreis|bearbeitung",
    "limited_disclosure": r"more detail we do not share|confidential|vertraulich",
    "approval_required": r"(?:requires?|needs?|subject to|awaiting|pending).{0,50}"
                         r"(?:approval|authori[sz]ation|pricing committee)|"
                         r"(?:approval|freigabe).{0,20}(?:required|needed|erforderlich)|"
                         r"(?:committee|owner).{0,30}(?:must|needs? to).{0,20}approve|"
                         r"authori[sz]ed.{0,35}(?:only|up to)",
    "authority_confirmed": r"(?:I am|we are)\s+fully authori[sz]ed|"
                           r"(?:committee|owner)\s+(?:has|have)\s+"
                           r"(?:reviewed[^.!?\n]{0,60}\s+and\s+)?(?:authori[sz]ed|approved)",
}


def annual_cost_claims(body):
    """Extract only explicitly stated rate/share pairs; never infer supplier cost shares."""
    pattern = (r"\b([A-Z][A-Z0-9-]+)\s+rose\s+([\d.]+)\s*%\s+in\s+(?:twelve|12)\s+months"
               r"\s+and\s+is\s+([\d.]+)\s*%\s+of\s+our\s+cost")
    items = []
    for series, rate, share in re.findall(pattern, body):
        try:
            rate, share = float(rate), float(share)
        except ValueError:
            continue
        if 0 <= share <= 100 and 0 <= rate <= 1000 and series not in {item["series"] for item in items}:
            items.append({"series": series, "annual_change_pct": rate, "stated_cost_share_pct": share,
                          "conditional_contribution_pp": round(rate * share / 100, 4)})
    return items


def supplier_signals(case, threads):
    result = {}
    for sid, events in threads.items():
        supplier_messages = [(i, m) for i, m in enumerate(events) if m.get("from") != "buyer"]
        if not supplier_messages:
            continue
        last_i, message = supplier_messages[-1]
        body = message.get("body", "")
        evidence = []
        for name, pattern in PATTERNS.items():
            if match := re.search(pattern, body, re.I):
                prefix = body[max(0, match.start() - 20):match.start()]
                if name == "final_language" and re.search(r"\b(?:not|isn't)\s+(?:our\s+|a\s+)?$", prefix, re.I):
                    continue
                evidence.append({"signal": name, "source_ref": f"message:{sid}:{last_i}",
                                 "quote": body[max(0, match.start() - 30):match.end() + 100]})
        names = {e["signal"] for e in evidence}
        live = sid in live_threads(threads)
        final = message.get("status") == "final_offer" or "final_language" in names
        # Compare primary offers across supplier messages, never alternatives within one menu.
        asks = []
        for i, item in supplier_messages:
            sheets = item.get("term_sheets") or []
            price = sheets[0].get("terms", {}).get("unit_price") if sheets else None
            if isinstance(price, (int, float)) and not isinstance(price, bool):
                asks.append({"unit_price": price, "source_ref": f"message:{sid}:{i}"})
        signals = {
            "live": live, "status": message.get("status"),
            "tone": "courteous" if "courtesy" in names else "unspecified",
            "stance": "terminal" if not live else "final" if final else "negotiating",
            "authority": ("approval_required_or_limited" if "approval_required" in names else
                          "supplier_confirmed" if "authority_confirmed" in names else "unspecified"),
            "withdrawal_risk_signal": "terminal" if not live else "high" if final else "unknown",
            "risk_basis": "Explicit finality or terminal status; qualitative signal, not a calibrated probability.",
            "rounds_left": max(0, moves_left(case, events)),
            "rounds_note": "Remaining API sends are not a promise that the supplier will continue.",
            "evidence": evidence, "primary_price_history": asks,
            "latest_primary_price_movement": round(asks[-1]["unit_price"] - asks[-2]["unit_price"], 4)
            if len(asks) > 1 else None,
            "live_objections": sorted(names & {"fixed_price_refused", "adjustment_required", "base_cost_objection",
                                                "limited_disclosure", "approval_required"}),
            "latest_stated_cost_drivers": annual_cost_claims(body),
            "previously_cited_refs": sorted({ref for m in events if m.get("from") == "buyer"
                                            for ref in re.findall(r"(?:po|idx|ctr|sc|quote|inv):[\w@.\-#]+",
                                                                  m.get("body", ""))}),
            "supplier_questions": [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.endswith("?")][:3],
        }
        if "courtesy" in names and final:
            signals["interpretation"] = "Polite wording coexists with a final position; it does not lower the risk."
        result[sid] = signals
    return result
