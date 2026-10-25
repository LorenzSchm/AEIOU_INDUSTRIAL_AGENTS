"""Explicit experiment: keep price/payment discovery on a contractual renewal date."""

import copy
import json
from datetime import date

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest

from .economic_beliefs import total_cost
from .learned_portfolio import LearnedPortfolioNegotiator
from .optimizer import alternative_cost, estimate, valid_terms
from .reciprocity import same_structure


def focused_renewal_case(case, pkg):
    if not {"effective_date", "payment_days", "unit_price"} <= set(case["negotiable"]):
        return case, None
    contracts = []
    for ref in case.get("refs", []):
        if not ref.startswith("ctr:"):
            continue
        fact = pkg.lookup(ref) or {}
        if fact.get("supplier") not in case["suppliers"] or not fact.get("valid_to"):
            continue
        try:
            expiry = date.fromisoformat(fact["valid_to"])
        except (ValueError, TypeError):
            continue
        contracts.append((expiry, ref))
    # Ambiguous multi-contract/multi-supplier cases retain the ordinary strategy.
    if len(contracts) != 1 or len(case["suppliers"]) != 1:
        return case, None
    expiry, ref = contracts[0]
    options = case.get("term_options", {}).get("effective_date", [])
    try:
        choices = [d for d in options if date.fromisoformat(d) > expiry]
    except (ValueError, TypeError):
        return case, None
    if not choices:
        return case, None
    selected = min(choices)
    return case, {
        "contract_ref": ref,
        "contract_valid_to": expiry.isoformat(),
        "selected_effective_date": selected,
        "limitation": "Experimental date focus, not proof that a later date is uneconomic or unacceptable.",
    }


def renewal_counter(case, events, sid, intelligence, target_date):
    """If the ordinary portfolio drops the renewal, counter its live quote safely."""
    if latest(events).get("status") != "open":
        return []
    std = standard_terms(events)
    supplier = estimate(case, sid, std, intelligence)
    cost = lambda terms: total_cost(case, terms, std, supplier)  # noqa: E731
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]
    proposals = []
    for sheet in latest(events).get("term_sheets", []):
        full = closing_terms(case, sheet.get("terms", {}), std)
        quote = {f: full.get(f) for f in case["negotiable"]}
        if quote.get("effective_date") != target_date or not valid_terms(case, quote):
            continue
        bid = {
            **quote,
            "unit_price": max(
                round(quote["unit_price"] * 0.99, 2),
                max((p["unit_price"] for p in previous if same_structure(p, quote)), default=0),
            ),
        }
        if valid_terms(case, bid) and cost(bid) < min(cost(quote), alternative_cost(case)) - 0.01:
            proposals.append(
                {
                    "id": "A",
                    "terms": bid,
                    "cost": cost(bid),
                    "label": "Bounded counter on the current renewal quote",
                    "observed_tradeoff": True,
                }
            )
    return [min(proposals, key=lambda p: p["cost"])] if proposals else []


class RenewalFocusedNegotiator(LearnedPortfolioNegotiator):
    """Not registered in the production factory; the trial runner opts in."""

    def packages(self, case, threads):
        offers = super().packages(case, threads)
        _, audit = focused_renewal_case(case, self.pkg)
        self.renewal_audit = None
        if audit is None:
            return offers
        for sid, packages in offers.items():
            selected = [p for p in packages if p["terms"].get("effective_date") == audit["selected_effective_date"]]
            if not selected:
                selected = renewal_counter(case, threads[sid], sid, self.intelligence, audit["selected_effective_date"])
            if not selected:
                continue  # never create an empty portfolio or invent a price
            selected = copy.deepcopy(selected)
            if not any(p["id"] == "A" for p in selected):
                selected.sort(key=lambda p: p["cost"])
                selected[0]["id"] = "A"
            offers[sid] = selected
            self.support[sid]["controller"]["candidate_ids"] = [p["id"] for p in selected]
            self.support[sid]["plan"]["packages"] = selected
            self.support[sid]["plan"]["recommendation"] = selected[0]["id"]
            self.renewal_audit = audit
        return offers

    def render_plan(self, case, threads, offers, best):
        payload = json.loads(super().render_plan(case, threads, offers, best))
        if self.renewal_audit:
            payload["proposal_contract"]["legal_values"]["effective_date"] = [
                self.renewal_audit["selected_effective_date"]
            ]
        return json.dumps(payload, ensure_ascii=False, default=str)

    def guard(self, case, threads, data, candidates):
        if self.renewal_audit and data.get("action") == "send":
            selected = set(data.get("candidate_ids") or [])
            if any(
                p["id"] in selected
                and p["terms"].get("effective_date") != self.renewal_audit["selected_effective_date"]
                for p in candidates.get(data.get("supplier_id"), [])
            ):
                return self.reject("Proposal leaves the experimental contractual renewal date")
        return super().guard(case, threads, data, candidates)

    def decide(self, case, threads):
        self.renewal_audit = None
        move = super().decide(case, threads)
        if self.decisions and self.renewal_audit:
            self.decisions[-1]["renewal_focus"] = self.renewal_audit
        return move
