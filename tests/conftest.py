import json

import pytest


@pytest.fixture
def model_supplier(monkeypatch):
    """Transport-only test double. Never used in simulations or performance reports."""

    class Supplier:
        calls = errors = input_tokens = output_tokens = 0

        def __init__(self, **kwargs):
            pass

        def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            package = min(payload["package_catalog"], key=lambda p: p["terms"]["unit_price"])
            return json.dumps(
                {
                    "status": "final_offer",
                    "body": "Test quotation.",
                    "package_ids": [package["id"]],
                    "repeat_unchanged": False,
                }
            )

    monkeypatch.setattr("aeiou.agent.ClaudeBackend", Supplier)
    return Supplier
