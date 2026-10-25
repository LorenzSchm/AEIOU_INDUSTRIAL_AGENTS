"""Complete, validate and price every package using the arena's published cost function."""

import copy
import math

from arena_kit.cost import closing_terms, complete_offer, cost_breakdown, must_haves_ok, total_cost
from arena_kit.docs import standard_terms
from arena_kit.play import latest, live_threads, standing


def case_with_supplier_defaults(case, events):
    """Admit quoted numeric defaults in addition to case options, as API v1 permits.

    The opening structured offer supplies defaults that the quote PDF omits from
    its 'unstated standard terms' section. Never learn options from buyer proposals
    or free text. Keep these observations local to this supplier and leave fixed
    terms and must-haves untouched. The server still validates every proposal.
    """
    opening = next(((i, m) for i, m in enumerate(events)
                    if m.get("from") != "buyer" and m.get("term_sheets")), None)
    if opening is None:
        return case
    index, message = opening
    defaults = {**message["term_sheets"][0].get("terms", {}),
                **standard_terms(events[:index + 1])}
    options = copy.deepcopy(case.get("term_options", {}))
    for field in ("payment_days", "lead_time_days", "volume_per_year", "contract_months", "ppm_commitment"):
        value = defaults.get(field)
        if (field in case["negotiable"] and field in options
                and type(value) is int and value >= 0 and value not in options[field]):
            options[field].append(value)
            options[field].sort()
    return {**case, "term_options": options}


def estimate(case, sid, std, intelligence):
    source = intelligence.get("estimates", {}).get(sid)
    if not source or source["full_cost"] <= 0:
        return None
    # Quote PDFs list only *unstated* standard terms. Offered payment/incoterm may therefore
    # be absent: the supplier master supplies the financing reference, not the current counter.
    master = intelligence.get("facts", {}).get(sid, {})
    reference = {k: master[k] for k in ("payment_days", "incoterm") if master.get(k) is not None}
    reference.update(std)
    reference = closing_terms(case, {}, reference)
    return {"full_cost": source["full_cost"], "interest_rate": 0.12, "standard": reference}


def valid_terms(case, terms):
    """Reject malformed values, including NaN and unknown enum objects, before scoring."""
    if set(terms) != set(case["negotiable"]):
        return False
    for field, value in terms.items():
        options = case.get("term_options", {}).get(field)
        if options and value not in options:
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
        if field == "unit_price" and (type(value) not in (int, float) or not 0 < value < 1e8):
            return False
        if field not in ("effective_date", "incoterm", "index_clause"):
            if value is not None and (type(value) not in (int, float) or value < 0):
                return False
    try:
        return must_haves_ok(case, terms) and math.isfinite(total_cost(case, terms))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def alternative_cost(case):
    alt = case["buyer"].get("alternative") or {}
    terms = alt.get("terms", {})
    if alt.get("kind") not in (None, "none") and terms.get("unit_price") and must_haves_ok(case, terms):
        return total_cost(case, terms)
    return math.inf


def evaluate(case, threads, intelligence):
    rows = []
    for sid in live_threads(threads):
        std = standard_terms(threads[sid])
        supplier = estimate(case, sid, std, intelligence)
        for sheet in latest(threads[sid]).get("term_sheets", []):
            sheet_id, terms = sheet.get("term_sheet_id"), sheet.get("terms", {})
            if not sheet_id or not terms.get("unit_price"):
                continue
            cost = total_cost(case, terms, std, supplier)
            ok = must_haves_ok(case, terms, std)
            rows.append(
                {
                    "supplier_id": sid,
                    "sheet_id": sheet_id,
                    "terms": closing_terms(case, terms, std),
                    "cost": cost,
                    "valid": ok,
                    "acceptable": ok and math.isfinite(cost) and cost <= alternative_cost(case),
                    "breakdown": cost_breakdown(case, terms, std, supplier),
                    "relationship_estimated": supplier is not None,
                }
            )
    return sorted(rows, key=lambda r: r["cost"])


def safe_close(case, threads, intelligence):
    for row in evaluate(case, threads, intelligence):
        if row["acceptable"]:
            return {
                "supplier_id": row["supplier_id"],
                "message": {"action": {"type": "accept", "term_sheet_id": row["sheet_id"]}},
            }
    from .action_policy import NegotiationBlocked

    raise NegotiationBlocked("NO_ELIGIBLE_OFFER")


def candidate_packages(case, events, sid, intelligence, anchor=0.18):
    """Beam search the legal non-price terms, then price equivalent alternatives.

    No access to opponent floors or benchmark state: anchors depend on observed opening offers.
    """
    _, current = standing(events)
    std = standard_terms(events)
    opening = next(
        (
            s["terms"]["unit_price"]
            for m in events
            if m.get("from") != "buyer"
            for s in m.get("term_sheets", [])
            if s["terms"].get("unit_price")
        ),
        current["unit_price"],
    )
    rounds = sum(m.get("from") == "buyer" for m in events)
    progress = min(rounds / max(1, case.get("round_limit", 10) - 2), 1)
    price = opening * (1 - anchor + anchor * progress**1.8)
    # Only concede against observable resistance; avoid paying above the standing ask.
    price = min(price, current["unit_price"])
    if rounds >= max(3, round(case.get("round_limit", 10) * 0.4)):
        observations = [
            s["terms"]["unit_price"]
            for m in events
            if m.get("from") != "buyer"
            for s in m.get("term_sheets", [])
            if s["terms"].get("unit_price")
        ]
        changes = [a - b for a, b in zip(observations[-4:-1], observations[-3:], strict=False) if a > b]
        step = sorted(changes)[len(changes) // 2] if changes else 0
        # Extrapolate a small observed step instead of chasing an unobserved floor into an ultimatum.
        price = min(current["unit_price"], max(price, current["unit_price"] - min(step, current["unit_price"] * 0.03)))
    supplier = estimate(case, sid, std, intelligence)

    def cost(t):
        try:
            return total_cost(case, t, std, supplier)
        except (TypeError, ValueError, KeyError, ZeroDivisionError):
            return math.inf

    seeds = [complete_offer(case, {**std, **current, "unit_price": round(price, 2)})]
    # Include neutral buyer-plan defaults, especially full horizon and volume coverage.
    seeds.append(complete_offer(case, {"unit_price": round(price, 2)}))
    beam = seeds
    for field in case["negotiable"]:
        if field == "unit_price":
            continue
        values = case.get("term_options", {}).get(field, [])
        if not values:
            continue
        expanded = [dict(t, **{field: v}) for t in beam for v in values]
        # Intermediate candidates may violate another must-have that a later coordinate fixes.
        unique = {str(t): t for t in expanded}
        beam = sorted(unique.values(), key=cost)[:32]
        # Preserve an early-payment path to discover cash-constrained suppliers.
        if field == "payment_days" and values:
            beam += [dict(t, payment_days=min(v for v in values if v is not None)) for t in seeds]
    legal = sorted((t for t in beam if valid_terms(case, t)), key=cost)
    if supplier and case["buyer"].get("category_goal") in {"stabilise", "secure_supply"}:
        # A strategic supplier's health can make a higher cash price the cheaper total deal.
        revised = []
        for t in legal:
            lo, hi = price, opening * 1.15
            for _ in range(28):
                a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
                if cost(dict(t, unit_price=a)) < cost(dict(t, unit_price=b)):
                    hi = b
                else:
                    lo = a
            revised.append(dict(t, unit_price=round((lo + hi) / 2, 2)))
        legal = sorted(revised, key=cost)
    if not legal:
        return []
    best, target = legal[0], cost(legal[0])
    packages = [{"id": "A", "label": "Buyer value", "terms": best, "cost": target}]
    # Equal-cost alternatives reveal supplier preferences; price adjusts for the buyer's financing cost.
    for label, days in [
        ("Cash flow", min(case.get("term_options", {}).get("payment_days") or [30])),
        ("Balanced", case["buyer"]["reference_payment_days"]),
    ]:
        t = complete_offer(case, {**best, "payment_days": days})
        base = cost(t)
        bumped = dict(t, unit_price=round(t["unit_price"] + 1, 2))
        derivative = cost(bumped) - base
        if derivative > 0:
            t["unit_price"] = round(max(0.01, t["unit_price"] + (target - base) / derivative), 2)
        if valid_terms(case, t) and t not in [p["terms"] for p in packages]:
            packages.append({"id": chr(65 + len(packages)), "label": label, "terms": t, "cost": cost(t)})
    return packages[:3]
