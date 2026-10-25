"""Opt-in P1 trial: offer earlier implementation for a compensating price cut."""

import copy
import json

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest

from .economic_beliefs import total_cost
from .optimizer import valid_terms
from .renewal_reciprocity import RenewalReciprocityNegotiator, bounded_reciprocal_bid


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
    def __init__(self, *args, exchange_after=5, followup=False, **kwargs):
        super().__init__(*args, **kwargs)
        if exchange_after not in (4, 5):
            raise ValueError("Date exchange is limited to the fourth or fifth buyer proposal")
        self.exchange_after = exchange_after
        self.exchange_followup = followup
        self.last_exchange = None

    def decide(self, case, threads):
        if case["case_id"] != "case:P01" or len(threads) != 1:
            return super().decide(case, threads)
        sid, events = next(iter(threads.items()))
        prior = [m for m in events if m.get("from") == "buyer" and m.get("term_sheets")]
        if len(prior) < self.exchange_after:
            return super().decide(case, threads)
        if self.case_id != case["case_id"]:
            self.prepare(case)
        if (self.exchange_followup and self.last_exchange
                and len(prior) == self.exchange_after + 1):
            counter = bounded_reciprocal_bid(case, events,
                                            round(self.last_exchange["terms"]["unit_price"] + 0.09, 2), 0.5)
            if (counter and counter["terms"].get("effective_date") == "2026-11-01"
                    and counter["terms"].get("payment_days") == 14
                    and total_cost(case, counter["terms"], standard_terms(events))
                    < self.last_exchange["reference_cost"]):
                message = {"subject": "Closing the earlier-start package", "body": (
                    "Dear Mr Brandlhuber,\n\nThank you for pricing the earlier implementation. "
                    f"We can increase our proposed price from EUR {counter['previous_price']:.2f} "
                    f"to EUR {counter['terms']['unit_price']:.2f}, keeping the 1 November start "
                    "and 14-day payment. If you can issue a binding quote on this complete "
                    "package, we are ready to accept it as our next action.\n\nExecutable package: "
                    + json.dumps(counter["terms"])), "term_sheets": [{"terms": counter["terms"]}]}
                self.decisions.append({"supplier_id": sid, "action": "send", "elapsed_s": 0,
                                       "source": "bounded date-exchange followup", "date_followup": counter,
                                       "actual_sent_terms": copy.deepcopy(message["term_sheets"])})
                self.runtime_context = {}
                return {"supplier_id": sid, "message": message}
            return self.close(case, threads)
        trade = (early_start_exchange(case, events, self.reference["result"]["terms"])
                 if len(prior) == self.exchange_after else None)
        if not trade:
            return self.close(case, threads)
        self.last_exchange = copy.deepcopy(trade)
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
