"""Bounded multi-issue proposals derived from public terms, never benchmark targets."""

import itertools
import math

from arena_kit.cost import closing_terms, complete_offer
from arena_kit.cost import total_cost as direct_cost
from arena_kit.docs import standard_terms

from .optimizer import valid_terms
from .uncertainty import robust_quote_improvement, weighted_quote_improvement


def structure(terms):
    return {k: v for k, v in terms.items() if k != "unit_price"}


def commercial_shapes(case, base, current):
    """Explore coupled issues that a one-coordinate optimizer cannot price jointly."""
    options, fields = case.get("term_options", {}), case["negotiable"]
    if {"ppm_commitment", "chargeback_eur_per_complaint"} <= set(fields):
        ppms = sorted(options.get("ppm_commitment", []), reverse=True)
        compensation = sorted(options.get("chargeback_eur_per_complaint", []))
        if compensation:
            # Balanced and light liability, both subject to the buyer's actual
            # quality limit. No target is taken from a historical winning deal.
            for charge in dict.fromkeys([compensation[len(compensation) // 2], compensation[0]]):
                for ppm in ppms:
                    terms = {**base, "ppm_commitment": ppm, "chargeback_eur_per_complaint": charge}
                    if valid_terms(case, terms):
                        yield "quality", terms
                        break
    if "index_clause" in fields:
        payments = options.get("payment_days", [base.get("payment_days")]) if "payment_days" in fields else [None]
        for clause, days in itertools.product(options.get("index_clause", []), payments):
            terms = {**base, "index_clause": clause}
            if "payment_days" in fields:
                terms["payment_days"] = days
            if valid_terms(case, terms):
                yield "index", terms
    if "lead_time_days" in fields and current.get("lead_time_days", 0) > case["buyer"]["need_lead_time_days"]:
        # Offer to pay for timely delivery, but never more than its buyer value.
        days = [d for d in options.get("lead_time_days", []) if d <= case["buyer"]["need_lead_time_days"]]
        if days:
            for source in (base, current):
                durations = options.get("contract_months", [source.get("contract_months")])
                for duration in durations:
                    terms = {**source, "lead_time_days": max(days)}
                    if "contract_months" in fields:
                        terms["contract_months"] = duration
                    if valid_terms(case, terms):
                        yield "delivery", terms
    if {"lead_time_days", "payment_days"} <= set(fields):
        # Follow the supplier's quoted cash preference while buying a discount
        # with the nearest legal delivery relaxation. Full cost prices lateness.
        cash = current.get("payment_days")
        if cash is not None and cash < base.get("payment_days", cash):
            for days in sorted(options.get("lead_time_days", [])):
                if days <= max(base.get("lead_time_days", 0), current.get("lead_time_days", 0)):
                    continue
                terms = {**base, "lead_time_days": days, "payment_days": cash}
                if valid_terms(case, terms):
                    yield "delivery_flex", terms
                    break


def portfolio_probes(case, events, base, cost, supplier, confidence, compensate, sensitivities=None):
    """Preserve A; add cost-compensated quality, risk and service alternatives.

    A bounded concession envelope is available only for structures not represented
    by A. It retains at least 75% of A's modeled improvement over the best
    current quote. Proposals must improve weighted economic sensitivities when
    available, otherwise all legacy cost stress points. Not an acceptance prediction.
    """
    if not events or events[-1].get("from") == "buyer" or events[-1].get("status") != "open":
        return []
    std = standard_terms(events)
    quotes = [
        {f: value for f, value in closing_terms(case, s.get("terms", {}), std).items() if f in case["negotiable"]}
        for s in events[-1].get("term_sheets", [])
    ]
    # An opening may violate our quality/coverage requirement; that is precisely
    # when a remediation portfolio is needed. It is only a cost reference, never
    # an acceptable deal. All outgoing proposals still pass valid_terms below.
    references = []
    for terms in quotes:
        try:
            if terms["unit_price"] > 0 and math.isfinite(cost(terms)):
                references.append(terms)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    quotes = references
    if not quotes:
        return []
    current = min(quotes, key=cost)
    standing_cost, anchor_cost = cost(current), cost(base)
    if anchor_cost >= standing_cost - 0.01:
        return []
    ceiling = anchor_cost + 0.25 * (standing_cost - anchor_cost)
    prior = [
        complete_offer(case, {**std, **s.get("terms", {})})
        for m in events
        if m.get("from") == "buyer"
        for s in m.get("term_sheets", [])
    ]
    grouped = {}
    for issue, shape in commercial_shapes(case, base, current):
        if issue == "delivery_flex" and not prior:
            continue  # Preserve the opening; bundle trades after observing a response.
        if structure(shape) == structure(base):
            continue
        # Keep strict equal-cost trades where possible. If the estimated
        # sustainability minimum blocks one, use the bounded standing-offer gap.
        probe = compensate(case, base, shape, cost, target_cost=ceiling if issue == "delivery" else None)
        if probe is None:
            if issue == "delivery_flex":
                continue  # Never give away cash AND service via the concession envelope.
            probe = compensate(case, base, shape, cost, target_cost=ceiling)
        if probe is None:
            continue
        previous = [p["unit_price"] for p in prior if structure(p) == structure(probe)]
        if previous:
            probe["unit_price"] = max(probe["unit_price"], max(previous))
        value = cost(probe)
        if not valid_terms(case, probe) or not math.isfinite(value) or value > ceiling + 0.001:
            continue
        if issue == "delivery_flex" and (value > anchor_cost + 0.001 or probe["unit_price"] >= base["unit_price"]):
            continue
        stresses = None
        if supplier and case["buyer"].get("category_goal") in {"stabilise", "secure_supply"}:
            stresses = (
                weighted_quote_improvement(case, probe, current, std, sensitivities)
                if sensitivities
                else robust_quote_improvement(case, probe, current, std, supplier, confidence)
            )
            if stresses is None:
                continue
        grouped.setdefault(issue, []).append(
            {
                "label": f"Conditional {issue} package",
                "terms": probe,
                "cost": value,
                "portfolio_probe": True,
                "issue": issue,
                "standing_cost": standing_cost,
                "portfolio_ceiling": ceiling,
                "estimate_stresses": stresses or [],
                "limitation": "Hypothetical package; supplier acceptance is unverified.",
            }
        )
    result = []
    for issue, probes in grouped.items():
        if issue == "index":
            # Retain a genuinely different risk structure. Favor a clause already
            # quoted publicly, then the least expensive compensated package.
            alternatives = [p for p in probes if p["terms"].get("index_clause") != base.get("index_clause")]
            if not alternatives:
                continue
            result.append(
                min(
                    alternatives,
                    key=lambda p: (
                        not any(structure(p["terms"]) == structure(q) for q in quotes),
                        # Isolate the new risk allocation before simultaneously
                        # changing cash terms. Cent rounding is not preference evidence.
                        abs(p["terms"].get("payment_days", 0) - current.get("payment_days", 0)),
                        cost({**p["terms"], "unit_price": base["unit_price"]}),
                        p["cost"],
                    ),
                )
            )
        elif issue == "quality":
            result.extend(probes[:2])
        else:
            result.append(min(probes, key=lambda p: p["cost"]))
    if sensitivities and "index_clause" in case["negotiable"] and not any(p["issue"] == "index" for p in result):
        # A noisy supplier-cost estimate may rule out every indexed exchange.
        # Reserve one experimental option: direct contractual cost must improve
        # even over A, expected sensitivity cost must beat the real quote, and
        # point-estimate downside is capped at the existing 0.5% guard tolerance.
        options = []
        quote_expected = sum(s["weight"] * direct_cost(case, current, std, s) for s in sensitivities)
        for issue, shape in commercial_shapes(case, base, current):
            if issue != "index" or shape.get("index_clause") == base.get("index_clause"):
                continue
            upper = min(base["unit_price"], current["unit_price"])
            for step in range(129):
                probe = {**shape, "unit_price": round(upper * (0.85 + 0.15 * step / 128), 2)}
                if any(probe["unit_price"] < p["unit_price"] for p in prior if structure(p) == structure(probe)):
                    continue
                value = cost(probe)
                if not valid_terms(case, probe) or value > standing_cost * 1.005:
                    continue
                if (
                    direct_cost(case, probe, std)
                    > min(direct_cost(case, base, std), direct_cost(case, current, std)) - 0.01
                ):
                    continue
                expected = sum(s["weight"] * direct_cost(case, probe, std, s) for s in sensitivities)
                if expected >= quote_expected - 0.01:
                    continue
                options.append(
                    {
                        "label": "Cost-compensated risk exchange",
                        "terms": probe,
                        "cost": value,
                        "portfolio_probe": True,
                        "issue": "index",
                        "standing_cost": standing_cost,
                        "bounded_uncertainty_probe": True,
                        "expected_cost": expected,
                        "expected_quote_cost": quote_expected,
                        "point_cost_limit": standing_cost * 1.005,
                        "limitation": "Uncalibrated sensitivities; point-cost downside limited to 0.5% of quote.",
                    }
                )
        if options:
            result.append(min(options, key=lambda p: p["expected_cost"]))
    return result


def merge_portfolio(packages, probes):
    # Existing compensated risk alternatives already answer the index question.
    # Do not crowd out a cash option with a second unobserved indexed variant.
    probes = [
        p
        for p in probes
        if p["issue"] != "index"
        or not any(
            other["terms"].get("index_clause") != packages[0]["terms"].get("index_clause") for other in packages[1:]
        )
    ]
    if not probes:
        return packages
    # Explicit uncertainty experiments stay visible; generic payment-only
    # alternatives must not crowd out the actual quality/risk negotiation.
    protected = [p for p in packages[1:] if p.get("uncertainty_probe") or p.get("feedback_probe")]
    ordered = [packages[0], *protected, *probes, *packages[1:]]
    selected = []
    for p in ordered:
        if any(structure(p["terms"]) == structure(other["terms"]) for other in selected):
            continue
        selected.append({**p, "id": chr(65 + len(selected))})
        if len(selected) == 3:
            break
    return selected
