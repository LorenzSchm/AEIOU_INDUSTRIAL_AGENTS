"""Opt-in experiment: compensate a payment extension from live quoted trades."""

import copy
import math
from itertools import combinations

from arena_kit.cost import closing_terms
from arena_kit.cost import total_cost as direct_cost
from arena_kit.docs import standard_terms
from arena_kit.play import latest, moves_left

from .economic_beliefs import total_cost
from .learned_portfolio import LearnedPortfolioNegotiator
from .optimizer import alternative_cost, estimate, valid_terms
from .quote_financing import implied_rate
from .reciprocity import same_structure


def payment_extension(case, events, packages, standard, supplier, confidence, ceiling):
    """Bound a secant extrapolation; quoted premiums are not financing facts.

    Extend by at most 30 days and twice the observed payment span. Require
    direct savings, savings under quote-implied rate/cost sensitivities, and
    <=0.25% downside under the existing point model. Never use old quoted prices
    as executable offers or modify the case's legal options/cost horizon.
    """
    if (
        not packages
        or not supplier
        or not isinstance(confidence, (int, float))
        or not 0 < confidence < 100
        or "payment_days" not in case["negotiable"]
        or latest(events).get("status") != "open"
        or moves_left(case, events) <= 2
        or not any(m.get("from") == "buyer" for m in events)
    ):
        return packages
    quotes = [closing_terms(case, s.get("terms", {}), standard) for s in latest(events).get("term_sheets", [])]
    quotes = [q for q in quotes if valid_terms(case, {f: q.get(f) for f in case["negotiable"]})]
    if len(quotes) < 2:
        return packages
    point = lambda t: total_cost(case, t, standard, supplier)  # noqa: E731
    best = min(quotes, key=point)
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]
    choices = []
    for first, second in combinations(quotes, 2):
        early, late = sorted((first, second), key=lambda q: q["payment_days"])
        rate = implied_rate(case, early, late, supplier)
        if rate is None or early["unit_price"] >= late["unit_price"]:
            continue
        span = late["payment_days"] - early["payment_days"]
        days = [
            d
            for d in case["term_options"]["payment_days"]
            if late["payment_days"] < d <= late["payment_days"] + min(30, 2 * span)
        ]
        if not days:
            continue
        day = max(days)
        price = late["unit_price"] + (late["unit_price"] - early["unit_price"]) * (day - late["payment_days"]) / span
        terms = {f: late.get(f) for f in case["negotiable"]}
        terms.update(payment_days=day, unit_price=math.ceil(price * 100 - 1e-7) / 100)
        if not valid_terms(case, terms) or terms in previous:
            continue
        if any(same_structure(p, terms) and p["unit_price"] > terms["unit_price"] for p in previous):
            continue
        if point(terms) > min(ceiling, point(best) * 1.0025):
            continue
        saving = min(direct_cost(case, q, standard) for q in quotes) - direct_cost(case, terms, standard)
        if saving <= 0.01:
            continue
        deltas = []
        for factor in (1 - confidence / 100, 1, 1 + confidence / 100):
            for assumption in (max(0, rate - 0.01), rate, min(1, rate + 0.01)):
                model = {**supplier, "full_cost": supplier["full_cost"] * factor, "interest_rate": assumption}
                deltas.append(
                    direct_cost(case, terms, standard, model)
                    - min(direct_cost(case, q, standard, model) for q in quotes)
                )
        if any(not math.isfinite(d) or d >= -0.01 for d in deltas):
            continue
        choices.append((saving, terms, early, late, rate, max(deltas)))
    if not choices:
        return packages
    saving, terms, early, late, rate, worst = max(choices, key=lambda r: r[0])
    result = copy.deepcopy(packages)
    slot = next((p for p in result if same_structure(p["terms"], terms)), None)
    if slot is None:
        slot = next((p for p in reversed(result) if p["id"] != "A"), None)
    if slot is None:
        return packages
    slot.update(
        terms=terms,
        cost=point(terms),
        observed_tradeoff=True,
        payment_extension=True,
        label="Compensated payment extension from current quotes",
        reference_quotes=[early, late],
        quoted_rate=rate,
        direct_saving=saving,
        worst_sensitivity_delta=worst,
        limitation="Bounded quote extrapolation; supplier acceptance and future score are unknown.",
    )
    return result


class PaymentExtensionNegotiator(LearnedPortfolioNegotiator):
    """Explicit experiment only; production and other track variants are unchanged."""

    def packages(self, case, threads):
        offers = super().packages(case, threads)
        for sid, packages in offers.items():
            standard = standard_terms(threads[sid])
            revised = payment_extension(
                case,
                threads[sid],
                packages,
                standard,
                estimate(case, sid, standard, self.intelligence),
                self.intelligence.get("estimates", {}).get(sid, {}).get("confidence_pct"),
                alternative_cost(case),
            )
            if revised == packages:
                continue
            # Keep an anchor plus the test package. The planner cannot silently
            # add a cheaper executable fallback that ends this term exchange.
            revised = [p for p in revised if p["id"] == "A" or p.get("payment_extension")]
            offers[sid] = revised
            self.support[sid]["controller"].update(
                action="send",
                candidate_ids=[p["id"] for p in revised],
                reason="Test a compensated payment extension supported by current quotes",
            )
            self.support[sid]["plan"]["packages"] = revised
        return offers

    def guard(self, case, threads, data, candidates):
        sid = data.get("supplier_id")
        if data.get("action") == "send" and any(p.get("payment_extension") for p in candidates.get(sid, [])):
            candidates = {
                **candidates,
                sid: [p for p in candidates[sid] if p["id"] == "A" or p.get("payment_extension")],
            }
            ids = [p["id"] for p in candidates[sid]]
            data = {**data, "candidate_ids": ids}
            self.controller["candidate_ids"] = ids
        return super().guard(case, threads, data, candidates)
