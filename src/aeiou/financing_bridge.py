"""Isolated routine-renewal experiment: price an intermediate payment exchange."""

import copy
from itertools import combinations

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest

from .economic_beliefs import total_cost
from .learned_portfolio import LearnedPortfolioNegotiator
from .optimizer import alternative_cost, estimate, valid_terms
from .reciprocity import same_structure
from .scenario_planner import equivalent_package


def financing_bridge(case, events, packages, standard, cost, ceiling):
    if (
        case["buyer"].get("category_goal") != "routine"
        or not {"unit_price", "payment_days", "contract_months"} <= set(case["negotiable"])
        or len(packages) < 2
        or latest(events).get("status") != "open"
        or not any(m.get("from") == "buyer" for m in events)
    ):
        return packages
    quotes = []
    for sheet in latest(events).get("term_sheets", []):
        full = closing_terms(case, sheet.get("terms", {}), standard)
        t = {f: full.get(f) for f in case["negotiable"]}
        if valid_terms(case, t):
            quotes.append(t)
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]
    choices = []
    for a, b in combinations(quotes, 2):
        cash, credit = sorted((a, b), key=lambda t: t["payment_days"])
        if cash["payment_days"] >= credit["payment_days"] or cash["unit_price"] >= credit["unit_price"]:
            continue
        if any(cash[f] != credit[f] for f in case["negotiable"] if f not in {"unit_price", "payment_days"}):
            continue  # do not attribute a bundled price difference to financing
        durations = sorted(
            d for d in case.get("term_options", {}).get("contract_months", []) if d > cash["contract_months"]
        )
        days = [
            d
            for d in case.get("term_options", {}).get("payment_days", [])
            if cash["payment_days"] < d < credit["payment_days"]
        ]
        if not durations or not days:
            continue
        best = min(quotes, key=cost)
        tick_saving = max(0.01, cost(best) - cost({**best, "unit_price": best["unit_price"] - 0.01}))
        budget = min(ceiling, cost(best) - tick_saving)
        for day in days:
            shape = {**cash, "payment_days": day, "contract_months": durations[0]}
            if shape["contract_months"] < case["buyer"]["horizon_years"] * 12:
                continue
            terms = equivalent_package(case, best, shape, cost, target_cost=budget)
            if terms is None or terms["unit_price"] < cash["unit_price"]:
                continue  # exchange payment flexibility rather than demand a deeper cash discount
            if any(same_structure(p, terms) and p["unit_price"] > terms["unit_price"] for p in previous):
                continue
            if terms in previous or not valid_terms(case, terms) or cost(terms) > budget + 0.001:
                continue
            choices.append(
                (abs(day - (cash["payment_days"] + credit["payment_days"]) / 2), cost(terms), terms, cash, credit)
            )
    if not choices:
        return packages
    _, value, terms, cash, credit = min(choices, key=lambda row: row[:2])
    result = copy.deepcopy(packages)
    match = next((p for p in result if same_structure(p["terms"], terms)), None)
    if match is not None and match["id"] == "A":
        return packages
    slot = match or next((p for p in reversed(result) if p["id"] != "A"), None)
    if slot is None:
        return packages
    slot.update(
        terms=terms,
        cost=value,
        observed_tradeoff=True,
        label="Intermediate payment for a longer renewal commitment",
        reference_quotes={"cash": cash, "credit": credit},
        limitation="Quoted endpoints; intermediate supplier acceptance is unverified.",
    )
    return result


class FinancingBridgeNegotiator(LearnedPortfolioNegotiator):
    """Explicit runner opt-in; leaves the production factory unchanged."""

    def packages(self, case, threads):
        offers = super().packages(case, threads)
        for sid, packages in offers.items():
            standard = standard_terms(threads[sid])
            supplier = estimate(case, sid, standard, self.intelligence)
            revised = financing_bridge(
                case,
                threads[sid],
                packages,
                standard,
                lambda t, std=standard, model=supplier: total_cost(case, t, std, model),
                alternative_cost(case),
            )
            if revised == packages:
                continue
            offers[sid] = revised
            protected = [p["id"] for p in revised if p.get("reference_quotes")]
            controller = self.support[sid]["controller"]
            controller["candidate_ids"] = list(dict.fromkeys(["A", *protected, *controller.get("candidate_ids", [])]))[
                :3
            ]
            self.support[sid]["plan"]["packages"] = revised
        return offers
