# P1 early reciprocal concessions

## Result

One hosted attempt, session `7fd2347a076d6658`, run
`b6367193f0dd407097400b044b6d5c4a`, revision `606fcf7`.
Score: **73.875148**, exactly matching the previous account best. No new record.
The shared account quota increased by one, 275 to 276, and the session diff contains
only this completed session. No buyer Claude API calls, errors or fallbacks.

Deal: EUR 134.20/unit, 14-day payment, effective 2027-01-01. Other terms unchanged;
must-haves passed. This is an account-best comparison, not a competition-wide claim.

## Hypothesis

The previous winning P1 negotiation repeated EUR 132.27 three times before raising
the bid to EUR 133.55. The supplier explicitly asked for movement. This experiment
preserved the first two offers, then moved a quarter of the comparable live price
gap immediately, capped at the same historical EUR 133.55 buyer offer.

The implementation is `RenewalReciprocityNegotiator`, an explicit known-case opt-in
experiment. It does not change the default factory, other winning profiles, or
learned model weights. Four boundary tests and a local archived-prefix replay passed.
The replay checked unchanged first two offers, bounded monotonic concessions, and
acceptance of a final offer. Ruff passed.

## What the negotiation showed

The supplier reached EUR 134.20 while still open, one reply before its final offer.
However, the buyer's last increase was only EUR 133.51 to EUR 133.55. The supplier
acknowledged the small step and refused a further price reduction, making EUR 134.20
final. The agent accepted that offer.

This is evidence against expecting an improvement from this particular concession
schedule. It does not establish a hard supplier floor or a causal effect of wording.
The cap consumed nearly all available movement before the final exchange. A future
experiment could reserve a meaningful final concession rather than reducing it to
four cents, while keeping the requested price below the current supplier offer.
No further session was started for that untested hypothesis.

Artifacts: `data/local/practice-p01-early-reciprocity-v1/` contains report.html,
report.json, run.json, preflight.json, start.json and quota-audit.json. Independent
one-attempt ledger: `data/local/p01-reciprocity-budget.json`.
