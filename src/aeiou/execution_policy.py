"""Explicit user-authorized package and closing controls, saved with each evaluation."""

import json
import math
import re
from decimal import Decimal

from arena_kit.cost import total_cost
from arena_kit.docs import standard_terms

from .config import state_root
from .optimizer import valid_terms
from .package_sequence import (
    sequence_action,
    sequence_plan,
    stage_from_action,
    validate_case_sequence,
    validate_sequence,
)
from .price_planner import maximum_affordable_price

CLOSING_PREFIX = "Thank you for reviewing our proposals and explaining your position."
CLOSING_REQUEST = "Could you kindly approve this complete package as your binding offer?"
PACKAGE_PREFIX = "Thank you for working through the options with us."
COMPROMISE_PREFIX = "Thank you for reviewing the price and payment options with us."

TRANSPORT = """

<authorized_execution_controls>
execution_policy is an explicit, case-scoped user setting saved with this run. When enabled,
code accepts a qualifying live offer before requesting another model decision. It may also make
one authorized direct closing counter on a final offer, overriding small-step concession rules
for that one move. These controls never relax the nominal or complete-cost limits, change a
supplier's quoted package during acceptance, or reopen a closed thread. The decision log records
these code-generated actions. If adapt_closing_price_to_budget is enabled, closing_price is
a ceiling: code solves the affordable cent price for each live offered structure, then proposes
the highest such price (lowest complete cost breaks ties). This is a buyer concession choice,
not a prediction of supplier acceptance. All other tactics follow the strategy.
When early_package_alternatives is enabled, code sends its authorized menu once after the
configured number of buyer proposals, on an open supplier reply with a legal send. It retains
an offered contract duration and validates every package against the case and cost limits.
This menu overrides small-step concessions for that move; it requests agreement to NEW terms.
After the menu, code does not repeat it or send the old automatic price-only final counter.
Accept qualifying binding replies first; otherwise address the actual objection through the
strategy. Never claim a proposed alternative is already agreed or force an ended thread open.
If menu_evidence_argument is configured, code adds that checked public calculation to the
menu only when its records are delivered and it has not already appeared in a buyer message.
The decision log records inclusion or the reason for skipping it. Do not present it as new
evidence again on the next turn, or as proof of the supplier's minimum price.
When open_payment_compromise is configured, code proposes that package once after the menu
on an open reply, including replies offering 14 or 30 days. It keeps an actually offered
clause and duration, rechecks the full cost, and defers to the strategy if supplier language
indicates a payment restriction. It never reserves the package for a final offer.
compromise_evidence_argument adds only the checked, unused public AS window to this move.
When stop_after_nonqualifying_final is enabled, a final offer with no qualifying quoted
package permits no further negotiation send or confirmation. Stop locally if no other useful
thread remains. This is the buyer's policy restriction, not an exhausted API round limit.
An eligible qualifying offer is always accepted first. The evaluation runner may separately
close an abandoned practice session through its existing cleanup path.
consistent_package_sequence replaces the legacy menu and payment compromise for its case.
Code sends its opening and two counters with one unchanged payment/clause/duration structure.
Progress is reconstructed from sent packages, never a mutable turn counter. It defers when
the history deviates, the supplier explicitly restricts the structure, or required evidence is
unavailable. package_sequence_state explains the reason. Address the actual objection using
permitted qualifying terms; do not blindly repeat the script or infer acceptance from courtesy.
The historical tenth-euro price pattern is a planning observation, not a hard acceptance rule.
</authorized_execution_controls>
"""


def validate_policy(policy, floor):
    required = {"case_id", "accept_eligible_offers", "closing_price", "maximum_unit_price",
                "maximum_total_cost", "minimum_contract_months", "one_final_counter"}
    optional = {"adapt_closing_price_to_budget", "early_package_alternatives", "menu_evidence_argument",
                "open_payment_compromise", "compromise_evidence_argument", "stop_after_nonqualifying_final",
                "consistent_package_sequence"}
    if not isinstance(policy, dict) or not required <= set(policy) or set(policy) - required - optional:
        raise ValueError("Execution policy must contain exactly the documented closing-control fields")
    for key in ("accept_eligible_offers", "one_final_counter"):
        if type(policy[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    if "adapt_closing_price_to_budget" in policy and type(policy["adapt_closing_price_to_budget"]) is not bool:
        raise ValueError("adapt_closing_price_to_budget must be a boolean")
    if "stop_after_nonqualifying_final" in policy and type(policy["stop_after_nonqualifying_final"]) is not bool:
        raise ValueError("stop_after_nonqualifying_final must be a boolean")
    for key in ("closing_price", "maximum_unit_price", "maximum_total_cost", "minimum_contract_months"):
        if type(policy[key]) not in (int, float) or not math.isfinite(policy[key]) or policy[key] <= 0:
            raise ValueError(f"{key} must be finite and positive")
    if not floor or floor.case_id != policy["case_id"]:
        raise ValueError("Execution controls require matching case-scoped acceptance limits")
    if (Decimal(str(policy["maximum_unit_price"])) != floor.maximum_unit_price
            or Decimal(str(policy["maximum_total_cost"])) != floor.max_cost
            or policy["closing_price"] > policy["maximum_unit_price"]):
        raise ValueError("Execution controls and prompt acceptance limits differ; align them before starting a run")
    if "early_package_alternatives" in policy:
        menu = policy["early_package_alternatives"]
        if not isinstance(menu, dict) or set(menu) != {"after_buyer_proposals", "packages"}:
            raise ValueError("early_package_alternatives requires after_buyer_proposals and packages")
        if type(menu["after_buyer_proposals"]) is not int or menu["after_buyer_proposals"] < 1:
            raise ValueError("after_buyer_proposals must be a positive integer")
        packages = menu["packages"]
        if not isinstance(packages, list) or not 2 <= len(packages) <= 3:
            raise ValueError("early_package_alternatives requires two or three packages")
        for p in packages:
            if not isinstance(p, dict) or set(p) != {"unit_price", "payment_days", "index_clause"}:
                raise ValueError("Each early package must specify price, payment days and index clause")
            if (type(p["unit_price"]) not in (int, float) or not math.isfinite(p["unit_price"])
                    or not 0 < p["unit_price"] <= policy["maximum_unit_price"]):
                raise ValueError("Early package price must be finite, positive and within the nominal cap")
            if type(p["payment_days"]) is not int or p["payment_days"] <= 0:
                raise ValueError("Early package payment_days must be a positive integer")
            if not isinstance(p["index_clause"], dict):
                raise ValueError("Early package alternatives require an explicit index clause")
        if any(p in packages[:i] for i, p in enumerate(packages)):
            raise ValueError("Early package alternatives must be distinct")
    if "menu_evidence_argument" in policy and (
            policy["menu_evidence_argument"] != "ppi_recent_change_pct"
            or policy["case_id"] != "case:P18" or not policy.get("early_package_alternatives")):
        raise ValueError("menu_evidence_argument must select ppi_recent_change_pct for a P18 package menu")
    if "open_payment_compromise" in policy:
        p = policy["open_payment_compromise"]
        if (policy["case_id"] != "case:P18" or not policy.get("early_package_alternatives")
                or not isinstance(p, dict) or set(p) != {"unit_price", "payment_days", "index_clause"}
                or type(p["unit_price"]) not in (int, float) or not math.isfinite(p["unit_price"])
                or not 0 < p["unit_price"] <= policy["maximum_unit_price"]
                or type(p["payment_days"]) is not int or p["payment_days"] <= 0
                or not isinstance(p["index_clause"], dict)):
            raise ValueError("open_payment_compromise requires a valid P18 package and an early menu")
        if p in policy["early_package_alternatives"]["packages"]:
            raise ValueError("open_payment_compromise must be distinct from the early menu")
    if "compromise_evidence_argument" in policy and (
            policy["compromise_evidence_argument"] != "as_recent_change_pct"
            or not policy.get("open_payment_compromise")):
        raise ValueError("compromise_evidence_argument requires as_recent_change_pct and an open compromise")
    if "consistent_package_sequence" in policy:
        validate_sequence(policy)
    return policy


def validate_case_policy(case, floor, policy):
    """Check configured proposals against public case options before starting a run."""
    if (policy or {}).get("consistent_package_sequence"):
        validate_case_sequence(case, floor, policy)
    menu = (policy or {}).get("early_package_alternatives")
    if not menu:
        return
    durations = [m for m in case.get("term_options", {}).get("contract_months", [])
                 if m >= policy["minimum_contract_months"]]
    if not durations:
        raise ValueError("No case contract duration satisfies the early package policy")
    packages = menu["packages"] + ([policy["open_payment_compromise"]]
                                  if policy.get("open_payment_compromise") else [])
    for package in packages:
        terms = {**package, "contract_months": min(durations)}
        if not valid_terms(case, terms):
            raise ValueError("Early package alternatives must use exactly the permitted case terms")
        if not package_allowed(case, terms, total_cost(case, terms), floor, policy):
            raise ValueError("Early package alternatives must satisfy the complete-cost and duration limits")


def load_execution_policies():
    path = state_root() / "negotiation-policies.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or any(not isinstance(p, dict) or p.get("case_id") != cid
                                          for cid, p in value.items()):
        raise ValueError("Negotiation policies must map exact case IDs to their controls")
    return value


def matches_scope(case, policy):
    return policy is not None and case["case_id"] == policy["case_id"]


def package_allowed(case, terms, cost, floor, policy):
    minimal = {k: terms.get(k) for k in case["negotiable"]}
    return (valid_terms(case, minimal) and terms.get("contract_months", 0) >= policy["minimum_contract_months"]
            and floor.allows(cost, terms))


def package_menu_used(events, menu):
    # Match terms as well as our wording, including a menu sent by the model. IDs,
    # term-sheet ordering and a subsequent supplier duration change must not reset it.
    return any(m.get("from") == "buyer" and (
        m.get("body", "").startswith(PACKAGE_PREFIX)
        or any(all(s.get("terms", {}).get(k) == v for k, v in p.items())
               for s in m.get("term_sheets", []) for p in menu["packages"])) for m in events)


def early_package_action(case, threads, state, floor, policy):
    menu = policy.get("early_package_alternatives")
    if not menu:
        return None
    for sid, supplier in state["suppliers"].items():
        events = threads[sid]
        if (supplier["supplier_status"] != "open" or "send" not in supplier["legal_actions"]
                or package_menu_used(events, menu)):
            continue
        proposals = sum(m.get("from") == "buyer" and any(
            valid_terms(case, {k: s.get("terms", {}).get(k) for k in case["negotiable"]})
            for s in m.get("term_sheets", [])) for m in events)
        if proposals < menu["after_buyer_proposals"]:
            continue
        # Use a duration currently quoted by the supplier, not an invented commitment.
        for offer in supplier["offers"]:
            if not offer["must_haves_ok"]:
                continue
            duration = offer["terms"].get("contract_months", 0)
            if duration < policy["minimum_contract_months"]:
                continue
            packages = [{**p, "contract_months": duration} for p in menu["packages"]]
            std = standard_terms(events)
            if not all(valid_terms(case, p) and package_allowed(case, p, total_cost(case, p, std), floor, policy)
                       for p in packages):
                continue
            options = []
            for i, p in enumerate(packages):
                clause = p["index_clause"]
                options.append(
                    f"Option {chr(65 + i)}: EUR {p['unit_price']:.2f}/kg, payment within {p['payment_days']} days "
                    f"net, a {p['contract_months']}-month contract, {clause['series']} on a "
                    f"{clause['material_share'] * 100:g}% material share, {clause['deadband_pct']:g}% deadband "
                    f"and base month {clause['base_month']}.")
            trade_note = ""
            for i, p in enumerate(packages):
                peers = [other for other in packages if other is not p]
                # Explain a real package trade only when the adjustment basis is
                # shared and every peer has a wider buffer, later payment and higher price.
                if all(all(p["index_clause"][key] == other["index_clause"][key]
                           for key in ("series", "material_share", "base_month"))
                       and p["index_clause"]["deadband_pct"] < other["index_clause"]["deadband_pct"]
                       and p["payment_days"] < other["payment_days"]
                       and p["unit_price"] < other["unit_price"] for other in peers):
                    trade_note = (f"Option {chr(65 + i)} pairs earlier payment with a narrower adjustment "
                                  "buffer in exchange for its lower starting price.\n\n")
            body = (
                f"{PACKAGE_PREFIX} We appreciate your team's careful review and would like to find an "
                "arrangement that works for Werdenfels as well as for us.\n\n"
                "Could you kindly consider one of these complete alternatives? Each price is conditional on "
                "its payment term and adjustment clause.\n\n"
                + trade_note
                + "\n".join(options)
                + "\n\nThe fixed terms of our enquiry remain unchanged. These are new proposals for your approval. "
                "Could you issue the option you can approve as your binding offer? If none works, which term "
                "would need to change? We would be pleased to review it together.\n\nKind regards,\nPurchasing"
            )
            return {"supplier_id": sid, "action": "send", "body": body, "term_sheets": packages}
    return None


def payment_restriction(events, days):
    """Conservatively defer ambiguous payment restrictions to the model, not a forced counter.

    This is a text cue, not a complete semantic classifier. Check the sent supplier
    history so an earlier explicit refusal isn't lost after another quotation.
    """
    for message in events:
        if message.get("from") == "buyer":
            continue
        for sentence in re.split(r"[.!?\n]", message.get("body", "")):
            payment = re.search(r"payment|credit|zahlungs|\bdays?\s+net\b|\bnet\s+\d+", sentence, re.I)
            target = re.search(rf"\b{days}[ -]*(?:days?|tage)\b", sentence, re.I)
            restriction = re.search(
                r"cannot|can't|unable|not (?:possible|acceptable|an option)|unacceptable|reject|declin|refus|"
                r"\bonly\b|\brequir(?:e|es|ed)\b|\bmust\b|maximum|at most|no more than|up to|non.negotiable|fixed at|"
                r"will not|won't|nicht|nur|maximal|müssen", sentence, re.I)
            if (payment or target) and restriction:
                return sentence.strip()
    return None


def open_payment_compromise_action(case, threads, state, floor, policy):
    package = policy.get("open_payment_compromise")
    if not package:
        return None
    for sid, supplier in state["suppliers"].items():
        events = threads[sid]
        used = any(m.get("from") == "buyer" and (
            m.get("body", "").startswith(COMPROMISE_PREFIX)
            or any(s.get("terms", {}).get("payment_days") == package["payment_days"]
                   and s.get("terms", {}).get("index_clause") == package["index_clause"]
                   for s in m.get("term_sheets", []))) for m in events)
        if (supplier["supplier_status"] != "open" or "send" not in supplier["legal_actions"] or used
                or not package_menu_used(events, policy["early_package_alternatives"])
                or payment_restriction(events, package["payment_days"])):
            continue
        for offer in sorted(supplier["offers"], key=lambda o: o["cost"]):
            current = offer["terms"]
            if (not offer["must_haves_ok"] or current.get("index_clause") != package["index_clause"]
                    or current.get("unit_price", 0) < package["unit_price"]):
                continue
            terms = {**package, "contract_months": current.get("contract_months", 0)}
            if not package_allowed(case, terms, total_cost(case, terms, standard_terms(events)), floor, policy):
                continue
            clause = terms["index_clause"]
            body = (
                f"{COMPROMISE_PREFIX} We appreciate your team's review.\n\n"
                "Could we agree the following complete compromise? "
                f"EUR {terms['unit_price']:.2f}/kg, payment within {terms['payment_days']} days net, "
                f"a {terms['contract_months']}-month contract, {clause['series']} on a "
                f"{clause['material_share'] * 100:g}% material share, {clause['deadband_pct']:g}% deadband "
                f"and base month {clause['base_month']}.\n\n"
                "The price is conditional on this payment term and the complete package. "
                "This is a new proposal for your approval. "
                "The fixed terms of our enquiry remain unchanged. "
                "Could your authorised colleague issue this package as your binding offer?\n\n"
                "Kind regards,\nPurchasing, Hollerau Pumpenwerke"
            )
            return {"supplier_id": sid, "action": "send", "body": body, "term_sheets": [terms]}
    return None


def nonqualifying_final(case, supplier, floor, policy):
    """Policy stop; a qualifying advertised alternative can still need binding confirmation."""
    return bool((policy or {}).get("stop_after_nonqualifying_final")
                and supplier["supplier_status"] == "final_offer"
                and not any(o["must_haves_ok"] and package_allowed(case, o["terms"], o["cost"], floor, policy)
                            for o in supplier["offers"]))


def controlled_action(case, threads, state, floor, policy):
    if not matches_scope(case, policy):
        return None
    acceptable = [(o["cost"], sid, o) for sid, s in state["suppliers"].items() for o in s["offers"]
                  if o["acceptance_eligible"] and package_allowed(case, o["terms"], o["cost"], floor, policy)]
    if acceptable and policy["accept_eligible_offers"]:
        _, sid, offer = min(acceptable, key=lambda item: item[0])
        return {"supplier_id": sid, "action": "accept", "term_sheet_id": offer["term_sheet_id"]}
    if acceptable:
        return None
    sequential = sequence_action(sequence_plan(case, threads, state, floor, policy, payment_restriction))
    if sequential:
        return sequential
    menu_action = early_package_action(case, threads, state, floor, policy)
    if menu_action:
        return menu_action
    compromise = open_payment_compromise_action(case, threads, state, floor, policy)
    if compromise:
        return compromise
    if state["suppliers"] and all(nonqualifying_final(case, s, floor, policy)
                                  for s in state["suppliers"].values()):
        return {"supplier_id": next(iter(state["suppliers"])), "action": "stop"}
    if not policy["one_final_counter"]:
        return None
    for sid, supplier in state["suppliers"].items():
        if supplier["supplier_status"] != "final_offer" or "send" not in supplier["legal_actions"]:
            continue
        if policy.get("early_package_alternatives") and package_menu_used(
                threads[sid], policy["early_package_alternatives"]):
            continue
        # Transcript-based idempotency survives retries/reconstruction of the agent.
        used = any(m.get("from") == "buyer" and (
            (m.get("body", "").startswith(CLOSING_PREFIX) and CLOSING_REQUEST in m.get("body", ""))
            or any(s.get("terms", {}).get("unit_price", 0) >= policy["closing_price"]
                   for s in m.get("term_sheets", []))) for m in threads[sid])
        if used:
            continue
        candidates = []
        for offer in supplier["offers"]:
            # A quoted alternative can be proposed for binding approval even when it is
            # not the server's standing offer. Acceptance above still requires eligibility.
            if not offer["must_haves_ok"] or (not policy.get("adapt_closing_price_to_budget")
                                              and not offer["server_acceptance_available"]):
                continue
            terms = {k: offer["terms"][k] for k in case["negotiable"]}
            if not {"unit_price", "payment_days", "contract_months"} <= terms.keys():
                continue
            # Keep the offered structure; don't invent a new duration or unknown supplier permission.
            terms["unit_price"] = policy["closing_price"]
            if policy.get("adapt_closing_price_to_budget"):
                price = maximum_affordable_price(case, terms, policy["maximum_total_cost"],
                                                 policy["closing_price"], standard_terms(threads[sid]))
                if price is None:
                    continue
                terms["unit_price"] = price
            cost = total_cost(case, terms, standard_terms(threads[sid]))
            if not package_allowed(case, terms, cost, floor, policy):
                continue
            candidates.append((terms, cost))
        if not candidates:
            continue
        terms, _ = (min(candidates, key=lambda item: (-item[0]["unit_price"], item[1]))
                    if policy.get("adapt_closing_price_to_budget") else candidates[0])
        clause = terms.get("index_clause")
        adjustment = (f"{clause['series']} on a {clause['material_share'] * 100:g}% material share, "
                      f"{clause['deadband_pct']:g}% deadband and base month {clause['base_month']}"
                      if clause else "a fixed price")
        body = (
            f"{CLOSING_PREFIX} "
            "We appreciate your team's effort and would very much like to conclude together.\n\n"
            f"We can offer EUR {terms['unit_price']:.2f} per unit with payment within "
            f"{terms['payment_days']} days net, a {terms['contract_months']}-month contract and {adjustment}. "
            "The other fixed terms of our enquiry remain unchanged.\n\n"
            f"{CLOSING_REQUEST} "
            "We are ready to conclude promptly if you can.\n\nKind regards,\nPurchasing"
        )
        return {"supplier_id": sid, "action": "send", "body": body, "term_sheets": [terms]}
    return None


def controlled_source(action):
    if action["action"] == "accept":
        return "policy:accept"
    if action["action"] == "stop":
        return "policy:final-offer-stop"
    if stage := stage_from_action(action):
        return f"policy:package-sequence:{stage}"
    if action.get("body", "").startswith(PACKAGE_PREFIX):
        return "policy:package-alternatives"
    if action.get("body", "").startswith(COMPROMISE_PREFIX):
        return "policy:payment-compromise"
    return "policy:closing-counter"


def controlled_assessment(action, context):
    sid = action["supplier_id"]
    ref = context["suppliers"][sid]["latest_message_ref"]
    accepting = action["action"] == "accept"
    menu = controlled_source(action) == "policy:package-alternatives"
    compromise = controlled_source(action) == "policy:payment-compromise"
    stopping = action["action"] == "stop"
    stage = stage_from_action(action)
    return {"assessment": {
        "observed_message": ref,
        "changes": ["Reviewed the current offer against the explicit execution controls."],
        "evidence": [{"claim": "The supplier supplied the current offer being evaluated.",
                      "kind": "supplier_claim", "source_refs": [ref], "confidence": "high"}],
        "open_questions": [] if accepting or stopping else [
            "Which proposed package, if any, can the supplier approve?" if menu
            else "Will the supplier issue the authorized closing package?"],
        "response_to_last_move": "Current structured terms and live eligibility determine the available action.",
        "adaptation": ("Apply the user's acceptance priority." if accepting else
                       "Stop after a final offer with no package meeting the saved limits." if stopping else
                       "Keep payment, duration and index clause unchanged through the sequence." if stage else
                       "Offer the payment compromise while open for any current payment term." if compromise else
                       "Offer the authorized package alternatives before a final offer." if menu else
                       "Use the one authorized final counter."),
        "next_move": ("Accept the exact qualifying live offer." if accepting else
                      "Stop locally without another counter or a below-threshold acceptance." if stopping else
                      f"Send the {stage} package once with its checked evidence." if stage else
                      "Send the payment compromise once and request a binding offer." if compromise else
                      "Send the package menu once and request a binding offer." if menu else
                      "Send the closing counter once."),
    }, "numeric_claims": []}
