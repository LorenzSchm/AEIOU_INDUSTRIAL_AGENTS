"""Explicit experiment: discover quotes on one legal payment horizon."""

import copy
import json

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest

from .economic_beliefs import total_cost
from .learned_portfolio import LearnedPortfolioNegotiator
from .optimizer import alternative_cost, estimate, valid_terms
from .reciprocity import same_structure


def focused_payment_packages(case, events, packages, standard, supplier, target):
    """Keep coverage, legal terms and the live-quote/alternative cost ceiling.

    This experiment deliberately changes the opening portfolio's payment focus,
    so it uses the live quote ceiling, not the old cash-anchor cost envelope.
    It does not alter case options, scoring, supplier costs or model weights.
    """
    if latest(events).get("status") != "open" or target not in case.get("term_options", {}).get("payment_days", []):
        return packages
    cost = lambda t: total_cost(case, t, standard, supplier)  # noqa: E731
    quotes = [closing_terms(case, s.get("terms", {}), standard) for s in latest(events).get("term_sheets", [])]
    quotes = [q for q in quotes if valid_terms(case, {f: q.get(f) for f in case["negotiable"]})]
    if not quotes:
        return packages
    ceiling = min(alternative_cost(case), min(cost(q) for q in quotes) - 0.01)
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]

    def allowed(t):
        return (
            valid_terms(case, t)
            and cost(t) <= ceiling
            and t.get("volume_per_year", case["buyer"]["need_per_year"]) >= case["buyer"]["need_per_year"]
            and t.get("contract_months", 12 * case["buyer"]["horizon_years"]) >= 12 * case["buyer"]["horizon_years"]
            and all(t["unit_price"] >= p["unit_price"] for p in previous if same_structure(p, t))
        )

    candidates = []
    for p in packages:
        t = {**p["terms"], "payment_days": target}
        if allowed(t):
            candidates.append({**copy.deepcopy(p), "terms": t, "cost": cost(t), "payment_focus": True})
    if not candidates:
        for q in quotes:
            if q["payment_days"] != target:
                continue
            base = {f: q.get(f) for f in case["negotiable"]}
            floor = max((p["unit_price"] for p in previous if same_structure(p, base)), default=0)
            for fraction in (0.995, 0.99, 0.985, 0.98):
                t = {**base, "unit_price": max(floor, round(q["unit_price"] * fraction, 2))}
                if t["unit_price"] < q["unit_price"] and allowed(t):
                    candidates.append(
                        {
                            "id": "A",
                            "terms": t,
                            "cost": cost(t),
                            "payment_focus": True,
                            "label": "Bounded counter on the current payment quote",
                        }
                    )
        if candidates:
            candidates = [min(candidates, key=lambda p: p["cost"])]
    if not candidates:
        return packages
    unique = []
    for p in sorted(candidates, key=lambda p: p["cost"]):
        if not any(same_structure(p["terms"], q["terms"]) for q in unique):
            p["id"] = "ABC"[len(unique)]
            unique.append(p)
        if len(unique) == 3:
            break
    return unique


class PaymentFocusedNegotiator(LearnedPortfolioNegotiator):
    """Only enabled by an explicit experiment runner, never by the agent factory."""

    def packages(self, case, threads):
        offers = super().packages(case, threads)
        self.focused_suppliers = set()
        options = case.get("term_options", {}).get("payment_days", [])
        if len(case["suppliers"]) != 1 or "payment_days" not in case["negotiable"] or not options:
            return offers
        self.target_payment = max(options)
        for sid, packages in offers.items():
            standard = standard_terms(threads[sid])
            revised = focused_payment_packages(
                case,
                threads[sid],
                packages,
                standard,
                estimate(case, sid, standard, self.intelligence),
                self.target_payment,
            )
            if not revised or not all(p.get("payment_focus") for p in revised):
                continue
            offers[sid] = revised
            self.focused_suppliers.add(sid)
            self.support[sid]["controller"]["candidate_ids"] = [p["id"] for p in revised]
            self.support[sid]["plan"]["packages"] = revised
        return offers

    def render_plan(self, case, threads, offers, best):
        payload = json.loads(super().render_plan(case, threads, offers, best))
        if self.controller["supplier_id"] in self.focused_suppliers:
            payload["proposal_contract"]["legal_values"]["payment_days"] = [self.target_payment]
            payload["proposal_contract"]["experiment"] = "Keep payment fixed; ask for a priced quote on this horizon."
        return json.dumps(payload, ensure_ascii=False, default=str)

    def guard(self, case, threads, data, candidates):
        sid = data.get("supplier_id")
        if data.get("action") == "send" and sid in self.focused_suppliers:
            selected = [p for p in candidates.get(sid, []) if p["terms"].get("payment_days") == self.target_payment]
            if not selected or not any(p["id"] == "A" for p in selected):
                return self.reject("Planner discarded the focused payment anchor")
            candidates = {**candidates, sid: selected}
            ids = [p["id"] for p in selected]
            data = {**data, "candidate_ids": ids}
            self.controller["candidate_ids"] = ids
        return super().guard(case, threads, data, candidates)
