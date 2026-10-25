# P1 record: reserve a meaningful final concession

## Measured result

One authorized hosted attempt on P1, session `b116b660f138f07d`, local run
`da184e1954c94ba296111db69a9cc636`, revision `93827ae`.

- Previous account best: **73.875148**.
- New score and confirmed account best: **75.963995**.
- Improvement: **2.088847 points**.
- Deal: EUR **134.00** per unit, 14-day payment, effective 2027-01-01.
- Previous best price: EUR 134.20 on the same non-price structure.
- Must-haves passed; no buyer Claude calls, errors, or fallbacks.
- Shared quota increased exactly once: 276 to 277. Session diff contains only this
  completed session. No additional session was started.

This is a personal/account record; no competition-wide record was verified.

## Change and observed sequence

The previous attempt tied the record after its final buyer move was constrained to
EUR 133.51 -> EUR 133.55. This variant keeps the first four bids unchanged on identical
reply histories. It then offers half the remaining comparable price gap, bounded below
both the live quote and the historical agreement. Only one larger final exchange is
allowed before closing. Existing behavior remains the default unless the trial runner
sets `reserve_final_step=True`.

| Buyer proposal, EUR | Supplier reply, EUR | Payment days |
|---|---|---|
| 130.26 / 131.64 | 136.50 / 137.20 | 14 / 30 |
| 132.27 / 131.64 | 135.50 | 14 |
| 133.08 | 134.80 | 14 |
| 133.51 | 134.20 | 14 |
| 133.85 | 134.00 | 14 |

The agent accepted the last offer. This supports the hypothesis that a meaningful
reciprocal move can outperform a token final concession in this negotiation. One
observation does not prove causality, establish a supplier floor, or demonstrate
generalization to new tracks.

Five boundary tests and Ruff passed. Local archived-prefix replay confirmed the first
four offers unchanged, the new final bid, final-offer acceptance and immutable case
inputs, without API calls. Default agent registration and other track profiles were
unchanged. No model weights were trained or changed.

## Reproduction artifacts

`data/local/practice-p01-final-step-v1/` contains report.html, report.json, run.json,
preflight.json, source snapshots, config.json, start.json and quota-audit.json.
The independent one-attempt ledger is `data/local/p01-final-step-budget.json`.
The reference run remains `35240d8857984c0cafdd966c14167a90`; the test runner explicitly
sets `reserve_final_step=True`. Do not rerun its session-creation script: its attempt
has been consumed.
