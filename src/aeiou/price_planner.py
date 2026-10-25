"""Auditable buyer price envelopes using the exact public cost function."""

import json
import math
import re
from decimal import ROUND_FLOOR, Decimal

from arena_kit.cost import complete_offer, score, total_cost
from arena_kit.docs import standard_terms

from .optimizer import case_with_supplier_defaults, valid_terms


def maximum_affordable_price(case, terms, budget, unit_cap, standard=None):
    """Largest cent price satisfying C and the nominal cap. No estimated supplier floor.

    The public cost without a private relationship estimate is affine in price with a
    positive slope. Refuse unsupported/non-monotonic inputs instead of guessing.
    """
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in (budget, unit_cap)):
        raise ValueError("Price solver needs finite positive bounds")
    template = {k: terms[k] for k in case["negotiable"] if k in terms}
    if "unit_price" not in case["negotiable"] or not valid_terms(case, {**template, "unit_price": 0.01}):
        return None
    probe = [total_cost(case, {**template, "unit_price": p}, standard) for p in (1, 2, 3)]
    slope = probe[1] - probe[0]
    if not all(math.isfinite(v) for v in probe) or slope <= 0 or abs(probe[2] - probe[1] - slope) > 0.021:
        return None
    lo, hi = 0, int((Decimal(str(unit_cap)) * 100).to_integral_value(rounding=ROUND_FLOOR))
    ceiling = Decimal(str(budget))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cost = total_cost(case, {**template, "unit_price": mid / 100}, standard)
        if Decimal(str(cost)) <= ceiling:
            lo = mid
        else:
            hi = mid - 1
    return lo / 100 if lo else None


def capacity_opening_config(strategy):
    """An explicit, case-scoped anchor in a saved strategy; no global default change."""
    blocks = re.findall(r"<capacity_opening>\s*(.*?)\s*</capacity_opening>", strategy, re.S)
    if not blocks:
        return None
    if len(blocks) != 1:
        raise ValueError("Specify exactly one capacity_opening block")
    value = json.loads(blocks[0])
    if (
        not isinstance(value, dict)
        or set(value) != {"case_id", "opening_fraction"}
        or not isinstance(value["case_id"], str)
        or not value["case_id"].startswith("case:")
        or type(value["opening_fraction"]) not in (int, float)
        or not math.isfinite(value["opening_fraction"])
        or not 0 < value["opening_fraction"] <= 1
    ):
        raise ValueError("capacity_opening requires case_id and an opening_fraction in (0, 1]")
    return value


def capacity_opening_tradeoffs(case, events, standard=None, opening_fraction=0.85):
    """Price full-coverage capacity alternatives from the visible opening only.

    The configured anchor is a buyer proposal, not an acceptance limit.
    Delivery penalties are offset using the public cost formula. Neither a
    should-cost estimate nor a previous run's attainable best enters this menu.
    """
    required = {"unit_price", "volume_per_year", "contract_months", "lead_time_days"}
    if case.get("type") != "capacity" or not required <= set(case.get("negotiable", [])):
        return []
    case = case_with_supplier_defaults(case, events)
    if type(opening_fraction) not in (int, float) or not 0 < opening_fraction <= 1:
        raise ValueError("Opening fraction must be in (0, 1]")
    opening = next(
        (
            s.get("terms", {}).get("unit_price")
            for m in events
            if m.get("from") != "buyer"
            for s in m.get("term_sheets", [])
            if s.get("terms", {}).get("unit_price")
        ),
        None,
    )
    if type(opening) not in (int, float) or not math.isfinite(opening) or opening <= 0:
        return []
    anchor = float(
        (Decimal(str(opening)) * Decimal(str(opening_fraction))).quantize(Decimal(".01"), rounding=ROUND_FLOOR)
    )
    reference = complete_offer(case, {"unit_price": anchor})
    if not valid_terms(case, reference):
        return []
    budget = total_cost(case, reference, standard)
    options, buyer = case.get("term_options", {}), case["buyer"]
    rows = []
    for months in options.get("contract_months", []):
        if months < 12 * buyer["horizon_years"]:
            continue
        for days in options.get("lead_time_days", []):
            # Faster than needed adds no buyer value in this case formula.
            if days < buyer["need_lead_time_days"]:
                continue
            terms = {**reference, "contract_months": months, "lead_time_days": days}
            price = maximum_affordable_price(case, terms, budget, anchor, standard)
            if price is None:
                continue
            terms["unit_price"] = price
            rows.append(
                {
                    "terms": terms,
                    "complete_cost": total_cost(case, terms, standard),
                    "reference_cost": budget,
                    "delay_cost_at_reference_price": round(
                        total_cost(case, {**terms, "unit_price": anchor}, standard) - budget, 2
                    ),
                    "unit_price_reduction": round(anchor - price, 2),
                    "opening_fraction": opening_fraction,
                    "status": (
                        f"Illustrative {opening_fraction:.0%}-of-opening buyer proposal; "
                        "requires supplier approval. "
                        "Public costs exclude unknown supplier relationship costs; cent rounding may lower cost. "
                        "Commitment beyond the scoring horizon is real but unpriced by this formula."
                    ),
                }
            )
    return rows


def estimated_score(floor, cost, *, clipped=False):
    if not floor.has_benchmark:
        return None
    if clipped:
        return round(score(float(floor.c_open), cost, float(floor.c_best)) * 100, 4)
    return round(float((floor.c_open - Decimal(str(cost))) / (floor.c_open - floor.c_best) * 100), 4)


def payment_tradeoffs(case, terms, floor, standard=None, minimum_contract_months=None):
    """Enumerate every permitted payment option at its qualifying cent price.

    These are new buyer proposals, never live offers or inferred supplier preferences.
    Keep the actual offered duration/clause and all other terms intact.
    """
    if "payment_days" not in case.get("negotiable", []) or not floor or not floor.maximum_unit_price:
        return []
    if minimum_contract_months and terms.get("contract_months", 0) < minimum_contract_months:
        return []
    rows = []
    for days in case.get("term_options", {}).get("payment_days", []):
        if type(days) is not int or days <= 0:
            continue
        proposal = {k: terms.get(k) for k in case["negotiable"]}
        proposal["payment_days"] = days
        cap = min(float(floor.maximum_unit_price), terms["unit_price"])
        price = maximum_affordable_price(case, proposal, float(floor.max_cost), cap, standard)
        if price is None:
            continue
        proposal["unit_price"] = price
        cost = total_cost(case, proposal, standard)
        rows.append(
            {
                "terms": proposal,
                "complete_cost": cost,
                "estimated_score": estimated_score(floor, cost, clipped=True),
                "price_change": round(price - terms["unit_price"], 2),
                "payment_days_change": days - terms["payment_days"],
                "status": "New buyer proposal only; requires supplier approval as a live binding offer.",
            }
        )
    return rows


def closing_comparisons(case, events, offers, price, floor, standard=None, minimum_contract_months=None):
    """Compare discussed structures at one price, without inventing live offers.

    A cheap current quote need not be the cheapest structure after negotiating
    both prices down. Include the last buyer menu so its other options do not
    disappear merely because the supplier chose a different one to discuss.
    """
    if (
        not floor
        or type(price) not in (int, float)
        or not math.isfinite(price)
        or price <= 0
        or not floor.allows_unit_price({"unit_price": price})
    ):
        return []
    sources = [(o["terms"], {"kind": "supplier_quote", "offer_id": o["term_sheet_id"]}) for o in offers]
    for index in range(len(events) - 1, -1, -1):
        message = events[index]
        if message.get("from") == "buyer" and message.get("term_sheets"):
            sources.extend(
                (s.get("terms", {}), {"kind": "buyer_proposal", "message_index": index}) for s in message["term_sheets"]
            )
            break
    unique = []
    for terms, origin in sources:
        proposal = {key: terms.get(key) for key in case["negotiable"]}
        proposal["unit_price"] = price
        if not valid_terms(case, proposal) or (
            minimum_contract_months and proposal.get("contract_months", 0) < minimum_contract_months
        ):
            continue
        # Dict equality preserves numeric equivalence (5 == 5.0). JSON text
        # keys would duplicate the same clause from server and buyer records.
        row = next((row for row in unique if row["terms"] == proposal), None)
        if row is None:
            cost = total_cost(case, proposal, standard)
            if not math.isfinite(cost):
                continue
            row = {
                "terms": proposal,
                "complete_cost": cost,
                "estimated_score": estimated_score(floor, cost, clipped=True),
                "within_buyer_limits": floor.allows(cost, proposal),
                "origins": [],
                "status": "Buyer calculation only; requires supplier approval and a live eligible offer.",
            }
            unique.append(row)
        if origin not in row["origins"]:
            row["origins"].append(origin)
    rows = sorted(unique, key=lambda row: row["complete_cost"])
    for row in rows:
        row["cost_above_lowest_comparison"] = round(row["complete_cost"] - rows[0]["complete_cost"], 2)
    return rows


def price_plan(
    case, threads, state, floor=None, preferred_price=None, minimum_contract_months=None, capacity_opening_fraction=0.85
):
    plan = {
        "basis": "Public total-cost formula and explicit buyer limits; no private supplier costs or reservation price.",
        "needed_quantity": case["buyer"]["need_per_year"] * case["buyer"]["horizon_years"],
        "horizon_years": case["buyer"]["horizon_years"],
        "preferred_closing_price": preferred_price,
        "ideal_price": None,
        "ideal_price_status": "Not identifiable from public data: the supplier's feasible minimum is unknown.",
        "closing_comparison_basis": (
            "Discussed structures compared at preferred_closing_price. Rankings are arithmetic, not predictions "
            "of supplier acceptance. Buyer proposals are not supplier commitments or eligible offers."
        ),
        "suppliers": {},
    }
    if floor:
        plan["minimum_score"] = float(floor.minimum_score) if floor.has_benchmark else None
        plan["maximum_total_cost"] = float(floor.max_cost)
        plan["score_basis"] = (
            "Arithmetic estimate from the configured benchmarks; official result is server-issued."
            if floor.has_benchmark
            else "No benchmark supplied; enforce absolute limits without estimating a score."
        )
    for sid, supplier in state["suppliers"].items():
        supplier_case = case_with_supplier_defaults(case, threads[sid])
        std, items = standard_terms(threads[sid]), []
        for offer in supplier["offers"]:
            terms = offer["terms"]
            item = {
                "offer_id": offer["term_sheet_id"],
                "unit_price": terms.get("unit_price"),
                "complete_cost": offer["cost"],
                "acceptance_eligible": offer["acceptance_eligible"],
            }
            if floor and floor.maximum_unit_price and math.isfinite(offer["cost"]):
                item["estimated_score"] = estimated_score(floor, offer["cost"])
                item["maximum_affordable_unit_price"] = maximum_affordable_price(
                    supplier_case, terms, float(floor.max_cost), float(floor.maximum_unit_price), std
                )
                item["cost_gap_to_acceptance"] = round(offer["cost"] - float(floor.max_cost), 2)
                item["qualifying_payment_tradeoffs"] = payment_tradeoffs(
                    supplier_case, terms, floor, std, minimum_contract_months
                )
                item["benchmark_price_limits"] = []
                thresholds = sorted({70, 80, 100, float(floor.minimum_score)}) if floor.has_benchmark else []
                for score in thresholds:
                    budget = floor.c_open - Decimal(score) / 100 * (floor.c_open - floor.c_best)
                    item["benchmark_price_limits"].append(
                        {
                            "score": score,
                            "unit_price_at_or_below": maximum_affordable_price(
                                supplier_case, terms, float(budget), float(floor.maximum_unit_price), std
                            ),
                            "meaning": (
                                "Arithmetic threshold for these terms, not evidence the supplier will accept it."
                            ),
                        }
                    )
                affordable = item["maximum_affordable_unit_price"]
                if affordable is not None:
                    proposal = {k: terms[k] for k in case["negotiable"]}
                    proposal["unit_price"] = affordable
                    cost = total_cost(case, proposal, std)
                    item["budget_ceiling_package"] = {
                        "terms": proposal,
                        "complete_cost": cost,
                        "estimated_score": estimated_score(floor, cost),
                        "status": "Buyer calculation only; supplier has not offered this price.",
                    }
                if preferred_price is not None:
                    target = {k: terms[k] for k in case["negotiable"]}
                    target["unit_price"] = preferred_price
                    target_cost = total_cost(case, target, std)
                    item["preferred_package"] = {
                        "terms": target,
                        "complete_cost": target_cost,
                        "within_buyer_limits": floor.allows(target_cost, target),
                    }
            items.append(item)
        qualified = sorted((o for o in supplier["offers"] if o["acceptance_eligible"]), key=lambda o: o["cost"])
        plan["suppliers"][sid] = {
            "offers": items,
            "best_eligible_offer_id": qualified[0]["term_sheet_id"] if qualified else None,
            "closing_package_comparison": closing_comparisons(
                supplier_case, threads[sid], supplier["offers"], preferred_price, floor, std, minimum_contract_months
            ),
        }
        if capacity_menu := capacity_opening_tradeoffs(case, threads[sid], std, capacity_opening_fraction):
            plan["suppliers"][sid]["capacity_opening_tradeoffs"] = capacity_menu
    return plan
