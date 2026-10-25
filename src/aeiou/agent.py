import json
import logging
import os
import re
import time

import anthropic
from arena_kit.agents.llm import ID_RE, parse
from arena_kit.cost import complete_offer, total_cost
from arena_kit.docs import standard_terms
from arena_kit.play import latest, live_threads, moves_left, standing

from .config import model_provider, model_settings, provider_key
from .negotiation_state import walk_away_diagnostic
from .optimizer import alternative_cost, candidate_packages, estimate, evaluate, safe_close, valid_terms
from .prompts import SYSTEM, VOSS
from .research import research

log = logging.getLogger("aeiou")


class ProviderRefusalError(RuntimeError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


class EmptyModelResponseError(RuntimeError):
    pass


class BuyerDecisionTimeout(TimeoutError):
    pass


# Leave time for validation and delivery within the arena's 60-second reply window.
BUYER_DECISION_BUDGET_S = 50


class ClaudeBackend:
    provider = "anthropic"

    def __init__(self, model=None, max_tokens=None, timeout=None, *, effort=None, speed=None):
        settings = model_settings(model)
        self.model = settings["model"]
        self.effort = effort or settings["effort"]
        self.speed = speed or settings["speed"]
        if self.speed not in {"standard", "fast"}:
            raise ValueError("Choose standard or fast model speed")
        if self.speed == "fast" and self.model != "claude-opus-5-5":
            raise ValueError("Fast mode is configured only for Claude Opus 5.5")
        self.timeout_s = timeout if timeout is not None else float(os.environ.get("LLM_TIMEOUT", "35"))
        self.client = anthropic.Anthropic(api_key=provider_key("anthropic"), timeout=self.timeout_s, max_retries=0)
        self.calls, self.errors, self.input_tokens, self.output_tokens = 0, 0, 0, 0
        self.refusals, self.empty_responses, self.stop_reasons = 0, 0, {}
        self.refusal_details = []
        self.max_tokens = max_tokens if max_tokens is not None else int(os.environ.get("LLM_MAX_TOKENS", "1800"))

    def complete_json(self, system, user, schema):
        return self.complete(system, user, output_schema=schema)

    def complete_before(self, system, user, deadline):
        return self.complete(system, user, deadline=deadline)

    def complete(self, system, user, *, output_schema=None, deadline=None):
        request_options = {}
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BuyerDecisionTimeout("Buyer decision time budget exhausted; no further model request was sent")
            request_options["timeout"] = min(self.timeout_s, remaining)
        self.calls += 1
        try:
            output_config = {"effort": self.effort}
            if output_schema is not None:
                output_config["format"] = {"type": "json_schema", "schema": output_schema}
            r = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                **({"speed": "fast"} if self.speed == "fast" else {}),
                **request_options,
            )
            self.input_tokens += r.usage.input_tokens
            self.output_tokens += r.usage.output_tokens
            if deadline is not None and time.monotonic() >= deadline:
                raise BuyerDecisionTimeout("Buyer model exceeded the decision time budget; its action was not sent")
            reason = r.stop_reason or "unknown"
            self.stop_reasons[reason] = self.stop_reasons.get(reason, 0) + 1
            if reason == "refusal":
                self.refusals += 1
                details = getattr(r, "stop_details", None)
                if hasattr(details, "model_dump"):
                    details = details.model_dump()
                # Whitelist diagnostic fields; never retain fallback credentials or model reasoning.
                safe = {
                    k: str(v)[:1000]
                    for k, v in (details or {}).items()
                    if k in {"type", "category", "explanation"} and v is not None
                }
                safe.update(model=self.model, request_id=getattr(r, "_request_id", None))
                self.refusal_details.append(safe)
                category = safe.get("category", "unspecified")
                raise ProviderRefusalError(
                    f"Claude declined this decision ({category}, stop_reason=refusal); "
                    "no action from this response was sent",
                    safe,
                )
            response = "".join(b.text for b in r.content if b.type == "text")
            if not response.strip():
                self.empty_responses += 1
                raise EmptyModelResponseError(f"Provider returned no text (stop_reason={reason})")
            return response
        except anthropic.APITimeoutError as exc:
            self.errors += 1
            if deadline is not None:
                raise BuyerDecisionTimeout(
                    f"Buyer model timed out with {request_options['timeout']:.1f}s available for this call. "
                    "No action was sent; the arena reply window is 60s."
                ) from exc
            raise
        except Exception:
            self.errors += 1
            raise


def make_backend(model=None, **kwargs):
    settings = model_settings(model)
    if model_provider(settings["model"]) == "openai":
        from .openai_backend import OpenAIBackend

        return OpenAIBackend(model=settings["model"], **kwargs)
    return ClaudeBackend(model=settings["model"], **kwargs)


class Negotiator:
    """Claude selects tactics and terms; independent code enforces the economic contract."""

    def __init__(self, pkg, backend=None, variant="adaptive", tactic="prepared"):
        self.pkg, self.backend, self.variant, self.tactic = pkg, backend, variant, tactic
        self.case_id, self.intelligence = None, {}
        self.decisions, self.fallbacks = [], 0

    def prepare(self, case):
        self.case_id = case["case_id"]
        self.intelligence = research(case, self.pkg)

    def close(self, case, threads):
        if case["case_id"] != self.case_id:
            self.prepare(case)
        move = safe_close(case, threads, self.intelligence)
        decision = {
            "supplier_id": move["supplier_id"],
            "source": "economic deadline close",
            "action": move["message"]["action"]["type"],
            "elapsed_s": 0,
        }
        if decision["action"] == "walk_away":
            decision["walk_away"] = walk_away_diagnostic(
                case, threads, move["supplier_id"], source="economic deadline close", intelligence=self.intelligence
            )
        self.decisions.append(decision)
        return move

    def decide(self, case, threads):
        if case["case_id"] != self.case_id:
            self.prepare(case)
        started = time.monotonic()
        live = live_threads(threads)
        if not live:
            raise ValueError("no live supplier thread")
        best = evaluate(case, threads, self.intelligence)
        # Fair RFQ rotation: don't let a cheap opening prevent discovering the other suppliers.
        sid = min(
            live,
            key=lambda s: (
                sum(m.get("from") == "buyer" for m in threads[s]),
                next((r["cost"] for r in best if r["supplier_id"] == s), float("inf")),
            ),
        )
        events = threads[sid]
        rounds = sum(m.get("from") == "buyer" for m in events)
        terminal = latest(events).get("status") in {"agreed", "final_offer"} or moves_left(case, events) <= 0
        explored = all(any(m.get("from") == "buyer" for m in threads[s]) for s in live)
        if terminal and explored:
            move, source = safe_close(case, threads, self.intelligence), "economic close"
        else:
            offers = {
                s: candidate_packages(
                    case, threads[s], s, self.intelligence, anchor=0.10 if self.variant == "guarded" else 0.18
                )
                for s in live
                if standing(threads[s])[1].get("unit_price")
            }
            move = None
            source = "deterministic"
            if self.backend:
                try:
                    raw = self.backend.complete(
                        SYSTEM + (VOSS if self.tactic == "voss" else ""), self.render(case, threads, offers, best)
                    )
                    move = self.guard(case, threads, parse(raw), offers)
                    source = "claude" if move else "guard fallback"
                except Exception as exc:
                    log.warning("Claude call failed: %s; using guarded policy", type(exc).__name__)
                    source = "provider fallback"
                if move is None:
                    self.fallbacks += 1
            if move is None:
                if terminal or not offers.get(sid):
                    move = safe_close(case, threads, self.intelligence)
                else:
                    packages = offers[sid]
                    # Ask for evidence while making an executable complete counteroffer.
                    body = (
                        "Thank you for the quotation. We value a reliable long-term partnership. "
                        "Please review these alternative packages and let us know which best fits your costs "
                        "and cash flow. Could you share a cost breakdown and the scope for an improved package?"
                    )
                    if rounds:
                        body = (
                            "Thank you for working through the options. We can move conditionally on the full "
                            "package below. Which term prevents agreement, and what reciprocal improvement "
                            "would allow you to approve it?"
                        )
                    move = {
                        "supplier_id": sid,
                        "message": {
                            "subject": f"Re: {case['title']}",
                            "body": body,
                            "term_sheets": [{"terms": p["terms"]} for p in packages],
                        },
                    }
        self.decisions.append(
            {
                "supplier_id": move["supplier_id"],
                "source": source,
                "elapsed_s": round(time.monotonic() - started, 3),
                "action": move["message"].get("action", {}).get("type", "send"),
            }
        )
        if self.decisions[-1]["action"] == "walk_away":
            self.decisions[-1]["walk_away"] = walk_away_diagnostic(
                case, threads, move["supplier_id"], source=source, intelligence=self.intelligence
            )
        return move

    def render(self, case, threads, offers, best):
        histories = {}
        for sid in live_threads(threads):
            histories[sid] = {
                "moves_left": moves_left(case, threads[sid]),
                "standard_terms": standard_terms(threads[sid]),
                "messages": [],
            }
            for msg in threads[sid][-8:]:
                histories[sid]["messages"].append(
                    {
                        **{k: msg[k] for k in ("from", "body", "status", "term_sheets", "claim_results") if k in msg},
                        "attachments": [
                            {"name": a.get("name"), "text": a.get("text", "")[:3500]}
                            for a in msg.get("attachments", [])
                        ],
                    }
                )
        return json.dumps(
            {
                "case": case,
                "research": self.intelligence,
                "standing_costs": best,
                "candidate_packages": offers,
                "threads": histories,
            },
            ensure_ascii=False,
            default=str,
        )

    def guard(self, case, threads, data, candidates):
        sid, kind = data.get("supplier_id"), data.get("action")
        if sid not in live_threads(threads):
            return None
        events = threads[sid]
        if kind == "accept":
            if not any(m.get("from") == "buyer" for m in events):
                return None
            if any(not any(m.get("from") == "buyer" for m in threads[s]) for s in live_threads(threads)):
                return None
            safe = [r for r in evaluate(case, threads, self.intelligence) if r["acceptable"]]
            if safe and safe[0]["supplier_id"] == sid:
                return safe_close(case, threads, self.intelligence)
            return None
        if kind == "walk_away":
            return None
        if kind != "send" or moves_left(case, events) <= 0:
            return None
        if latest(events).get("status") in {"agreed", "final_offer"}:
            return None
        proposals = data.get("term_sheets") or []
        if not proposals:
            selected = data.get("candidate_ids") or ["A", "B", "C"]
            proposals = [p["terms"] for p in candidates.get(sid, []) if p["id"] in selected]
        sheets = []
        std = standard_terms(events)
        supplier = estimate(case, sid, std, self.intelligence)
        ceiling = alternative_cost(case)
        acceptable = [r["cost"] for r in evaluate(case, threads, self.intelligence) if r["acceptable"]]
        if acceptable:
            ceiling = min(ceiling, min(acceptable) * 1.005)
        for proposal in proposals[:3]:
            if not isinstance(proposal, dict):
                return None
            try:
                t = complete_offer(case, proposal.get("terms", proposal))
                if not valid_terms(case, t):
                    return None
                # The supplier can select any package. Block a discounted overbuy or a short
                # horizon that makes C worse than our alternative or current safe standing deal.
                if total_cost(case, t, std, supplier) > ceiling:
                    return None
            except (TypeError, ValueError, KeyError):
                return None
            if t not in sheets:
                sheets.append(t)
        if not sheets:
            return None
        body = str(data.get("body", "")).strip()
        if not body or re.search(r"<negotiation_(?:preparation|strategy)>", body):
            return None
        known = set(self.intelligence.get("facts", {}))
        known |= {standing(ev)[0] for ev in threads.values()}
        # Drop complete unsupported claim sentences, rather than just erasing a fake citation.
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n", body)
        body = " ".join(s for s in sentences if all(ref in known for ref in ID_RE.findall(s)))
        # Our counterpart's own quote ID is not rival-price evidence; don't spend a claim slot on it.
        own_offers = {
            s["term_sheet_id"]
            for m in events
            if m.get("from") != "buyer"
            for s in m.get("term_sheets", [])
            if "term_sheet_id" in s
        }
        body = ID_RE.sub(lambda m: "your offer" if m.group(0) in own_offers else m.group(0), body)
        if not body.strip():
            return None
        return {
            "supplier_id": sid,
            "message": {
                "subject": str(data.get("subject") or case["title"])[:300],
                "body": body[:8000],
                "term_sheets": [{"terms": t} for t in sheets],
            },
        }
