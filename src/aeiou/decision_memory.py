"""Per-run decision summaries and deterministic economics; never a model scratchpad."""

import copy
import math
import re

from arena_kit.cost import closing_terms, cost_breakdown, must_haves_ok
from arena_kit.docs import standard_terms
from arena_kit.play import latest, live_threads

from .supplier_signals import annual_cost_claims

ASSESSMENT_VERSION = "supplier-assessment-v2-source-labels"
ASSESSMENT_TRANSPORT = """

<decision_summary_contract>
Alongside the action, return a short evidence-based assessment of the selected supplier's
latest reply. Use conclusions only, never private chain of thought or a scratchpad. This is
internal decision support, not part of the supplier email. Keep following the editable strategy.
decision_context contains previous assessments, a binding-offer ledger, source refs
and code-calculated economics. Previous assessments are fallible; update or discard them when
new evidence contradicts them. Historical offers are NOT necessarily still available: only
protocol_state determines legal acceptance. Inactive or must-have-invalid offers are not live
alternatives. There is no learning or shared memory across separate test runs.

Include these fields in the same action JSON (also when accepting):
"assessment": {
  "observed_message": "message:sup:EXAMPLE:0",
  "changes": ["Briefly state what changed or explicitly stayed unchanged."],
  "evidence": [{"claim":"A concise relevant conclusion", "kind":"supplier_claim",
    "source_refs":["message:sup:EXAMPLE:0"], "confidence":"medium"}],
  "open_questions": ["Relevant unresolved question, or an empty list if none."],
  "response_to_last_move": "What the supplier returned for our last proposal; no causal certainty.",
  "adaptation": "What the response changes in our approach, or why to keep it.",
  "next_move": "Brief intended action consistent with the strategy."
},
"numeric_claims": []

Use observed_message from decision_context.suppliers[selected supplier].latest_message_ref.
Evidence kinds: public_record (case/research/calculation source), supplier_claim (supplier
message), inference (a hypothesis citing its supporting sources, never certain knowledge).
Source existence is not proof that a claim is true. Distinguish supplier assertions from verified
records; acknowledge uncertainty and open questions. A cost-driver percentage applies only to
its stated share, not to the entire unit price. Check claims against the supplied evidence before
using them. Retain still-relevant evidence in the updated assessment; it replaces the prior one.
Maximum 5 changes, 8 evidence items and 5 questions; each text at most 400 characters.
Code may conservatively correct an internal evidence label from the source registry: supplier
sources remain supplier_claim and mixed/derived sources become inference. This never verifies
the claim's wording, changes your email or terms, or makes an unread source available.

For each derived economic number asserted in the outgoing email, add a numeric_claim:
{"calculation_id":"an ID from decision_context.calculations", "value":123.45,
 "quote":"exact short substring of the email containing that number"}.
Use those supplied calculations rather than doing arithmetic in prose. If the required calculation
is unavailable, omit the derived number or ask for clarification. Proposed prices/terms and literal
source quotations are not derived claims. Checks validate the declared arithmetic and references,
not the truth or meaning of every sentence. Do not describe them as universal fact checking.
Economics use the public case formula, excluding unknown private supplier relationship costs.
Positive cost changes cost the buyer more; negative changes improve buyer cost. Cost changes
compare the lowest-cost must-have-valid package in each menu, not a proven causal concession.
</decision_summary_contract>
"""


def message_ref(sid, index):
    return f"message:{sid}:{index}"


def _menu(case, events, message):
    """Only buyer-visible terms; no private supplier floors or post-run scoring inputs."""
    standard = standard_terms(events)
    offers = []
    for sheet in message.get("term_sheets", []):
        terms = sheet.get("terms", sheet)
        try:
            resolved = closing_terms(case, terms, standard)
            breakdown = cost_breakdown(case, resolved)
            cost = round(sum(breakdown.values()), 2)
            valid = must_haves_ok(case, resolved) and math.isfinite(cost)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        offers.append({"terms": resolved, "cost": cost if math.isfinite(cost) else None,
                       "must_haves_ok": valid,
                       "term_sheet_id": sheet.get("term_sheet_id"),
                       "breakdown": {k: round(v, 2) for k, v in breakdown.items()}})
    eligible = [o for o in offers if o["must_haves_ok"]]
    return offers, min(eligible, key=lambda o: o["cost"]) if eligible else None


def _change(history):
    if len(history) < 2:
        return None
    previous, current = history[-2:]
    before, after = previous["best_valid_offer"], current["best_valid_offer"]
    if not before or not after:
        return None
    return {"from_ref": previous["message_ref"], "to_ref": current["message_ref"],
            "cost_delta": round(after["cost"] - before["cost"], 2),
            "term_changes": [{"term": k, "before": before["terms"].get(k), "after": v}
                             for k, v in after["terms"].items() if before["terms"].get(k) != v]}


def build_context(case, threads, intelligence, memory):
    """Rebuild observed economics from the full transcript on every decision."""
    sources = {"case": {"kind": "public_record", "description": "Buyer case and constraints"}}
    for ref in intelligence.get("facts", {}):
        sources[ref] = {"kind": "public_record", "description": "Supplied research record"}
    for doc in intelligence.get("documents", []):
        sources[doc["id"]] = {"kind": "public_record", "description": "Supplied document"}
    calculations, suppliers = {}, {}

    def calculation(ref, value, description, refs, kind="public_record"):
        if value is None or not math.isfinite(value):
            return
        calculations[ref] = {"value": round(value, 4), "description": description, "source_refs": refs}
        sources[ref] = {"kind": kind, "description": description}

    for i, index in enumerate(intelligence.get("indices", [])):
        refs = index["refs"]
        for key in ("change_pct", "weighted_change_pct"):
            calculation(f"index:{i}:{key}", index[key], f"{index['series']}: {key}; weight {index['weight']}", refs)

    for item in intelligence.get("calculations", []):
        refs = item["source_refs"]
        if not refs or any(ref not in sources for ref in refs):
            raise ValueError(f"Research calculation {item['id']} refers to an unavailable source")
        calculation(item["id"], item["value"], item["description"], refs)

    for sid, events in threads.items():
        history = []
        for index, message in enumerate(events):
            ref = message_ref(sid, index)
            buyer = message.get("from") == "buyer"
            sources[ref] = {"kind": "buyer_message" if buyer else "supplier_claim",
                            "description": f"{'Buyer' if buyer else sid} message {index + 1}"}
            if not message.get("term_sheets"):
                continue
            # Use only standards known at that point, never later attachments.
            _, best = _menu(case, events[:index + 1], message)
            history.append({"message_ref": ref, "party": "buyer" if buyer else "supplier",
                            "best_valid_offer": ({k: best[k] for k in ("terms", "cost", "term_sheet_id")}
                                                 if best else None)})
            if best:
                calculation(f"cost:{sid}:{index}", best["cost"], "Lowest valid menu cost in EUR", [ref, "case"])
        supplier_history = [row for row in history if row["party"] == "supplier"]
        buyer_history = [row for row in history if row["party"] == "buyer"]
        latest_index = next((i for i in range(len(events) - 1, -1, -1)
                             if events[i].get("from") != "buyer"), None)
        current = latest(events) if latest_index is not None else {}
        _, current_best = _menu(case, events, current)
        live = sid in live_threads(threads)
        data = {"latest_message_ref": message_ref(sid, latest_index) if latest_index is not None else None,
                "status": current.get("status"), "live": live,
                "current_best_valid_offer": current_best if live else None,
                "supplier_change": _change(supplier_history), "buyer_change": _change(buyer_history),
                "offer_history": history, "previous_assessment": copy.deepcopy(memory.get(sid))}
        for party in ("supplier", "buyer"):
            change = data[f"{party}_change"]
            if change:
                calculation(f"delta:{sid}:{party}", change["cost_delta"], f"Latest {party} menu cost change in EUR",
                            [change["from_ref"], change["to_ref"], "case"])
        suppliers[sid] = data
        for i, message in reversed(list(enumerate(events))):
            if message.get("from") == "buyer":
                continue
            claims = annual_cost_claims(message.get("body", ""))
            if not claims:
                continue
            share_sum = sum(c["stated_cost_share_pct"] for c in claims)
            data["stated_annual_cost_drivers"] = {"source_ref": message_ref(sid, i), "components": claims,
                                                 "stated_share_sum_pct": share_sum}
            if share_sum <= 100:
                calc_ref = f"weighted-claims:{sid}:{i}"
                description = ("Conditional contribution using the supplier's stated shares as non-overlapping "
                               "base-period weights. Not a verified total-cost increase: timing, overlap, "
                               "remaining components and prior price recovery are unknown.")
                calculation(calc_ref, sum(c["conditional_contribution_pp"] for c in claims), description,
                            [message_ref(sid, i)], kind="inference")
                data["stated_annual_cost_drivers"]["conditional_calculation_id"] = calc_ref
            break
    return {"version": ASSESSMENT_VERSION, "suppliers": suppliers, "sources": sources,
            "calculations": calculations,
            "cost_basis": "Public case formula; excludes unknown private supplier relationship costs."}


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 400:
        raise ValueError(f"assessment.{field} must be nonempty text of at most 400 characters")
    return value


def normalize_evidence_labels(action, context):
    """Repair internal provenance labels only, without promoting anything to verified evidence."""
    result, repairs = copy.deepcopy(action), []
    assessment = result.get("assessment")
    evidence = assessment.get("evidence") if isinstance(assessment, dict) else None
    if not isinstance(evidence, list):
        return result, repairs
    for index, item in enumerate(evidence[:8]):
        if not isinstance(item, dict) or item.get("kind") not in {"public_record", "supplier_claim"}:
            continue
        refs = item.get("source_refs")
        if (not isinstance(refs, list) or not 1 <= len(refs) <= 8 or any(
                not isinstance(ref, str) or ref not in context["sources"]
                or context["sources"][ref].get("available") is False for ref in refs)):
            continue
        kinds = {context["sources"][ref]["kind"] for ref in refs}
        original = item["kind"]
        if kinds == {original}:
            continue
        item["kind"] = "supplier_claim" if kinds == {"supplier_claim"} else "inference"
        if item["kind"] == "inference" and item.get("confidence") == "high":
            item["confidence"] = "medium"
        repairs.append({"evidence_index": index, "source_refs": list(refs),
                        "declared_kind": original, "normalized_kind": item["kind"],
                        "reason": "Source-registry classification only; claim wording is not verified."})
    return result, repairs


def evidence_diagnostic(action, context):
    """Bounded rejected evidence metadata; excludes private preparation and raw model output."""
    assessment = action.get("assessment") if isinstance(action, dict) else None
    evidence = assessment.get("evidence") if isinstance(assessment, dict) else None
    if not isinstance(evidence, list):
        return []
    result = []
    for index, item in enumerate(evidence[:8]):
        if not isinstance(item, dict):
            continue
        refs = item.get("source_refs")
        result.append({"evidence_index": index, "claim": str(item.get("claim", ""))[:400],
                       "declared_kind": str(item.get("kind", ""))[:40],
                       "sources": [{"ref": str(ref)[:160],
                                    **(context["sources"].get(ref, {"status": "unknown"})
                                       if isinstance(ref, str) else {"status": "invalid"})}
                                   for ref in (refs[:8] if isinstance(refs, list) else [])]})
    return result


def validate_assessment(action, context):
    """Validate source references, freshness and declared arithmetic, not free-text truth."""
    sid = action.get("supplier_id")
    if sid not in context["suppliers"]:
        raise ValueError("Assessment must select a known supplier")
    assessment = action.get("assessment")
    if not isinstance(assessment, dict):
        raise ValueError("Include assessment and numeric_claims alongside the action")
    if assessment.get("observed_message") != context["suppliers"][sid]["latest_message_ref"]:
        raise ValueError("assessment.observed_message must reference the selected supplier's latest reply")
    result = {"observed_message": assessment["observed_message"]}
    for key, limit in (("changes", 5), ("open_questions", 5)):
        values = assessment.get(key)
        if not isinstance(values, list) or len(values) > limit or (key == "changes" and not values):
            raise ValueError(f"assessment.{key} must be a list of at most {limit} items")
        result[key] = [_text(v, key) for v in values]
    for key in ("response_to_last_move", "adaptation", "next_move"):
        result[key] = _text(assessment.get(key), key)
    evidence = assessment.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 8:
        raise ValueError("assessment.evidence must contain 1 to 8 grounded conclusions")
    result["evidence"] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ValueError("Each evidence item must be an object")
        kind, refs = item.get("kind"), item.get("source_refs")
        if kind not in {"public_record", "supplier_claim", "inference"}:
            raise ValueError("Evidence kind must be public_record, supplier_claim or inference")
        if (not isinstance(refs, list) or not 1 <= len(refs) <= 8
                or any(not isinstance(ref, str) or ref not in context["sources"] for ref in refs)):
            raise ValueError(f"Evidence item {index} must cite existing source_refs from decision_context.sources; "
                             f"received {str(refs)[:400]}")
        deferred = [ref for ref in refs if context["sources"][ref].get("available") is False]
        if deferred:
            raise ValueError(f"Evidence item {index} is deferred: {', '.join(deferred)}. "
                             "Use retrieve_evidence before citing those sources, or omit that claim")
        if kind != "inference" and any(context["sources"][ref]["kind"] != kind for ref in refs):
            actual = {ref: context["sources"][ref]["kind"] for ref in refs}
            raise ValueError(f"Evidence kind must match its sources in item {index}: "
                             f"declared {kind}, source kinds {actual}; mixed sources require inference")
        if item.get("confidence") not in {"low", "medium", "high"}:
            raise ValueError("Evidence confidence must be low, medium or high")
        result["evidence"].append({"claim": _text(item.get("claim"), "evidence.claim"),
                                   "kind": kind, "source_refs": refs, "confidence": item["confidence"]})
    if not any(result["observed_message"] in item["source_refs"] for item in result["evidence"]):
        raise ValueError("Ground at least one assessment conclusion in the latest supplier reply")
    claims = action.get("numeric_claims")
    if not isinstance(claims, list) or len(claims) > 12:
        raise ValueError("numeric_claims must be a list of at most 12 declared calculations")
    checks = []
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("Each numeric claim must be an object")
        calc_id, value, quote = claim.get("calculation_id"), claim.get("value"), claim.get("quote")
        calc = context["calculations"].get(calc_id) if isinstance(calc_id, str) else None
        if not calc or type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("Numeric claims require an existing calculation_id and a finite numeric value")
        if not math.isclose(value, calc["value"], abs_tol=0.011, rel_tol=0):
            raise ValueError(f"Numeric claim {calc_id} is {value}; computed value is {calc['value']}")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 400 or quote not in action.get("body", ""):
            raise ValueError("Numeric claim quote must be an exact email substring, at most 400 characters")
        numbers = [float(n.replace(",", "")) for n in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", quote)]
        if not any(math.isclose(n, value, abs_tol=0.011, rel_tol=0) for n in numbers):
            raise ValueError("Numeric claim quote must contain its declared number (decimal point, optional commas)")
        checks.append({"calculation_id": calc_id, "value": value, "quote": quote, **calc})
    return result, checks
