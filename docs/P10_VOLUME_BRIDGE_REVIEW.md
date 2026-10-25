# P10 staged commitment with forecast evidence

One additional hosted attempt was authorized after the completed twenty-attempt campaign.
The isolated experiment is `CapacityCommitmentNegotiator`; the production factory and
previous winning profiles are unchanged. This is known-case adaptation, not evidence of
generalization or a fine-tuned language model.

## Hypothesis and validation

Preserve the previously successful 24-to-36-month commitment sequence and the supplier's
quoted 54-day lead time. Explain that the September shared forecast of 21,310 units is
2,090 below the current annual requirement of 23,400. Distinguish existing forecast data
from a newly proposed binding commitment; do not invent a three-year forecast.

The local replay checked both proposals, legal supplier-quoted lead time, final-offer
acceptance, and input immutability without API calls. Ruff passed. The experiment remains
opt-in and uses no learned artifact on its normal two-proposal path.

## Observed result

- Revision tested: `f9e7b22`.
- Session: `5ae9c45440d80675`; local run: `0531016f2ec14472b70ccf41c7dc6212`.
- Previous account best: 70.208043; result: 67.866358; difference: -2.341685 points.
- Deal: EUR 31.60/unit, 23,400/year, 36 months, 54 days. Must-haves passed.
- Previous best price on the same structure: EUR 31.30/unit.
- Buyer Claude API calls: zero. This does not mean the hosted supplier used no LLM.
- Both cited claims were verified. The supplier explicitly said the volume data did not
  change its calculation, while taking the should-cost data into account.
- Opening EUR 32.20 -> first reply EUR 31.80 -> final EUR 31.60.

The extra explanation did not improve this observation. One run cannot identify whether
wording, repeated evidence, or supplier variability caused the difference. Keep the
previous winner; do not promote this experiment or repeat it without a new hypothesis.

Artifacts are under `data/local/practice-p10-volume-bridge-v1/`: report.html, report.json,
run.json, preflight.json, start.json and quota-audit.json. The independent one-attempt
ledger is `data/local/p10-volume-bridge-budget.json`.

## Shared quota audit

This runner issued one session-creation request, recorded with an exclusive marker and
no creation retries. The shared account counter rose by two (273 to 275). The read-only
session diff also shows a second P10 session, `ce34449c6742a38d`, open concurrently and
not owned by this runner. Its origin was not established; it was not modified or closed.
