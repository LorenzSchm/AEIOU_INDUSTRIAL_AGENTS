"""Observable protocol state, package confirmations and decision diagnostics.

Only public case inputs and supplier messages enter this module. No model reasoning or
simulator-private floors are needed to explain an action.
"""

import hashlib
import json
import math

from arena_kit.cost import closing_terms, must_haves_ok, total_cost
from arena_kit.docs import standard_terms
from arena_kit.play import latest, live_threads, moves_left

from .optimizer import alternative_cost, evaluate
from .store import clean

REQUIRED_REPEATS = 5
PROTOCOL_VERSION = "faithful-v18-closing-cost-comparison"


def package_key(case, terms, standard=None):
    complete = closing_terms(case, terms, standard)

    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
        return value

    return json.dumps(normalize(complete), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def package_repeats(case, events, terms):
    """Consecutive supplier quotations containing this package, irrespective of menu order/IDs.

    A package is counted at most once in a message. Re-reading the same message ID does not
    make another confirmation. Changing another package does not reset this package's count.
    """
    target = package_key(case, terms, standard_terms(events))
    quoted, seen = [], set()
    for i, message in enumerate(events):
        if message.get("from") == "buyer" or not message.get("term_sheets"):
            continue
        mid = message.get("message_id")
        if mid and mid in seen:
            continue
        if mid:
            seen.add(mid)
        std = standard_terms(events[: i + 1])
        quoted.append({package_key(case, s.get("terms", {}), std) for s in message["term_sheets"]})
    count = 0
    for packages in reversed(quoted):
        if target not in packages:
            break
        count += 1
    return count


def offer_state(case, events, required_repeats=0):
    rows, std, alt = [], standard_terms(events), alternative_cost(case)
    current = latest(events)
    rejected_id = (current.get("acceptance_rejection") or {}).get("rejected_offer_id")
    for position, sheet in enumerate(current.get("term_sheets", []), 1):
        terms = closing_terms(case, sheet.get("terms", {}), std)
        try:
            cost = total_cost(case, sheet.get("terms", {}), std)
            valid = bool(sheet.get("term_sheet_id")) and math.isfinite(cost) and must_haves_ok(case, terms)
        except (TypeError, ValueError, KeyError, ZeroDivisionError):
            cost, valid = math.inf, False
        repeats = package_repeats(case, events, sheet.get("terms", {}))
        acceptable = valid and cost <= alt
        standing = ("standing_offer_id" not in current or sheet.get("term_sheet_id") == current["standing_offer_id"])
        standing = standing and sheet.get("term_sheet_id") != rejected_id
        key = package_key(case, sheet.get("terms", {}), std)
        rows.append(
            {
                "term_sheet_id": sheet.get("term_sheet_id"),
                "offer_position": position,
                "package_fingerprint": hashlib.sha256(key.encode()).hexdigest(),
                "terms": terms,
                "cost": cost,
                "must_haves_ok": valid,
                "economically_acceptable": acceptable,
                "repeat_count": repeats,
                "confirmations_needed": max(0, required_repeats - repeats),
                "server_acceptance_available": standing,
                "acceptance_eligible": acceptable and standing and repeats >= required_repeats,
            }
        )
    return rows


def protocol_state(case, threads, required_repeats=0, targets=None):
    suppliers, targets = {}, targets or {}
    for sid in live_threads(threads):
        events, offers = threads[sid], offer_state(case, threads[sid], required_repeats)
        remaining = max(0, moves_left(case, events))
        eligible = [o for o in offers if o["acceptance_eligible"]]
        possible = [o for o in offers if o["economically_acceptable"] and o["confirmations_needed"] <= remaining]
        target = next((o for o in possible if o["package_fingerprint"] == targets.get(sid)), None)
        reserve = bool(required_repeats and remaining <= required_repeats - 1 and possible and not eligible)
        confirming = bool(target and not target["acceptance_eligible"])
        phase = "confirm" if reserve or confirming else "negotiate"
        legal = []
        if eligible:
            legal.append("accept")
        if remaining:
            legal.append("confirm" if phase == "confirm" else "send")
        suppliers[sid] = {
            "rounds_left": remaining,
            "round_limit": case.get("round_limit", 10),
            "supplier_status": latest(events).get("status"),
            "phase": phase,
            "offers": offers,
            "legal_actions": legal,
            "eligible_offer_ids": [o["term_sheet_id"] for o in eligible],
            "confirmation_offer_ids": [o["term_sheet_id"] for o in possible],
            "confirmation_target": target,
            "confirmation_window_missed": bool(required_repeats and offers and not possible and not eligible),
            "acceptance_rejection": latest(events).get("acceptance_rejection"),
            "consequences": {
                "accept": "Closes the case on the selected eligible binding package.",
                "send": "Consumes one round. Changing any package term resets that package's repeat count.",
                "confirm": "Consumes one round requesting the identical complete package, with no changed terms.",
            },
        }
    return {
        "version": PROTOCOL_VERSION,
        "required_repeats": required_repeats,
        "alternative_cost": alternative_cost(case),
        "suppliers": suppliers,
    }


def walk_away_diagnostic(
    case,
    threads,
    supplier_id,
    *,
    required_repeats=0,
    source="model",
    model_reason=None,
    rejections=None,
    forced_reason=None,
    intelligence=None,
):
    state = protocol_state(case, threads, required_repeats)
    if intelligence is not None:
        priced = {(r["supplier_id"], r["sheet_id"]): r for r in evaluate(case, threads, intelligence)}
        for sid, supplier in state["suppliers"].items():
            for offer in supplier["offers"]:
                if row := priced.get((sid, offer["term_sheet_id"])):
                    offer.update(
                        cost=row["cost"],
                        economically_acceptable=row["acceptable"],
                        acceptance_eligible=row["acceptable"] and offer["repeat_count"] >= required_repeats,
                    )
    all_offers = [o for s in state["suppliers"].values() for o in s["offers"]]
    valid = [o for o in all_offers if o["must_haves_ok"]]
    safe = [o for o in valid if o["economically_acceptable"]]
    eligible = [o for o in safe if o["acceptance_eligible"]]
    remaining = sum(s["rounds_left"] for s in state["suppliers"].values())
    if forced_reason:
        code = forced_reason
    elif not valid:
        code = "NO_VALID_OFFER"
    elif not safe:
        code = "OFFERS_WORSE_THAN_ALTERNATIVE"
    elif not remaining and required_repeats and not eligible:
        code = "REPEAT_RULE_UNMET_AT_ROUND_LIMIT"
    else:
        code = "MODEL_CHOICE_WITH_ACCEPTABLE_OFFER"
    return clean(
        {
            "reason_code": code,
            "source": source,
            "model_reason": model_reason,
            "supplier_id": supplier_id,
            "protocol_version": PROTOCOL_VERSION,
            "rounds_left": {sid: s["rounds_left"] for sid, s in state["suppliers"].items()},
            "required_repeats": required_repeats,
            "supplier_estimates_included": intelligence is not None,
            "offers": {sid: s["offers"] for sid, s in state["suppliers"].items()},
            "alternative_cost": state["alternative_cost"],
            "cheaper_valid_offer_available": bool(safe),
            "acceptance_eligible_offer_available": bool(eligible),
            "rejections": rejections or [],
        }
    )
