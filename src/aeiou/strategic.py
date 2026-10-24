"""An opt-in agent that learns from package choices and keeps economics in code."""

import copy
import json
import math
import os
import re
import time

from arena_kit.agents.llm import parse
from arena_kit.play import latest, live_threads, moves_left

from .agent import Negotiator, log
from .grounding import compose_email, fresh_evidence_cards, next_evidence_ids, responsive_act
from .negotiation_state import supplier_state
from .opponent_model import learn_beliefs, predicted_choices
from .optimizer import alternative_cost, evaluate, safe_close
from .retrieval import evidence_cards, evidence_cutoff
from .scenario_planner import plan_packages
from .strategy_policy import StrategicFeatures, adaptive_packages, plan_next_action

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
    def __init__(self, pkg, backend=None, features=None, writer_instruction=None, **kwargs):
        super().__init__(pkg, backend, **kwargs)
        self.support, self.rejections = {}, []
        self.features = features or StrategicFeatures()
        if writer_instruction is None and os.getenv("AEIOU_WRITER_INSTRUCTION_FILE"):
            from .prompt_optimization import load_writer_instruction

            writer_instruction = load_writer_instruction(os.environ["AEIOU_WRITER_INSTRUCTION_FILE"])
        self.writer_instruction = writer_instruction or ""
        self.controller = {}

    def prepare(self, case):
        super().prepare(case)
        self._cards = []
        if self.features.retrieval:
            self._index = self.pkg.evidence_index()

    def system_prompt(self):
        grounded = (
            """
The economic controller already selected the action and executable package IDs.
You select a communication act only. Return JSON with the planned supplier_id,
action='send', the supplied candidate_ids, opening='warm|concise|firm',
question='priorities|cash|continuity|authority|costs|delivery|quality|tradeoff', evidence_ids=[].
Select at most two current shareable evidence cards for THIS supplier. The composer
quotes complete approved excerpts verbatim and adds exact contract terms.
Do not add term_sheets or free-form factual claims. Do not change the planned action.
"""
            if self.features.grounded_language
            else ""
        )
        return super().system_prompt() + STRATEGIC_SYSTEM + grounded + self.writer_instruction

    def packages(self, case, threads):
        raw = {}
        for sid in live_threads(threads):
            state = self.support[sid]["state"]
            belief = self.support[sid].setdefault(
                "belief", learn_beliefs(case, threads[sid], self.features.opponent_learning)
            )
            raw[sid], strategy = adaptive_packages(
                case, threads[sid], sid, self.intelligence, state, belief, self.features
            )
            self.support[sid]["strategy"] = strategy
        safe = [r["cost"] for r in evaluate(case, threads, self.intelligence) if r["acceptable"]]
        ceiling = min(alternative_cost(case), min(safe, default=math.inf) * 1.005)
        result = {}
        for sid, candidates in raw.items():
            plan = plan_packages(
                case,
                threads[sid],
                sid,
                self.intelligence,
                candidates,
                self.support[sid]["state"],
                use_counteroffers=self.features.opponent_learning,
            )
            # No exploration package may cost more than the current optimizer's envelope.
            budget = min(ceiling, max((p["cost"] for p in candidates), default=0) * 1.0025)
            plan["packages"] = [p for p in plan["packages"] if p["cost"] <= budget]
            if plan["recommendation"] not in {p["id"] for p in plan["packages"]}:
                plan["recommendation"] = next((p["id"] for p in plan["packages"]), None)
            plan["economic_ceiling"] = budget if math.isfinite(budget) else None
            plan["modeled_choice_weights"] = predicted_choices(case, plan["packages"], self.support[sid]["belief"])
            controller = plan_next_action(
                case,
                threads[sid],
                plan["packages"],
                evaluate(case, threads, self.intelligence),
                self.support[sid]["belief"],
                self.features.lookahead,
            )
            # Initially retain diverse questions. Once evidence accumulates, exploit
            # the preferred compensated package while preserving buyer-value A.
            if self.support[sid]["belief"]["observations"] >= 2 and plan["packages"]:
                chosen = max(zip(plan["packages"], plan["modeled_choice_weights"], strict=True), key=lambda p: p[1])[0]
                controller["candidate_ids"] = list(dict.fromkeys([plan["packages"][0]["id"], chosen["id"]]))
                controller["candidate_ids"] = list(
                    dict.fromkeys(
                        controller["candidate_ids"] + [p["id"] for p in plan["packages"] if p.get("feedback_probe")]
                    )
                )[:3]
            self.support[sid]["controller"] = controller
            self.support[sid]["plan"] = plan
            result[sid] = plan["packages"]
        return result

    def render(self, case, threads, offers, best):
        payload = json.loads(super().render(case, threads, offers, best))
        if self.features.retrieval:
            payload["research"] = {
                "evidence_cards": self._cards,
                "indices": self.intelligence.get("indices", []),
                "estimates": self.intelligence.get("estimates", {}),
            }
        payload["decision_support"] = self.support
        payload["planned_action"] = self.controller
        for sid, packages in payload["candidate_packages"].items():
            if sid == self.controller.get("supplier_id"):
                ids = self.controller.get("candidate_ids", [p["id"] for p in packages])
                payload["candidate_packages"][sid] = [p for p in packages if p["id"] in ids]
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
            if self.controller.get("supplier_id") == sid and set(ids) != set(self.controller.get("candidate_ids", ids)):
                return self.reject("The writer cannot change the planned package selection")
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
        if action == "send" and self.features.grounded_language:
            try:
                strategy = self.support.get(sid, {}).get("strategy", {}).get("name", "balanced")
                if not data.get("evidence_ids"):
                    data = {**data, "evidence_ids": next_evidence_ids(self._cards, threads, sid)}
                data = responsive_act(
                    data, threads[sid], any(p.get("feedback_probe") and p["id"] in ids for p in candidates.get(sid, []))
                )
                data = {**data, "body": compose_email(data, self._cards, sid, strategy)}
            except ValueError as exc:
                return self.reject(str(exc))
        move = super().guard(case, threads, data, candidates)
        if move is None:
            return self.reject("Action failed the shared contract or evidence guard")
        return move

    def decide(self, case, threads):
        started = time.monotonic()
        if self.case_id != case["case_id"]:
            self.prepare(case)
        self.support = {
            sid: {
                "state": supplier_state(case, events),
                "belief": learn_beliefs(case, events, self.features.opponent_learning),
            }
            for sid, events in threads.items()
        }
        self.rejections = []
        live = live_threads(threads)
        if not live:
            raise ValueError("No live supplier")
        offers = self.packages(case, threads)
        unexplored = [sid for sid in live if not any(m.get("from") == "buyer" for m in threads[sid])]
        sid = min(unexplored or live, key=lambda s: sum(m.get("from") == "buyer" for m in threads[s]))
        controller = self.support[sid]["controller"]
        self.controller = {**controller, "supplier_id": sid}
        if unexplored and controller["action"] == "accept":
            self.controller["action"] = "send"
        if (
            self.controller["action"] == "accept"
            and moves_left(case, threads[sid]) > 2
            and latest(threads[sid]).get("status") == "open"
        ):
            # An uncalibrated rollout cannot discard a materially better legal
            # counter or another supplier's continuation opportunity.
            safe_cost = min(
                (r["cost"] for r in evaluate(case, threads, self.intelligence) if r["acceptable"]),
                default=math.inf,
            )
            target = min((p["cost"] for ps in offers.values() for p in ps), default=math.inf)
            continuation = min(
                (
                    t["modeled_continuation_cost"]
                    for s in self.support.values()
                    for t in s.get("controller", {}).get("traces", [])
                ),
                default=math.inf,
            )
            if safe_cost > min(target * 1.005, continuation * 1.0005):
                self.controller.update(action="send", reason="Preserve a better legal counter or supplier continuation")
        ids = self.controller.get("candidate_ids", [p["id"] for p in offers.get(sid, [])])
        self.controller["candidate_ids"] = ids
        self._cards = evidence_cards([], self.intelligence)
        if self.features.retrieval:
            query = (
                f"{case['title']} {self.support[sid]['strategy']['name']} "
                "costs payment cash volume fixed material capacity approval"
            )
            hits = self._index.search(query, [sid], case["parts"], cutoff=evidence_cutoff(case), limit=6)
            self._cards = evidence_cards(hits, self.intelligence)
            # A newly retrieved document must also be known to the inherited
            # citation guard; only package-backed references enter this set.
            for card in self._cards:
                for ref in card.get("source_ids") or [card.get("source_id")]:
                    if ref and ref not in self.intelligence["facts"]:
                        if fact := self.pkg.lookup(ref):
                            self.intelligence["facts"][ref] = fact
        if self.features.grounded_language:
            self._cards = fresh_evidence_cards(self._cards, threads)
        move, source = None, "controller policy"
        if self.controller["action"] in {"accept", "walk_away"} or not offers.get(sid):
            move, source = safe_close(case, threads, self.intelligence), "controller close"
        if move is None and self.backend:
            try:
                data = parse(
                    self.backend.complete(
                        self.system_prompt(),
                        self.render(case, threads, offers, evaluate(case, threads, self.intelligence)),
                    )
                )
                if data.get("action") != "send" or data.get("supplier_id") != sid:
                    self.reject("The writer cannot change the controller's action or supplier")
                else:
                    move = self.guard(case, threads, data, offers)
                source = "claude" if move else "guard fallback"
            except Exception as exc:
                log.warning("Strategic writer failed: %s", type(exc).__name__)
                source = "provider fallback"
            if move is None:
                self.fallbacks += 1
        if move is None:
            chosen = [p for p in offers.get(sid, []) if p["id"] in ids]
            if not chosen:
                move = safe_close(case, threads, self.intelligence)
            else:
                selected_evidence = (
                    next_evidence_ids(self._cards, threads, sid) if self.features.grounded_language else []
                )
                body = compose_email(
                    responsive_act(
                        {"evidence_ids": selected_evidence}, threads[sid], any(p.get("feedback_probe") for p in chosen)
                    ),
                    self._cards,
                    sid,
                    self.support[sid]["strategy"]["name"],
                )
                move = {
                    "supplier_id": sid,
                    "message": {
                        "subject": f"Re: {case['title']}",
                        "body": body,
                        "term_sheets": [{"terms": p["terms"]} for p in chosen],
                    },
                }
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
        self.decisions.append(
            {
                "supplier_id": move["supplier_id"],
                "source": source,
                "elapsed_s": round(time.monotonic() - started, 3),
                "action": move["message"].get("action", {}).get("type", "send"),
            }
        )
        self.decisions[-1].update(
            decision_support=copy.deepcopy(self.support),
            guard_rejections=list(self.rejections),
            policy="strategic-v2",
            features=self.features.metadata(),
            controller=copy.deepcopy(self.controller),
            evidence_cards=copy.deepcopy(self._cards),
        )
        return move
