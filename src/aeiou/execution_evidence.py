"""Attach opted-in public index calculations to deterministic P18 proposals."""

import copy
import math

from .execution_policy import controlled_source
from .package_sequence import stage_from_action

PO_RECORDS = {
    "po:4500014022-10": ("2026-06-02", 5.26),
    "po:4500014078-10": ("2026-06-17", 5.26),
    "po:4500014355-10": ("2026-09-01", 5.84),
    "po:4500014427-10": ("2026-09-18", 5.84),
}


def add_paragraph(action, paragraph, claim, refs):
    enriched = copy.deepcopy(action)
    first, separator, rest = enriched["body"].partition("\n\n")
    if stage_from_action(action):
        intro, separator, rest = rest.partition("\n\n")
        first += "\n\n" + intro
    enriched["body"] = first + "\n\n" + paragraph + separator + rest
    enriched["assessment"]["evidence"].append({
        "claim": claim, "kind": "public_record", "source_refs": refs, "confidence": "high"})
    return enriched


def opening_po_evidence(action, payload, events):
    refs = list(PO_RECORDS)
    audit = {"argument_id": "po_price_history", "source_refs": refs, "status": "skipped"}
    if any(m.get("from") == "buyer" and any(ref in m.get("body", "") for ref in refs) for m in events):
        return action, {**audit, "reason": "already_sent"}
    sources = payload.get("decision_context", {}).get("sources", {})
    facts = payload.get("research", {}).get("facts", {})
    if any(ref not in facts or sources.get(ref, {}).get("available") is not True
           or sources.get(ref, {}).get("kind") != "public_record" for ref in refs):
        return action, {**audit, "reason": "sources_not_delivered"}
    for ref, (date, price) in PO_RECORDS.items():
        record = facts[ref]
        if (not isinstance(record, dict) or record.get("date") != date
                or record.get("part") != "part:370010" or record.get("supplier") != "sup:70740"
                or type(record.get("price_unit")) not in (int, float) or record["price_unit"] != 1
                or type(record.get("unit_price")) not in (int, float) or record["unit_price"] != price):
            return action, {**audit, "reason": "po_record_mismatch"}
    sentence = (
        "Our purchase orders with you for part:370010 on 2 and 17 June 2026 were at EUR 5.26/kg "
        "(po:4500014022-10, po:4500014078-10), compared with EUR 5.84/kg on 1 and 18 September 2026 "
        "(po:4500014355-10, po:4500014427-10).")
    enriched = add_paragraph(action, sentence, sentence, refs)
    enriched["assessment"]["next_move"] = "Send the opening package with checked literal PO prices once."
    # Literal record values, not a derived change: no numeric_claim calculation is invented.
    return enriched, {**audit, "status": "included"}


def apply_menu_evidence(action, payload, events, policy):
    """Use only delivered records; return bounded audit data even when the claim is skipped."""
    source = controlled_source(action)
    stage = stage_from_action(action)
    if stage:
        sequence = (policy or {}).get("consistent_package_sequence")
        if not sequence or policy.get("case_id") != "case:P18":
            raise ValueError("Sequence evidence requires its saved P18 execution policy")
        if stage == "opening":
            return opening_po_evidence(action, payload, events)
    options = {
        "policy:package-alternatives": ("menu_evidence_argument", "ppi_recent_change_pct", "PPI-C2431", "2026-08"),
        "policy:payment-compromise": ("compromise_evidence_argument", "as_recent_change_pct", "AS-14404", "2026-09"),
        "policy:package-sequence:ppi_counter": (None, "ppi_recent_change_pct", "PPI-C2431", "2026-08"),
        "policy:package-sequence:alloy_close": (None, "as_recent_change_pct", "AS-14404", "2026-09"),
    }
    if source not in options:
        return action, None
    setting, expected, series, end = options[source]
    argument = expected if stage else (policy or {}).get(setting)
    if not argument:
        return action, None
    if argument != expected or policy.get("case_id") != "case:P18":
        raise ValueError("Unsupported execution evidence argument")
    start = "2026-06"
    refs = [f"idx:{series}@{start}", f"idx:{series}@{end}"]
    calc_id = f"p18:{argument}"
    audit = {"argument_id": argument, "source_refs": refs, "calculation_id": calc_id, "status": "skipped"}

    def skip(reason):
        return action, {**audit, "reason": reason}

    # The sent transcript, rather than mutable in-memory counters, is the ledger.
    # Match a cited argument or its series and ISO window in a buyer paraphrase.
    for message in events:
        body = message.get("body", "")
        if message.get("from") == "buyer" and (
                all(ref in body for ref in refs) or all(token in body for token in (series, start, end))):
            return skip("already_sent")

    context = payload.get("decision_context") or {}
    sources = context.get("sources", {})
    facts = payload.get("research", {}).get("facts", {})
    if any(ref not in facts or sources.get(ref, {}).get("available") is not True
           or sources.get(ref, {}).get("kind") != "public_record" for ref in refs):
        return skip("sources_not_delivered")
    records = [facts[ref] for ref in refs]
    for record, month in zip(records, (start, end), strict=True):
        if (not isinstance(record, dict) or record.get("series") != series or record.get("month") != month
                or type(record.get("value")) not in (int, float) or not math.isfinite(record["value"])
                or record["value"] <= 0):
            return skip("invalid_index_records")
    change = (records[1]["value"] / records[0]["value"] - 1) * 100
    calc = context.get("calculations", {}).get(calc_id)
    if (not isinstance(calc, dict) or calc.get("source_refs") != refs
            or type(calc.get("value")) not in (int, float) or not math.isfinite(calc["value"])
            or not math.isclose(change, calc["value"], abs_tol=.0001, rel_tol=0)):
        return skip("calculation_mismatch")
    if change >= 0:
        return skip("window_is_not_a_decline")

    quote = f"changed by {change:+.2f}%"
    sentence = f"{series} {quote} from {start} to {end} ({', '.join(refs)})."
    paragraph = (
        f"For your review, {sentence} We recognise that this recent movement can coexist with the annual "
        "increase you described. Could you take it into account when reviewing the starting price?"
    )
    enriched = add_paragraph(action, paragraph, sentence, refs)
    enriched["numeric_claims"].append({"calculation_id": calc_id, "value": round(change, 2), "quote": quote})
    if stage:
        enriched["assessment"]["adaptation"] = (
            "Keep the complete package structure consistent and add this checked, unused index window; "
            "verification is not proof of supplier acceptance or a known minimum price.")
        enriched["assessment"]["next_move"] = f"Send the {stage} package once with checked {series} evidence."
    elif source == "policy:package-alternatives":
        enriched["assessment"]["adaptation"] = (
            "Add the previously unused recent PPI window to the authorized menu; "
            "it does not establish the supplier's total cost change or minimum price.")
        enriched["assessment"]["next_move"] = "Send the qualifying menu with the checked PPI calculation once."
    else:
        enriched["assessment"]["adaptation"] = (
            "Send the new payment compromise while the supplier remains open and add the checked, unused AS window; "
            "do not reserve it for a final offer or treat it as proof of the supplier's minimum price.")
        enriched["assessment"]["next_move"] = "Send the qualifying payment compromise with checked AS evidence once."
    return enriched, {**audit, "status": "included", "value": round(change, 2), "quote": quote}
