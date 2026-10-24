"""An opt-in agent that learns from package choices and keeps economics in code."""

import copy
import json
import math
import re

from arena_kit.play import latest, live_threads, moves_left

from .agent import Negotiator
from .negotiation_state import supplier_state
from .optimizer import alternative_cost, evaluate
from .scenario_planner import plan_packages

STRATEGIC_SYSTEM = """
Strategic controller: use decision_support to interpret public supplier behavior.
Supplier preferences are tentative hypotheses, not known utilities or hidden floors.
Scenario costs are explicit buyer-cost sensitivities, not acceptance probabilities.
When sending, choose candidate_ids from the current supplied packages. Include A, the
protected buyer-value package. Other IDs explore compensated trade-offs. Do not invent
term_sheets or increase concessions independently: Python owns the package schedule.
The controller appends exact contract terms. Keep body focused on a diagnostic question,
verified evidence and the conditional nature of the exchange. Do not state numerical
contract terms in body. Never acknowledge a cost breakdown or 8D report unless it is
listed among received_attachments. Never expose scenario costs, ceilings or private budgets.
"""


class StrategicNegotiator(Negotiator):
    def __init__(self, pkg, backend=None, **kwargs):
        super().__init__(pkg, backend, **kwargs)
        self.support, self.rejections = {}, []

    def system_prompt(self):
        return super().system_prompt() + STRATEGIC_SYSTEM

    def packages(self, case, threads):
        raw = super().packages(case, threads)
        safe = [r["cost"] for r in evaluate(case, threads, self.intelligence) if r["acceptable"]]
        ceiling = min(alternative_cost(case), min(safe, default=math.inf) * 1.005)
        result = {}
        for sid, candidates in raw.items():
            plan = plan_packages(case, threads[sid], sid, self.intelligence, candidates, self.support[sid]["state"])
            # No exploration package may cost more than the current optimizer's envelope.
            budget = min(ceiling, max((p["cost"] for p in candidates), default=0) * 1.0025)
            plan["packages"] = [p for p in plan["packages"] if p["cost"] <= budget]
            if plan["recommendation"] not in {p["id"] for p in plan["packages"]}:
                plan["recommendation"] = next((p["id"] for p in plan["packages"]), None)
            plan["economic_ceiling"] = budget if math.isfinite(budget) else None
            self.support[sid]["plan"] = plan
            result[sid] = plan["packages"]
        return result

    def render(self, case, threads, offers, best):
        payload = json.loads(super().render(case, threads, offers, best))
        payload["decision_support"] = self.support
        return json.dumps(payload, ensure_ascii=False, default=str)

    def reject(self, reason):
        self.rejections.append(reason)
        return None

    def guard(self, case, threads, data, candidates):
        sid, action = data.get("supplier_id"), data.get("action")
        if sid not in live_threads(threads):
            return self.reject("Unknown or closed supplier")
        if action == "send":
            if data.get("term_sheets"):
                return self.reject("Custom packages bypass the economic controller; select candidate_ids")
            ids = data.get("candidate_ids")
            available = {p["id"] for p in candidates.get(sid, [])}
            if (
                not isinstance(ids, list)
                or not ids
                or any(not isinstance(i, str) or i not in available for i in ids)
                or len(ids) > 3
                or len(ids) != len(set(ids))
            ):
                return self.reject("Select one to three distinct current package IDs")
            if "A" in available and "A" not in ids:
                return self.reject("The buyer-value package must remain available")
            body = str(data.get("body", ""))
            attachments = self.support.get(sid, {}).get("state", {}).get("received_attachments", [])
            received = " ".join(str(a.get("name", "")) for a in attachments).lower()
            if re.search(r"(?:thank|thanks).{0,45}(?:cost breakdown|8d report)", body, re.I | re.S):
                if not any(name in received for name in ("breakdown", "cost", "8d")):
                    return self.reject("Acknowledged an unobserved attachment")
            # Numerical contract claims in prose could contradict the executable sheet.
            if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:days?\b|months?\b|EUR\b|euros?\b)|[€$]\s*\d", body, re.I):
                return self.reject("Contract numbers belong in the controller's exact package summary")
        elif action == "accept" and latest(threads[sid]).get("status") == "open" and moves_left(case, threads[sid]) > 2:
            target = min((p["cost"] for p in candidates.get(sid, [])), default=math.inf)
            standing = [r["cost"] for r in evaluate(case, threads, self.intelligence) if r["acceptable"]]
            if standing and min(standing) > target * 1.005:
                return self.reject("A materially better controlled counteroffer remains available")
        move = super().guard(case, threads, data, candidates)
        if move is None:
            return self.reject("Action failed the shared contract or evidence guard")
        return move

    def decide(self, case, threads):
        self.support = {sid: {"state": supplier_state(case, events)} for sid, events in threads.items()}
        self.rejections = []
        move = super().decide(case, threads)
        sheets = move["message"].get("term_sheets", [])
        if sheets:
            summaries = []
            labels = {
                "unit_price": "Unit price (EUR)",
                "payment_days": "Payment (days)",
                "contract_months": "Contract duration (months)",
                "volume_per_year": "Annual volume",
                "lead_time_days": "Lead time (days)",
                "ppm_commitment": "Quality commitment (PPM)",
                "chargeback_eur_per_complaint": "Compensation per complaint (EUR)",
                "incoterm": "Freight terms",
            }
            for i, sheet in enumerate(sheets, 1):
                terms = "; ".join(
                    f"{labels.get(k, k.replace('_', ' ').capitalize())}: {json.dumps(v, ensure_ascii=False)}"
                    for k, v in sheet["terms"].items()
                )
                summaries.append(f"Package {i}: {terms}")
            summary = "\n\nExecutable packages:\n" + "\n".join(summaries)
            if len(summary) > 7900:
                raise ValueError("Executable package summary exceeds the message size limit")
            move["message"]["body"] = move["message"]["body"][: 8000 - len(summary)] + summary
        self.decisions[-1].update(
            decision_support=copy.deepcopy(self.support),
            guard_rejections=list(self.rejections),
            policy="strategic-v1",
        )
        return move
