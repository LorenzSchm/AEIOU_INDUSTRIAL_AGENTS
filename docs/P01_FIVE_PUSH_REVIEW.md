# P1 five-attempt campaign

The user authorized five additional P1 attempts. All five completed with deals and
passed the must-haves. Five exclusive start markers and five distinct session IDs
confirm exactly five session-creation requests; no replacement or sixth session was
started. The rolling shared quota changed during the campaign, so raw before/after
quota differences are not used to count this campaign.

Starting and finishing account best: **75.963995**. No new record in this campaign.
Mean across the five different variants: **75.059181**. This mean is descriptive;
it is not an estimate of one fixed policy's performance or isolated LLM variability.
No competition-wide record was verified.

| Attempt | Variant | Score | EUR/unit | Effective date | Session |
|---|---|---:|---:|---|---|
| 1 | Additional open-quote counter | 75.963995 | 134.00 | 2027-01-01 | 0ce06172a65e7497 |
| 2 | Documented renewal framing | 74.929815 | 134.10 | 2027-01-01 | 5ce6b1a12673d7f6 |
| 3 | Smaller early concessions | 74.929815 | 134.10 | 2027-01-01 | 0865585122b4c0b2 |
| 4 | Earlier implementation exchange | 75.365371 | 132.80 | 2026-11-01 | 07561aec658136c0 |
| 5 | Earlier date trade plus follow-up | 74.106909 | 132.90 | 2026-11-01 | 59616d80f0a755f0 |

All deals retain 14-day payment and the case's fixed non-price terms.
Buyer Claude calls, errors, and fallbacks were zero across these five runs. Hosted
supplier processing still occurred; this is not a claim of zero hosted model usage.

## Findings

1. Continuing beyond the prior winning open quote did not improve its EUR 134.00
   price; the supplier rejected the extra small concession.
2. Documenting the December contract expiry in the opening yielded EUR 134.10.
   That observation does not support promoting this wording.
3. Reducing the early gap fraction from 25% to 15% also yielded EUR 134.10.
4. Exchanging January renewal for November implementation produced EUR 132.80,
   but the earlier increase made total buyer cost worse than January at EUR 134.00.
5. Introducing the date trade one proposal earlier and allowing one bounded counter
   produced EUR 132.90 in November; the supplier held that price as final.

The earlier-date experiment only proposed a trade when its calculated buyer cost
beat both the live quote and the reference agreement. The supplier's counteroffer
need not retain that improvement, which is what happened here. Proposed economics
must not be reported as achieved outcomes.

## Retained winner and implementation scope

Keep the prior winner: session `b116b660f138f07d`, run
`da184e1954c94ba296111db69a9cc636`, EUR 134.00 / 14 days / January 2027,
score 75.963995. Its explicit configuration is the original reference run
`35240d8857984c0cafdd966c14167a90`, `reserve_final_step=True`, `max_bids=5`,
`early_fraction=0.25`, `closing_fraction=0.5`, `explain_renewal_basis=False`.
The defaults preserve this five-bid behavior; the default production factory and
other track profiles are unchanged. No learned weights were changed or trained.

Eight boundary tests passed. Ruff passed for the changed modules. Each new strategy
had a local replay before its hosted attempt. The final preflight used a copied
archived final quote marked open solely to exercise the follow-up control-flow
branch; that synthetic branch check was not a predicted supplier response or score.

## Artifacts

The aggregate report and full negotiation transcripts are in
`data/local/p01-five-push-review/report.html`, with report.json and
own-starts-audit.json alongside it. Individual source snapshots, results, run JSON,
preflights, configs and session audits are under `data/local/practice-p01-five-push-01/`
through `practice-p01-five-push-05/`. The ledger is
`data/local/p01-five-push-budget.json`, completed with zero starts remaining.
