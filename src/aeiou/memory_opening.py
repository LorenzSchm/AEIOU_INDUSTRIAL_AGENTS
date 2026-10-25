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
from arena_kit.play import latest, moves_left

from .economic_beliefs import total_cost
from .learned_portfolio import LearnedPortfolioNegotiator
from .optimizer import alternative_cost, estimate, valid_terms
from .reciprocity import same_structure
from .settlement import FIRM_CLOSE


def memory_cost_evidence(case, intelligence, sid):
    source = intelligence.get("estimates", {}).get(sid, {}).get("source")
    fact = intelligence.get("facts", {}).get(source, {})
    value, confidence = fact.get("unit_cost"), fact.get("confidence_pct")
    if (
        not isinstance(source, str)
        or not source.startswith("cost:")
        or fact.get("part") not in case["parts"]
        or fact.get("price_unit") != 1
        or type(value) not in (int, float)
        or not math.isfinite(value)
        or value <= 0
        or type(confidence) not in (int, float)
        or not 0 < confidence < 100
    ):
        return None
    return {
        "ref": source,
        "text": (
            f"Our buyer should-cost estimate is EUR {value:.2f} per unit excluding margin "
            f"({source}), with {confidence:g}% uncertainty. This is a buyer benchmark, "
            "not your disclosed cost. Could you take this evidence and the complete "
            "contractual commitment into account when reviewing the price?"
        ),
    }


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
    # The API permits supplier standard values in addition to term_options.
    # Preserve an off-grid value only if it is BOTH in the prior confirmed deal
    # and explicitly present in this supplier's current structured quote. Do
    # not infer arbitrary values from prose or reuse a withdrawn quote.
    validation_case = copy.deepcopy(case)
    observed_terms = {}
    for field, value in terms.items():
        options = validation_case.get("term_options", {}).get(field)
        if (
            field == "lead_time_days"
            and options
            and value not in options
            and any(
                field in s.get("terms", {}) and s["terms"][field] == value
                for s in latest(threads[sid]).get("term_sheets", [])
            )
        ):
            options.append(copy.deepcopy(value))
            observed_terms[field] = value
    if not valid_terms(validation_case, terms):
        return None
    reference = terms["unit_price"]
    terms["unit_price"] = round(reference * (1 - discount), 2)
    if not valid_terms(validation_case, terms):
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
        "reference_run_id": memory.get("id") or memory.get("session_id"),
        "reference_price": reference,
        "discount": discount,
        "observed_supplier_terms": observed_terms,
        "modeled_cost": cost,
        "ceiling": ceiling,
        "limitation": "Known-context historical anchor; previous acceptance does not guarantee a new deal.",
    }


def retained_memory_counter(case, threads, anchor, intelligence, price_step=0):
    sid = anchor["supplier_id"]
    events = threads.get(sid, [])
    if not events:
        return None
    message = latest(events)
    if message.get("status") != "open" or moves_left(case, events) <= 2 or FIRM_CLOSE.search(message.get("body", "")):
        return None
    std = standard_terms(events)
    current = next(iter(message.get("term_sheets", [])), {})
    full = closing_terms(case, current.get("terms", {}), std)
    quote = {f: full.get(f) for f in case["negotiable"]}
    terms = copy.deepcopy(anchor["terms"])
    if not 0 <= price_step <= 0.01:
        return None
    terms["unit_price"] = round(
        min(anchor["reference_price"], terms["unit_price"] + anchor["reference_price"] * price_step), 2
    )
    if not same_structure(terms, quote) or not quote.get("unit_price"):
        return None
    history = [s["terms"] for m in events if m.get("from") == "buyer" for s in m.get("term_sheets", [])]
    if not any(p == anchor["terms"] for p in history):
        return None  # Never hold an anchor that was not actually sent.
    floor = max((p["unit_price"] for p in history if same_structure(p, terms)), default=0)
    if terms["unit_price"] < floor or terms["unit_price"] >= quote["unit_price"]:
        return None
    validation = copy.deepcopy(case)
    for field, value in anchor.get("observed_supplier_terms", {}).items():
        if field != "lead_time_days" or quote.get(field) != value:
            return None
        if value not in validation["term_options"][field]:
            validation["term_options"][field].append(value)
    if not valid_terms(validation, terms):
        return None
    supplier = estimate(case, sid, std, intelligence)
    modeled = total_cost(case, terms, std, supplier)
    quote_cost = total_cost(case, quote, std, supplier)
    direct, quote_direct = total_cost(case, terms, std), total_cost(case, quote, std)
    if (
        not all(math.isfinite(v) and v > 0 for v in [modeled, quote_cost, direct, quote_direct])
        or modeled > min(quote_cost * 1.0025, alternative_cost(case))
        or direct >= quote_direct
    ):
        return None
    return {
        "supplier_id": sid,
        "terms": terms,
        "reference_quote": current.get("term_sheet_id"),
        "modeled_cost": modeled,
    }


class MemoryOpeningNegotiator(LearnedPortfolioNegotiator):
    def __init__(
        self,
        *args,
        memory_run,
        memory_discount=0.002,
        include_cost_evidence=False,
        followthrough_counters=0,
        followthrough_step=0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if not 0 <= memory_discount <= 0.02:
            raise ValueError("Memory opening discount must be between zero and two percent")
        self.memory_run = copy.deepcopy(memory_run)
        self.memory_discount = memory_discount
        self.include_cost_evidence = include_cost_evidence
        if type(followthrough_counters) is not int or not 0 <= followthrough_counters <= 2:
            raise ValueError("At most two explicit follow-through counters")
        self.followthrough_counters = followthrough_counters
        if not 0 <= followthrough_step <= 0.005:
            raise ValueError("Follow-through step must be between zero and 0.5 percent")
        self.followthrough_step = followthrough_step
        self.memory_anchor = None
        self.followthrough_used = 0

    def prepare(self, case):
        super().prepare(case)
        self.memory_anchor = None
        self.followthrough_used = 0

    def decide(self, case, threads):
        if self.case_id != case["case_id"]:
            self.prepare(case)
        if self.memory_anchor and self.followthrough_used < self.followthrough_counters:
            counter = retained_memory_counter(
                case,
                threads,
                self.memory_anchor,
                self.intelligence,
                self.followthrough_step * (self.followthrough_used + 1),
            )
            if counter:
                self.followthrough_used += 1
                move = {
                    "supplier_id": counter["supplier_id"],
                    "message": {
                        "subject": "Re: " + case["title"],
                        "body": (
                            "Thank you for reviewing our proposal. We can keep the complete commercial "
                            "commitment below. Could you approve our proposed price on these same terms? "
                            "The price remains conditional on the whole package; please confirm your binding offer."
                            "\n\nExecutable package: " + json.dumps(counter["terms"], ensure_ascii=False)
                        ),
                        "term_sheets": [{"terms": counter["terms"]}],
                    },
                }
                self.decisions.append(
                    {
                        "supplier_id": counter["supplier_id"],
                        "source": "bounded historical-structure follow-through",
                        "action": "send",
                        "elapsed_s": 0,
                        "memory_followthrough": counter,
                        "actual_sent_terms": copy.deepcopy(move["message"]["term_sheets"]),
                    }
                )
                self.runtime_context = {}
                return move
        offer = memory_opening(case, threads, self.memory_run, self.intelligence, self.memory_discount)
        if offer is None:
            return super().decide(case, threads)
        self.memory_anchor = copy.deepcopy(offer)
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
        if self.include_cost_evidence:
            evidence = memory_cost_evidence(case, self.intelligence, offer["supplier_id"])
            if evidence:
                move["message"]["body"] += "\n\n" + evidence["text"]
                move["message"]["refs"] = [evidence["ref"]]
                offer["cost_evidence"] = evidence
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
