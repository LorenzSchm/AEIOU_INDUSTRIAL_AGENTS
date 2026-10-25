"""Opt-in P18 package sequence reconstructed from messages actually sent."""

import math
import re

from arena_kit.cost import total_cost
from arena_kit.docs import standard_terms

from .optimizer import valid_terms

STAGES = {
    "opening": ("po_price_history", "Thank you for your quotation and for considering a longer-term agreement."),
    "ppi_counter": ("ppi_recent_change_pct", "Thank you for reviewing our complete long-term proposal."),
    "alloy_close": ("as_recent_change_pct", "Thank you for your continued review of our proposal."),
}


def stage_from_action(action):
    body = action.get("body", "")
    return next((name for name, (_, intro) in STAGES.items() if body.startswith(f"Dear Mr Seidl,\n\n{intro}")), None)


def sequence_packages(policy):
    sequence = policy["consistent_package_sequence"]
    shared = {k: sequence[k] for k in ("payment_days", "contract_months", "index_clause")}
    return [{**shared, "unit_price": stage["unit_price"]} for stage in sequence["stages"]]


def validate_sequence(policy):
    sequence = policy["consistent_package_sequence"]
    if (
        policy["case_id"] != "case:P18"
        or policy["one_final_counter"]
        or any(
            k in policy
            for k in (
                "early_package_alternatives",
                "open_payment_compromise",
                "menu_evidence_argument",
                "compromise_evidence_argument",
            )
        )
    ):
        raise ValueError("P18 consistent_package_sequence cannot be combined with legacy counter controls")
    if (
        not isinstance(sequence, dict)
        or set(sequence) != {"payment_days", "contract_months", "index_clause", "stages"}
        or any(type(sequence[k]) is not int or sequence[k] <= 0 for k in ("payment_days", "contract_months"))
        or not isinstance(sequence["index_clause"], dict)
    ):
        raise ValueError("consistent_package_sequence requires a complete shared structure")
    stages = sequence["stages"]
    if not isinstance(stages, list) or len(stages) != len(STAGES):
        raise ValueError("consistent_package_sequence requires opening, ppi_counter and alloy_close stages")
    previous = 0
    for stage, (name, (argument, _)) in zip(stages, STAGES.items(), strict=True):
        if (
            not isinstance(stage, dict)
            or set(stage) != {"name", "unit_price", "evidence_argument"}
            or stage["name"] != name
            or stage["evidence_argument"] != argument
            or type(stage["unit_price"]) not in (int, float)
            or not math.isfinite(stage["unit_price"])
            or not previous < stage["unit_price"] <= policy["closing_price"]
        ):
            raise ValueError("Invalid consistent package stage, evidence argument or concession order")
        previous = stage["unit_price"]
    if previous != policy["closing_price"] or not policy.get("stop_after_nonqualifying_final"):
        raise ValueError("The sequence must end at closing_price and retain final-offer stopping")


def validate_case_sequence(case, floor, policy):
    for terms in sequence_packages(policy):
        if (
            not valid_terms(case, terms)
            or terms["contract_months"] < policy["minimum_contract_months"]
            or not floor.allows(total_cost(case, terms), terms)
        ):
            raise ValueError("Consistent package stages must satisfy case options, duration and complete-cost limits")


def structure_restriction(events, days, payment_restriction):
    if cue := payment_restriction(events, days):
        return cue
    # Conservative cues, not a semantic classifier: ambiguous refusals go to the model.
    for message in events:
        if message.get("from") == "buyer":
            continue
        for sentence in re.split(r"[.!?\n]", message.get("body", "")):
            structure = re.search(
                r"deadband|indexation|index clause|contract|months?|laufzeit|gleitklausel", sentence, re.I
            )
            refusal = re.search(
                r"cannot|can't|unable|not (?:possible|acceptable|an option)|reject|refus|"
                r"will not|won't|\bonly\b|nicht|nur",
                sentence,
                re.I,
            )
            if structure and refusal:
                return sentence.strip()
    return None


def sequence_plan(case, threads, state, floor, policy, payment_restriction):
    """Return bounded, visible reasons when automatic progression is inappropriate."""
    if not (policy or {}).get("consistent_package_sequence"):
        return {}
    sequence = policy["consistent_package_sequence"]
    packages = sequence_packages(policy)
    result = {}
    for sid, supplier in state["suppliers"].items():
        events = threads[sid]
        buyers = [m for m in events if m.get("from") == "buyer"]
        item = result[sid] = {"status": "deferred", "sent_buyer_messages": len(buyers)}
        if supplier["supplier_status"] != "open" or "send" not in supplier["legal_actions"]:
            item["reason"] = "supplier_not_open_or_no_send"
            continue
        if events[-1].get("from") == "buyer":
            item["reason"] = "awaiting_supplier_reply"
            continue
        # Do not counter over a qualifying advertised alternative; request binding confirmation.
        if any(
            o["must_haves_ok"]
            and o["terms"].get("contract_months", 0) >= policy["minimum_contract_months"]
            and floor.allows(o["cost"], o["terms"])
            for o in supplier["offers"]
        ):
            item["reason"] = "qualifying_quote_requires_acceptance_or_confirmation"
            continue
        if len(buyers) >= len(packages):
            item["reason"] = "sequence_complete"
            continue
        aligned = all(
            len(m.get("term_sheets", [])) == 1
            and {k: m["term_sheets"][0].get("terms", m["term_sheets"][0]).get(k) for k in case["negotiable"]}
            == packages[i]
            for i, m in enumerate(buyers)
        )
        if not aligned:
            item["reason"] = "history_does_not_match_sequence"
            continue
        stage = sequence["stages"][len(buyers)]
        terms = packages[len(buyers)]
        item.update(stage=stage["name"], evidence_argument=stage["evidence_argument"], terms=terms)
        if cue := structure_restriction(events, sequence["payment_days"], payment_restriction):
            item.update(reason="explicit_structure_restriction", source_quote=cue)
            continue
        cost = total_cost(case, terms, standard_terms(events))
        if (
            not valid_terms(case, terms)
            or terms["contract_months"] < policy["minimum_contract_months"]
            or not floor.allows(cost, terms)
        ):
            item["reason"] = "package_outside_current_limits"
            continue
        item.update(status="ready", total_cost=cost)
    return result


def sequence_action(plan):
    for sid, item in plan.items():
        if item["status"] != "ready":
            continue
        terms = item["terms"]
        clause = terms["index_clause"]
        intro = STAGES[item["stage"]][1]
        body = (
            f"Dear Mr Seidl,\n\n{intro} We value our relationship with Werdenfels and "
            "would like to find a package your team can approve.\n\n"
            f"We propose EUR {terms['unit_price']:.2f}/kg for part:370010, payment within "
            f"{terms['payment_days']} days net, a {terms['contract_months']}-month contract, "
            f"{clause['series']} on a {clause['material_share'] * 100:g}% material share, "
            f"{clause['deadband_pct']:g}% deadband and base month {clause['base_month']}.\n\n"
            "Our price applies only together with the complete package above. "
            "This is a proposal for your approval, not a confirmation of agreement. "
            "The fixed terms of our enquiry remain unchanged. "
            "Could you issue this complete package as your binding offer? "
            "If a term prevents approval, please identify it so we can address it directly.\n\n"
            "Kind regards,\nPurchasing, Hollerau Pumpenwerke"
        )
        return {"supplier_id": sid, "action": "send", "body": body, "term_sheets": [terms]}
    return None
