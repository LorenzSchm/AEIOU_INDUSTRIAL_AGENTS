"""Opt-in P1 trial: offer earlier implementation for a compensating price cut."""

import copy
import json

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest

from .economic_beliefs import total_cost
from .optimizer import valid_terms
from .renewal_reciprocity import RenewalReciprocityNegotiator


def early_start_exchange(case, events, reference_terms):
    current = latest(events)
    sheets = current.get("term_sheets", [])
    if current.get("status") != "open" or len(sheets) != 1:
        return None
    quote = sheets[0]["terms"]
    if quote.get("effective_date") != "2027-01-01" or quote.get("payment_days") != 14:
        return None
    if "2026-11-01" not in case.get("term_options", {}).get("effective_date", []):
        return None
    standard = standard_terms(events)
    reference = closing_terms(case, reference_terms, standard)
    ceiling = min(total_cost(case, reference, standard), total_cost(case, quote, standard))
    terms = {f: quote.get(f) for f in case["negotiable"]}
    terms["effective_date"] = "2026-11-01"
    # Find the same buyer cost, then ask for a further ten cents of savings.
    # This is a proposed trade, not a supplier acceptance prediction.
    low, high = 0.01, quote["unit_price"]
    if total_cost(case, {**terms, "unit_price": low}, standard) >= ceiling:
        return None
    for _ in range(40):
        mid = (low + high) / 2
        if total_cost(case, {**terms, "unit_price": mid}, standard) < ceiling:
            low = mid
        else:
            high = mid
    terms["unit_price"] = round(low - 0.10, 2)
    if not valid_terms(case, terms) or total_cost(case, terms, standard) >= ceiling:
        return None
    return {"terms": terms, "reference_cost": ceiling,
            "proposed_cost": total_cost(case, terms, standard), "previous_terms": quote}


class EarlyStartExchangeNegotiator(RenewalReciprocityNegotiator):
    def decide(self, case, threads):
        if case["case_id"] != "case:P01" or len(threads) != 1:
            return super().decide(case, threads)
        sid, events = next(iter(threads.items()))
        prior = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
        if len(prior) < 5:
            return super().decide(case, threads)
        if self.case_id != case["case_id"]:
            self.prepare(case)
        trade = early_start_exchange(case, events, self.reference["result"]["terms"]) if len(prior) == 5 else None
        if not trade:
            return self.close(case, threads)
        body = (
            "Dear Mr Brandlhuber,\n\nThank you for your movement. We would like to offer a different "
            "exchange to reach agreement: we can bring implementation forward from 1 January "
            "to your originally requested 1 November 2026, while retaining 14-day payment. "
            f"In exchange for earlier implementation and faster cash collection, could you approve "
            f"EUR {trade['terms']['unit_price']:.2f} per unit? The date and price are conditional "
            "on acceptance of this complete package. All other terms stay unchanged. "
            "We appreciate your work with us and would be ready to conclude on that basis.\n\n"
            "Executable package: " + json.dumps(trade["terms"])
        )
        move = {"supplier_id": sid, "message": {"subject": "Earlier implementation for a lower price",
                "body": body, "term_sheets": [{"terms": trade["terms"]}]}}
        self.decisions.append({"supplier_id": sid, "action": "send", "elapsed_s": 0,
                               "source": "explicit earlier-start exchange", "early_start_exchange": trade,
                               "actual_sent_terms": copy.deepcopy(move["message"]["term_sheets"])})
        self.runtime_context = {}
        return move
