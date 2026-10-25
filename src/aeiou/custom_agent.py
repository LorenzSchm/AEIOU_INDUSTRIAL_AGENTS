"""Explicit hybrid buyer: learned local forecasts and the existing controlled writer."""

import json
from dataclasses import replace

from .config import ROOT
from .custom_model import CustomResponseModel
from .custom_model_data import CASES
from .execution_policy import load_execution_policies, validate_policy
from .prompt_protocol import PromptLabAgent, PromptSpec, encode_payload


def validate_custom_case(case_id):
    if case_id not in CASES:
        raise ValueError("The custom model supports case:P10 and case:P18 only")


class CustomBuyer(PromptLabAgent):
    def __init__(self, pkg, backend, model=None):
        self.response_model = model or CustomResponseModel()
        strategy = (ROOT / "prompts/universal-negotiator-v1.txt").read_text()
        spec = PromptSpec(
            "custom-p10-p18",
            strategy,
            revision="custom-p10-p18-v2",
            assessment_enabled=True,
            response_support_mode="advisory",
        )
        super().__init__(pkg, backend, spec)
        self.audit["custom_model"] = {
            "sha256": self.response_model.sha256,
            "version": self.response_model.artifact["version"],
            "role": "experimental advisory prediction with a separate strategy; no proven outcome benefit",
        }
        self.audit["custom_forecasts"] = []

    def prepare(self, case):
        validate_custom_case(case["case_id"])
        if self.case_id != case["case_id"]:
            # Snapshot this case's saved limits once. Never borrow P18 controls for P10.
            policy = load_execution_policies().get(case["case_id"])
            self.spec = replace(self.spec, execution_policy=policy)
            self.acceptance_floor = self.spec.acceptance_floor
            self.execution_policy = validate_policy(policy, self.acceptance_floor) if policy else None
        super().prepare(case)

    def render_lab(self, case, threads):
        payload = json.loads(super().render_lab(case, threads))
        # Keep the existing custom-buyer audit key without running a second model path.
        forecasts = {
            sid: data["menus"] for sid, data in payload["learned_response_support"]["suppliers"].items()
        }
        self.audit["custom_forecasts"].append(
            {
                "message_index": {sid: len(events) for sid, events in threads.items()},
                "forecasts": forecasts,
                "wording": "neutral placeholder; actual email and evidence are not evaluated",
            }
        )
        return encode_payload(payload)
