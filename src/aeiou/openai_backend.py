"""OpenAI Responses adapter. Each call owns its stream, deadline and cancellation."""

import asyncio
import time

import openai

from .agent import BUYER_DECISION_BUDGET_S, BuyerDecisionTimeout, EmptyModelResponseError, ProviderRefusalError
from .config import model_settings, require_model_key


class IncompleteModelResponseError(RuntimeError):
    pass


class OpenAIBackend:
    provider = "openai"
    client = None  # Async clients are closed inside their own event loop.

    def __init__(self, model="gpt-5.5", max_tokens=None, timeout=None, *, effort=None, speed=None):
        settings = model_settings(model, effort)
        if settings["provider"] != self.provider:
            raise ValueError("OpenAIBackend requires an OpenAI model")
        if speed not in (None, "standard"):
            raise ValueError("OpenAI evaluations use standard mode")
        self.model, self.effort, self.speed = model, settings["effort"], "standard"
        self.api_key = require_model_key(model)
        self.timeout_s = timeout if timeout is not None else BUYER_DECISION_BUDGET_S
        self.max_tokens = max_tokens if max_tokens is not None else 12000
        self.calls = self.errors = self.input_tokens = self.output_tokens = 0
        self.reasoning_tokens = self.cached_input_tokens = self.refusals = self.empty_responses = 0
        self.stop_reasons, self.refusal_details = {}, []
        self.resolved_model, self.cancel_event = None, None

    def set_cancel_event(self, cancel):
        self.cancel_event = cancel

    def _check_cancelled(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise RuntimeError("Stopped at your request")

    def complete_json(self, system, user, schema):
        return self.complete(system, user, output_schema=schema)

    def plan_negotiation(self, payload):
        from .agent import parse
        from .strategic_planner import PLANNER_SYSTEM

        return parse(self.complete(PLANNER_SYSTEM, payload))

    def complete_before(self, system, user, deadline):
        return self.complete(system, user, deadline=deadline)

    async def _read_response(self, system, user, output_schema, remaining):
        text = {}
        if output_schema is not None:
            text["format"] = {"type": "json_schema", "name": "negotiation_action", "schema": output_schema}
        # Fix the destination: an Anthropic credential or a configured proxy must never
        # accidentally receive this provider's requests.
        async with openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=remaining,
        ) as client:
            stream = await client.responses.create(
                model=self.model,
                instructions=system,
                input=[{"role": "user", "content": user}],
                reasoning={"effort": self.effort},
                max_output_tokens=self.max_tokens,
                store=False,
                stream=True,
                **({"text": text} if text else {}),
            )
            async with stream:
                async for event in stream:
                    if event.type in {"response.completed", "response.incomplete", "response.failed"}:
                        return event.response
                    if event.type == "error":
                        raise RuntimeError("OpenAI stream reported an error; no action was sent")
        raise IncompleteModelResponseError("OpenAI stream ended without a complete response; no action was sent")

    async def _with_deadline(self, system, user, output_schema, remaining):
        # SDK socket timeouts alone reset as data arrives. This timeout covers the
        # entire streamed request, including connection, reasoning and output.
        async with asyncio.timeout(remaining):
            task = asyncio.create_task(self._read_response(system, user, output_schema, remaining))
            try:
                while True:
                    self._check_cancelled()
                    done, _ = await asyncio.wait({task}, timeout=0.1)
                    if done:
                        self._check_cancelled()
                        return task.result()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def complete(self, system, user, *, output_schema=None, deadline=None):
        self._check_cancelled()
        deadline = min(deadline, time.monotonic() + self.timeout_s) if deadline else time.monotonic() + self.timeout_s
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BuyerDecisionTimeout("Buyer decision time budget exhausted; no OpenAI request was sent")
        self.calls += 1
        try:
            response = asyncio.run(self._with_deadline(system, user, output_schema, remaining))
            if response.usage:
                self.input_tokens += response.usage.input_tokens
                self.output_tokens += response.usage.output_tokens
                self.reasoning_tokens += getattr(response.usage.output_tokens_details, "reasoning_tokens", 0) or 0
                self.cached_input_tokens += getattr(response.usage.input_tokens_details, "cached_tokens", 0) or 0
            self.resolved_model = response.model
            reason = response.status or "unknown"
            self.stop_reasons[reason] = self.stop_reasons.get(reason, 0) + 1
            if time.monotonic() >= deadline:
                raise BuyerDecisionTimeout("OpenAI exceeded the buyer decision budget; no action was sent")
            if any(
                block.type == "refusal" for item in response.output if item.type == "message" for block in item.content
            ):
                self.refusals += 1
                safe = {"model": self.model, "category": "refusal", "response_id": response.id}
                self.refusal_details.append(safe)
                raise ProviderRefusalError("OpenAI declined this decision; no action was sent", safe)
            if reason != "completed":
                detail = getattr(response.incomplete_details, "reason", None) or reason
                raise IncompleteModelResponseError(f"OpenAI response incomplete ({detail}); no action was sent")
            if not response.output_text.strip():
                self.empty_responses += 1
                raise EmptyModelResponseError("OpenAI returned no text; no action was sent")
            return response.output_text
        except (TimeoutError, openai.APITimeoutError) as exc:
            self.errors += 1
            raise BuyerDecisionTimeout(
                f"OpenAI buyer timed out with {remaining:.1f}s available; no action was sent"
            ) from exc
        except openai.APIError as exc:
            self.errors += 1
            # Do not expose response bodies, request headers or credentials in the UI.
            status = getattr(exc, "status_code", None)
            raise RuntimeError(f"OpenAI request failed ({type(exc).__name__}, HTTP {status or 'unavailable'})") from exc
        except Exception:
            self.errors += 1
            raise
