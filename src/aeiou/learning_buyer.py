"""Experimental learned support with an independent exact-offer terminal boundary."""

from arena_kit.play import latest

from .action_policy import NegotiationBlocked
from .prompt_protocol import ContractError, PromptLabAgent


class LearningBuyer(PromptLabAgent):
    def terminal_confirmable(self, threads, sid, offer):
        events = threads[sid]
        first_final = next(
            (
                i
                for i, m in enumerate(events)
                if m.get("from") != "buyer" and m.get("status") in {"final", "final_offer", "agreed"}
            ),
            len(events),
        )
        return (
            not offer["server_acceptance_available"]
            and offer["must_haves_ok"]
            and offer["economically_acceptable"]
            and all(
                offer.get(k, True) for k in ("meets_score_floor", "meets_unit_price_limit", "meets_execution_policy")
            )
            and not any(m.get("from") == "buyer" for m in events[first_final + 1 :])
        )

    def decide(self, case, threads):
        if self.case_id != case["case_id"]:
            self.prepare(case)
        state = self.decision_protocol_state(case, threads)
        terminal = {
            sid: s
            for sid, s in state["suppliers"].items()
            if s["supplier_status"] in {"final", "final_offer", "agreed"}
        }
        if terminal:
            eligible = [(sid, o) for sid, s in terminal.items() for o in s["offers"] if o["acceptance_eligible"]]
            if eligible:
                sid, offer = min(eligible, key=lambda p: p[1]["cost"])
                move = self.contract(
                    case, threads, {"supplier_id": sid, "action": "accept", "term_sheet_id": offer["term_sheet_id"]}
                )
                self.decisions.append(
                    {
                        "supplier_id": sid,
                        "source": "policy:terminal-exact-accept",
                        "action": "accept",
                        "elapsed_s": 0,
                        "message_index": len(threads[sid]),
                    }
                )
                return move
            confirmable = [
                (sid, o)
                for sid, s in terminal.items()
                for o in s["offers"]
                if s["rounds_left"] and self.terminal_confirmable(threads, sid, o)
            ]
            if confirmable:
                sid, offer = min(confirmable, key=lambda p: p[1]["cost"])
                move = self.contract(
                    case, threads, {"supplier_id": sid, "action": "confirm", "term_sheet_id": offer["term_sheet_id"]}
                )
                self.decisions.append(
                    {
                        "supplier_id": sid,
                        "source": "policy:terminal-exact-alternative-confirm",
                        "action": "confirm",
                        "elapsed_s": 0,
                        "message_index": len(threads[sid]),
                        "confirmation": offer,
                    }
                )
                return move
            if len(terminal) == len(state["suppliers"]):
                raise NegotiationBlocked("FINAL_OFFER_OUTSIDE_LIMITS")
        return super().decide(case, threads)

    def contract(self, case, threads, action):
        sid = action.get("supplier_id")
        if sid in threads and action.get("action") in {"send", "confirm"}:
            if latest(threads[sid]).get("status") in {"final", "final_offer", "agreed"}:
                offers = self.decision_protocol_state(case, threads)["suppliers"][sid]["offers"]
                selected = next((o for o in offers if o["term_sheet_id"] == action.get("term_sheet_id")), None)
                if (
                    action["action"] != "confirm"
                    or not selected
                    or not self.terminal_confirmable(threads, sid, selected)
                ):
                    raise ContractError(
                        "No new proposal after finality; only one unchanged qualifying alternative confirmation"
                    )
        return super().contract(case, threads, action)
