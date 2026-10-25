"""Minimal model output for transport fixtures, never for performance evaluation."""


def assessment_fields(payload, sid):
    context = payload.get("decision_context")
    if not context:
        return {}
    ref = context["suppliers"][sid]["latest_message_ref"]
    return {
        "assessment": {
            "observed_message": ref,
            "changes": ["Reviewed the latest fixture quotation."],
            "evidence": [
                {
                    "claim": "The supplier supplied a quotation.",
                    "kind": "supplier_claim",
                    "source_refs": [ref],
                    "confidence": "high",
                }
            ],
            "open_questions": [],
            "response_to_last_move": "Fixture response observed.",
            "adaptation": "Use the currently eligible fixture offer.",
            "next_move": "Execute the fixture action.",
        },
        "numeric_claims": [],
    }
