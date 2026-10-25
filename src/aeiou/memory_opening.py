"""Explicit known-context experiment: reopen from an actual past agreement.

This is retrieval of negotiated terms, not training, hidden-score optimization,
or evidence of generalization. Incompatible or stale commercial contexts fall
back to the ordinary agent. Production never imports this experimental class.
"""

import copy
import json
import math

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms
from arena_kit.play import latest

from .economic_beliefs import total_cost
from .learned_portfolio import LearnedPortfolioNegotiator
from .optimizer import alternative_cost, estimate, valid_terms


def memory_opening(case, threads, memory, intelligence, discount):
    if not 0 <= discount <= 0.02:
        return None
    old = memory.get("case") or {}
    result = memory.get("result") or {}
    sid = result.get("supplier_id")
    # Require the same commercial context, not merely a matching case label.
    fields = ("parts", "negotiable", "fixed_terms", "term_options", "buyer")
    if (
        not result.get("deal")
        or not result.get("must_haves_ok")
        or sid not in case["suppliers"]
        or sid not in old.get("suppliers", [])
        or sid not in threads
        or any(case.get(k) != old.get(k) for k in fields)
        or any(m.get("from") == "buyer" for events in threads.values() for m in events)
        or latest(threads[sid]).get("status") != "open"
    ):
        return None
    terms = {f: result.get("terms", {}).get(f) for f in case["negotiable"]}
    if not valid_terms(case, terms):
        return None
    reference = terms["unit_price"]
    terms["unit_price"] = round(reference * (1 - discount), 2)
    if not valid_terms(case, terms):
        return None
    std = standard_terms(threads[sid])
    supplier = estimate(case, sid, std, intelligence)
    quotes = [closing_terms(case, s.get("terms", {}), std) for s in latest(threads[sid]).get("term_sheets", [])]
    quotes = [q for q in quotes if type(q.get("unit_price")) in (float, int) and q["unit_price"] > 0]
    if not quotes:
        return None
    cost = total_cost(case, terms, std, supplier)
    ceiling = min(alternative_cost(case), min(total_cost(case, q, std, supplier) for q in quotes))
    if not math.isfinite(cost) or cost <= 0 or cost > ceiling:
        return None
    return {
        "supplier_id": sid,
        "terms": terms,
        "reference_run_id": memory.get("id"),
        "reference_price": reference,
        "discount": discount,
        "modeled_cost": cost,
        "ceiling": ceiling,
        "limitation": "Known-context historical anchor; previous acceptance does not guarantee a new deal.",
    }


class MemoryOpeningNegotiator(LearnedPortfolioNegotiator):
    def __init__(self, *args, memory_run, memory_discount=0.002, **kwargs):
        super().__init__(*args, **kwargs)
        if not 0 <= memory_discount <= 0.02:
            raise ValueError("Memory opening discount must be between zero and two percent")
        self.memory_run = copy.deepcopy(memory_run)
        self.memory_discount = memory_discount

    def decide(self, case, threads):
        if self.case_id != case["case_id"]:
            self.prepare(case)
        offer = memory_opening(case, threads, self.memory_run, self.intelligence, self.memory_discount)
        if offer is None:
            return super().decide(case, threads)
        terms = copy.deepcopy(offer["terms"])
        move = {
            "supplier_id": offer["supplier_id"],
            "message": {
                "subject": "Re: " + case["title"],
                "body": (
                    "Thank you for your quotation. We would like to agree a commercially workable "
                    "complete package promptly. Could you authorize the terms below and issue your "
                    "binding offer? The price and all commercial terms are conditional on approval "
                    "of this whole package.\n\nExecutable package: " + json.dumps(terms, ensure_ascii=False)
                ),
                "term_sheets": [{"terms": terms}],
            },
        }
        self.decisions.append(
            {
                "supplier_id": offer["supplier_id"],
                "source": "explicit historical-agreement opening",
                "action": "send",
                "elapsed_s": 0,
                "memory_opening": offer,
                "actual_sent_terms": copy.deepcopy(move["message"]["term_sheets"]),
            }
        )
        self.runtime_context = {}
        return move
