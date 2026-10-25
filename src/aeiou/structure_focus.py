"""Opt-in controlled experiments that hold selected legal commercial terms fixed."""

import copy
import json
import math

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest

from .economic_beliefs import total_cost
from .learned_portfolio import LearnedPortfolioNegotiator
from .optimizer import alternative_cost, estimate, valid_terms
from .reciprocity import same_structure


def focused_structure(case, events, packages, standard, supplier, focus):
    """Explore a declared structure below current quote and alternative costs.

    No extra volume, reduced horizon coverage, illegal field or retracted prior
    price concession is admitted. Focus is experimental configuration, not a
    hidden scoring parameter; supplier response and achievable price are unknown.
    """
    if (
        not focus
        or latest(events).get("status") != "open"
        or any(
            f not in case["negotiable"] or v not in case.get("term_options", {}).get(f, []) for f, v in focus.items()
        )
    ):
        return packages
    cost = lambda t: total_cost(case, t, standard, supplier)  # noqa: E731
    quotes = [closing_terms(case, s.get("terms", {}), standard) for s in latest(events).get("term_sheets", [])]
    # An opening quote may lie outside the proposal grid and violate a buyer
    # must-have (e.g. its measured quality level).
    # It can bound expenditure but is never thereby made acceptable: every
    # outgoing proposal still passes valid_terms, including all must-haves.
    quotes = [
        q
        for q in quotes
        if type(q.get("unit_price")) in (int, float)
        and 0 < q["unit_price"] < 1e8
        and math.isfinite(cost(q))
        and cost(q) > 0
    ]
    if not quotes:
        return packages
    ceiling = min(alternative_cost(case), min(cost(q) for q in quotes) - 0.01)
    history = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]

    def allowed(t):
        return (
            valid_terms(case, t)
            and cost(t) <= ceiling
            and t.get("volume_per_year", case["buyer"]["need_per_year"]) == case["buyer"]["need_per_year"]
            and t.get("contract_months", 12 * case["buyer"]["horizon_years"]) >= 12 * case["buyer"]["horizon_years"]
            and all(t["unit_price"] >= p["unit_price"] for p in history if same_structure(p, t))
        )

    options = []
    for p in packages:
        terms = {**p["terms"], **focus}
        if allowed(terms):
            options.append({**copy.deepcopy(p), "terms": terms, "cost": cost(terms), "structure_focus": True})
    if not options:
        for q in quotes:
            if not all(q.get(f) == v for f, v in focus.items()):
                continue
            base = {f: q.get(f) for f in case["negotiable"]}
            floor = max((p["unit_price"] for p in history if same_structure(p, base)), default=0)
            for fraction in (0.995, 0.99, 0.985, 0.98):
                terms = {**base, "unit_price": max(floor, round(q["unit_price"] * fraction, 2))}
                if terms["unit_price"] < q["unit_price"] and allowed(terms):
                    options.append(
                        {
                            "terms": terms,
                            "cost": cost(terms),
                            "structure_focus": True,
                            "label": "Bounded counter on the current commercial structure",
                        }
                    )
        if options:
            options = [min(options, key=lambda p: p["cost"])]
    if not options:
        return packages
    result = []
    for p in sorted(options, key=lambda p: p["cost"]):
        if any(same_structure(p["terms"], q["terms"]) for q in result):
            continue
        p["id"] = "ABC"[len(result)]
        result.append(p)
        if len(result) == 3:
            break
    return result


class StructureFocusedNegotiator(LearnedPortfolioNegotiator):
    """Explicit runner configuration; does not replace any production strategy."""

    def __init__(self, *args, focus_terms, **kwargs):
        super().__init__(*args, **kwargs)
        self.focus_terms = dict(focus_terms)
        self.focused_suppliers = set()

    def packages(self, case, threads):
        offers = super().packages(case, threads)
        self.focused_suppliers = set()
        if len(case["suppliers"]) != 1:
            return offers
        for sid, packages in offers.items():
            standard = standard_terms(threads[sid])
            revised = focused_structure(
                case,
                threads[sid],
                packages,
                standard,
                estimate(case, sid, standard, self.intelligence),
                self.focus_terms,
            )
            if not revised or not all(p.get("structure_focus") for p in revised):
                continue
            offers[sid] = revised
            self.focused_suppliers.add(sid)
            self.support[sid]["controller"]["candidate_ids"] = [p["id"] for p in revised]
            self.support[sid]["plan"]["packages"] = revised
        return offers

    def render_plan(self, case, threads, offers, best):
        payload = json.loads(super().render_plan(case, threads, offers, best))
        if self.controller["supplier_id"] in self.focused_suppliers:
            payload["proposal_contract"]["legal_values"].update({f: [v] for f, v in self.focus_terms.items()})
            payload["proposal_contract"]["experiment"] = "Keep the focused commercial terms; negotiate a binding price."
        return json.dumps(payload, ensure_ascii=False, default=str)

    def guard(self, case, threads, data, candidates):
        sid = data.get("supplier_id")
        if data.get("action") == "send" and sid in self.focused_suppliers:
            selected = [
                p for p in candidates.get(sid, []) if all(p["terms"].get(f) == v for f, v in self.focus_terms.items())
            ]
            if not selected or not any(p["id"] == "A" for p in selected):
                return self.reject("Planner discarded the experiment's commercial anchor")
            candidates = {**candidates, sid: selected}
            ids = [p["id"] for p in selected]
            data = {**data, "candidate_ids": ids}
            self.controller["candidate_ids"] = ids
        return super().guard(case, threads, data, candidates)
