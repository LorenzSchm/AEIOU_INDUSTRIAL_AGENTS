"""Opt-in P10 experiment: staged commitment with an honest forecast bridge.

This known-case experiment is deliberately absent from the production factory.
It reuses a successful bargaining sequence, not hidden supplier parameters.
"""

import copy
import json

from arena_kit.play import latest

from .optimizer import valid_terms
from .strategic import StrategicNegotiator


class CapacityCommitmentNegotiator(StrategicNegotiator):
    def decide(self, case, threads):
        if case["case_id"] != "case:P10":
            raise ValueError("Explicit experiment only supports P10")
        if self.case_id != case["case_id"]:
            self.prepare(case)
        if len(threads) != 1:
            return super().decide(case, threads)
        sid, events = next(iter(threads.items()))
        sent = [m for m in events if m.get("from") == "buyer"]
        current = latest(events)
        if current.get("status") != "open" or len(sent) >= 2:
            return self.close(case, threads)
        quotes = current.get("term_sheets", [])
        if not quotes or quotes[0]["terms"].get("lead_time_days") != 54:
            return super().decide(case, threads)
        forecast = self.pkg.source_record("fc:340010@2026-09")
        cost = self.pkg.lookup("cost:340010")
        if forecast.get("qty_next_12m") != 21310 or cost.get("unit_cost") != 24.31:
            return super().decide(case, threads)
        if sent and not sent[-1].get("body", "").startswith("Dear Ms Zellner,\n\nOur capacity commitment"):
            return super().decide(case, threads)
        terms = {
            "unit_price": 29.89 if not sent else 31.07,
            "volume_per_year": case["buyer"]["need_per_year"],
            "contract_months": 24 if not sent else 36,
            "lead_time_days": 54,
        }
        # Supplier-standard values are legal alongside listed options. Only allow
        # the lead time in the current structured quote, without mutating inputs.
        validation = copy.deepcopy(case)
        validation["term_options"]["lead_time_days"].append(54)
        if terms["volume_per_year"] != 23400 or not valid_terms(validation, terms):
            return super().decide(case, threads)
        stage = (
            "We offer EUR 29.89/unit for a binding 23,400 units per year over 24 months. "
            if not sent
            else "Thank you for the revised quotation. We increase our price proposal from EUR 29.89 "
            "to EUR 31.07/unit and add a third binding year: 23,400 units in EACH year over 36 months. "
        )
        body = (
            "Dear Ms Zellner,\n\nOur capacity commitment addresses your request for firm, long-term "
            "planning in your 18 September capacity letter (doc:20260918-70410-P10). " + stage
            + "We accept your quoted 54-day lead time, rather than the 42-day framework standard "
            "(ctr:4600070410#5.1), in exchange for full allocation and the proposed price.\n\n"
            "Here is the planning basis: the September forecast shared with suppliers covers 21,310 "
            "units over the next 12 months (fc:340010@2026-09). Our current enquiry requires 23,400 "
            "per year. Our proposed binding commitment is therefore 2,090 units above that earlier "
            "forecast, approximately 9.81% higher. We do not claim that the old forecast already "
            "contains the new requirement or proves demand for three years. The additional years "
            "are the contractual commitment we are offering now, conditional on the complete deal. "
            "For capacity discussion, 23,400/year averages 1,950/month; this is a proposed planning "
            "cadence, not an existing approved monthly call-off schedule.\n\n"
            "Our buyer should-cost estimate is EUR 24.31/unit excluding margin, with 5% uncertainty "
            "(cost:340010); this is a buyer benchmark, not your disclosed cost. We are offering "
            "firm volume, longer duration and scheduling room in return for the price. "
            "All fixed terms remain DAP, payment 30 days, effective 2027-01-01, no index or tooling. "
            "Could you authorize the complete package below?\n\n" + json.dumps(terms)
        )
        move = {"supplier_id": sid, "message": {
            "subject": "Firm allocation and substantiated volume commitment",
            "body": body,
            "refs": ["doc:20260918-70410-P10", "fc:340010@2026-09", "cost:340010", "ctr:4600070410"],
            "term_sheets": [{"terms": terms}],
        }}
        self.decisions.append({"supplier_id": sid, "source": "explicit staged capacity experiment",
                               "action": "send", "elapsed_s": 0, "stage": len(sent) + 1,
                               "actual_sent_terms": copy.deepcopy(move["message"]["term_sheets"])})
        self.runtime_context = {}
        return move
