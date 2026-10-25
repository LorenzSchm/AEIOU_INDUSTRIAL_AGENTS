"""Opt-in single closing counter; never treats a counter as an agreement."""

import copy
import json
import math

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest, moves_left

from .economic_beliefs import total_cost
from .learned_portfolio import LearnedPortfolioNegotiator
from .optimizer import alternative_cost, estimate, valid_terms
from .payment_focus import PaymentFocusedNegotiator
from .reciprocity import same_structure
from .renewal_focus import RenewalFocusedNegotiator
from .settlement import FIRM_CLOSE


def closing_probe(case, events, sheet_id, supplier, fraction):
    """One small, legal price counter to an open offer the controller would accept.

    This spends an additional negotiation turn and can lose the standing quote.
    It is an experiment, not a free improvement. The caller enforces one use.
    Direct cost must improve; modeled complete cost may worsen by at most 0.25%.
    """
    message = latest(events)
    if (
        not 0 < fraction <= 0.01
        or message.get("status") != "open"
        or moves_left(case, events) <= 2
        or FIRM_CLOSE.search(message.get("body", ""))
    ):
        return None
    sheet = next((s for s in message.get("term_sheets", []) if s.get("term_sheet_id") == sheet_id), None)
    if not sheet:
        return None
    std = standard_terms(events)
    quote = closing_terms(case, sheet.get("terms", {}), std)
    terms = {f: quote.get(f) for f in case["negotiable"]}
    if not valid_terms(case, terms):
        return None
    previous = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]
    if not previous:
        return None
    floor = max((p["unit_price"] for p in previous if same_structure(p, terms)), default=0)
    terms["unit_price"] = max(floor, round(quote["unit_price"] * (1 - fraction), 2))
    if terms["unit_price"] >= quote["unit_price"] or terms in previous or not valid_terms(case, terms):
        return None
    modeled = total_cost(case, terms, std, supplier)
    quote_modeled = total_cost(case, quote, std, supplier)
    direct = total_cost(case, terms, std)
    quote_direct = total_cost(case, quote, std)
    if (
        not all(math.isfinite(v) and v > 0 for v in [modeled, quote_modeled, direct, quote_direct])
        or direct >= quote_direct - 0.01
        or modeled > min(quote_modeled * 1.0025, alternative_cost(case))
    ):
        return None
    return {
        "terms": terms,
        "reference_sheet": sheet_id,
        "fraction": fraction,
        "direct_saving": quote_direct - direct,
        "modeled_cost_delta": modeled - quote_modeled,
        "limitation": "One extra counter may lose the standing offer; no acceptance prediction.",
    }


class ClosingProbeMixin:
    def __init__(self, *args, probe_fraction=0.005, **kwargs):
        if not 0 < probe_fraction <= 0.01:
            raise ValueError("Closing probe must be positive and at most one percent")
        super().__init__(*args, **kwargs)
        self.probe_fraction = probe_fraction
        self.probed_suppliers = set()

    def prepare(self, case):
        super().prepare(case)
        self.probed_suppliers = set()

    def decide(self, case, threads):
        move = super().decide(case, threads)
        sid = move["supplier_id"]
        action = move["message"].get("action", {})
        if action.get("type") != "accept" or sid in self.probed_suppliers:
            return move
        std = standard_terms(threads[sid])
        probe = closing_probe(
            case,
            threads[sid],
            action.get("term_sheet_id"),
            estimate(case, sid, std, self.intelligence),
            self.probe_fraction,
        )
        if not probe:
            return move
        self.probed_suppliers.add(sid)
        body = (
            "Thank you for working through the terms with us. We are close to agreement. "
            "Could you approve the small price adjustment in the complete package below? "
            "All other commercial terms match your current offer. This is a conditional "
            "counterproposal; please confirm whether you can issue a binding offer on it.\n\n"
            "Executable package: " + json.dumps(probe["terms"], ensure_ascii=False)
        )
        revised = {
            "supplier_id": sid,
            "message": {
                "subject": "Re: " + case["title"],
                "body": body,
                "term_sheets": [{"terms": copy.deepcopy(probe["terms"])}],
            },
        }
        self.decisions[-1].update(
            action="send",
            source="explicit one-time closing probe",
            closing_probe=probe,
            original_action=copy.deepcopy(action),
            actual_sent_terms=copy.deepcopy(revised["message"]["term_sheets"]),
        )
        return revised


class ClosingProbeNegotiator(ClosingProbeMixin, LearnedPortfolioNegotiator):
    pass


class ClosingProbePaymentNegotiator(ClosingProbeMixin, PaymentFocusedNegotiator):
    pass


class ClosingProbeRenewalNegotiator(ClosingProbeMixin, RenewalFocusedNegotiator):
    pass
