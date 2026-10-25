"""Explicit hosted experiment: rank legal menus, never alter prices or closure.

Not wired into the production agent factory. The caller must supply a locally
trained trusted artifact and explicitly choose this experimental subclass.
"""

import copy
import itertools

from arena_kit.play import latest, moves_left

from .response_learning import extract_run, features
from .response_refinement import enrich_history, predict_selected
from .strategic import StrategicNegotiator


def menu_variants(packages, original):
    available = {p["id"]: p for p in packages}
    if not original or any(i not in available for i in original):
        return []
    anchor = "A" if "A" in available else packages[0]["id"]
    required = {anchor}
    required.update(
        i
        for i in original
        if any(
            available[i].get(flag)
            for flag in ("renewal_settlement_probe", "retained_concession", "feedback_probe", "observed_tradeoff")
        )
    )
    variants = [list(original)]
    for size in range(1, min(3, len(packages)) + 1):
        for subset in itertools.combinations(available, size):
            if required.issubset(subset) and set(subset) != set(original):
                variants.append(list(subset))
    return variants


class ProgressSelector:
    def __init__(self, artifact):
        self.artifact = artifact

    def probability(self, case, sid, events, packages, body="Thank you", refs=None):
        previous, _ = extract_run({"mode": "hosted", "session_id": "current", "case": case, "threads": {sid: events}})
        proposal = {"body": body, "refs": refs or [], "term_sheets": [{"terms": p["terms"]} for p in packages]}
        current = {
            "session_id": "current",
            "supplier_id": sid,
            "case_id": case["case_id"],
            "turn": len(events),
            "x": features(case, events, proposal),
            "y": {},
        }
        row = enrich_history(previous + [current])[-1]
        target = "direct_cost_improved"
        probability = predict_selected(self.artifact["models"][target], self.artifact["selections"][target], [row])
        return float(probability[0])

    def choose(self, case, sid, events, packages, original, body="Thank you", refs=None):
        if latest(events).get("status") != "open" or moves_left(case, events) <= 2:
            return list(original), {"changed": False, "reason": "terminal_or_closing_turn"}
        variants = menu_variants(packages, original)
        if not variants:
            return list(original), {"changed": False, "reason": "no_valid_menu"}
        available = {p["id"]: p for p in packages}
        scores = [
            {"ids": ids, "probability": self.probability(case, sid, events, [available[i] for i in ids], body, refs)}
            for ids in variants
        ]
        best = max(scores, key=lambda r: r["probability"])
        chosen = best["ids"] if best["probability"] >= scores[0]["probability"] + 0.01 else list(original)
        return chosen, {
            "changed": chosen != list(original),
            "original_ids": list(original),
            "selected_ids": chosen,
            "menus": scores,
            "target": "next_direct_cost_improvement",
            "minimum_probability_gain": 0.01,
        }


class LearnedPortfolioNegotiator(StrategicNegotiator):
    def __init__(self, *args, learned_artifact, **kwargs):
        super().__init__(*args, **kwargs)
        self.selector = ProgressSelector(learned_artifact)
        self.selection_audit = []

    def packages(self, case, threads):
        offers = super().packages(case, threads)
        for sid, packages in offers.items():
            controller = self.support[sid]["controller"]
            if controller.get("action") != "send" or not packages:
                continue
            original = controller.get("candidate_ids", [p["id"] for p in packages])
            try:
                chosen, audit = self.selector.choose(case, sid, threads[sid], packages, original)
                controller["candidate_ids"] = chosen
                self.selection_audit.append({"stage": "controller", "supplier_id": sid, **audit})
            except (KeyError, TypeError, ValueError) as exc:
                self.selection_audit.append({"stage": "controller", "changed": False, "error_type": type(exc).__name__})
        return offers

    def guard(self, case, threads, data, candidates):
        sid = data.get("supplier_id")
        original_controller = copy.deepcopy(self.controller)
        audit = None
        original = data.get("candidate_ids")
        if data.get("action") == "send" and sid in threads and isinstance(original, list):
            try:
                chosen, audit = self.selector.choose(
                    case,
                    sid,
                    threads[sid],
                    candidates.get(sid, []),
                    original,
                    data.get("body") or "Thank you",
                    data.get("evidence_ids"),
                )
                data = {**data, "candidate_ids": chosen}
                self.controller["candidate_ids"] = chosen
            except (KeyError, TypeError, ValueError) as exc:
                audit = {"changed": False, "error_type": type(exc).__name__}
        move = super().guard(case, threads, data, candidates)
        if move is None:
            self.controller = original_controller
        if audit is not None:
            self.selection_audit.append(
                {"stage": "admitted_planner_menu", "supplier_id": sid, "guard_passed": move is not None, **audit}
            )
        return move

    def decide(self, case, threads):
        self.selection_audit = []
        move = super().decide(case, threads)
        if self.decisions:
            self.decisions[-1]["learned_portfolio"] = copy.deepcopy(self.selection_audit)
            self.decisions[-1]["actual_sent_terms"] = copy.deepcopy(move["message"].get("term_sheets", []))
        return move
