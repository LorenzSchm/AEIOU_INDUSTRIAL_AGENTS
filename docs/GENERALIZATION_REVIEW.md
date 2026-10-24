# Practice and sandbox review: generalization rather than case memorization

## What the evidence supports

The latest ten Practice sessions averaged **48.71/100** including the P05 timeout.
The current team bests average **64.72/100**. Bests are maxima over unequal attempt
counts and may come from different agents; this is a diagnostic comparison, not a
controlled experiment or evidence that one version caused the whole gap.

| Case | Latest Practice run | Best observed |
|---|---:|---:|
| P01 | 56.29 | 70.59 |
| P02 | 15.70 | 54.48 |
| P03 | 83.03 | 83.03 |
| P04 | 86.04 | 86.04 |
| P05 | 0.00 | 45.88 |
| P07 | 70.15 | 70.15 |
| P08 | 49.31 | 59.15 |
| P10 | 64.73 | 64.73 |
| P11 | 21.24 | 61.12 |
| P18 | 40.62 | 52.00 |

The latest sandbox results on `e91326a` are D01 **77.20** and D02 **72.32**,
both valid deals without provider/guard fallback. D01 is below its historical
two-run mean of 84.63, D02 slightly above its 71.27 reference mean. One run per
case is insufficient to distinguish policy effects from supplier/writer variation.
The preceding stale-offer fix was not exercised by these two sandbox runs.

Reading the historical *public outcomes*, not hidden supplier floors, reveals:

- P02's best observed agreement used a compensated index clause and 30-day
  payment; the latest fixed-price agreement demanded 14-day payment. A low cash
  price and total buyer value are different objectives. The point estimate of
  supplier sustainability can rule out an otherwise interesting commercial trade.
- P11's better agreement used 1500 ppm and EUR 300 compensation at EUR 46/unit,
  instead of 300 ppm / EUR 500 at EUR 48.61. The lesson is joint optimization of
  contractual quality, price and compensation under the actual must-haves.
- P05 demonstrates execution risk: an agreed supplier quote is not a completed
  transaction. The existing refresh/deadline/recovery fix is more fundamental than
  another persuasive sentence. Four of the 48 historical team sessions ended by
  timeout, across unknown versions and operating conditions.
- The two demos do not establish coverage of quality, capacity or material-risk
  negotiation. Their gains cannot justify assuming the controller is universally good.

## Implemented changes

This is a refinement of the decision policy, **not training Claude's model weights**.
There are no branches on P01/P02/D01/etc., stored winning price targets, private
supplier utilities, or post-run `c_best` inputs in the agent.

1. **Compare prices under the same conditions.** The observed supplier price step
   now excludes changes in payment, quality, duration and other negotiated terms.
   A discounted price bought with an earlier payment is not free price flexibility.
2. **Do not infer unsupported preferences.** The cash/duration/freight hypothesis
   model does not update its weights from choices involving quality, index, volume
   or delivery changes it does not model. Cash specialization requires at least two
   observations and two-thirds of the hypothesis mass, not a barely winning guess.
   These weights remain assumptions rather than calibrated acceptance probabilities.
3. **Expire transient resistance signals.** Settlement/concession pressure and
   diagnostic wording use the latest supplier message. A past objection does not
   automatically cause another concession after a subsequent cooperative response.
   Structured package-choice history remains available independently.
4. **Bound exploratory trades under uncertain supplier costs.** If strict
   point-estimate compensation produces no exploratory variant, a second path
   considers direct-buyer-cost-compensated trades. It requires an explicit valid
   estimate sensitivity percentage and improvement versus the actual standing
   quote at low/central/high supplier-cost assumptions. Legal terms, global cost
   ceilings and protected A remain. These probes can be more expensive than the
   optimistic A anchor; they do not dominate it and do not prove acceptance.
   Three stress points are neither a statistical confidence interval nor a
   guarantee over all possible supplier costs. A dedicated full-controller test
   exercises this path; it did **not** activate in the randomized holdout below.

## Evaluation design and result

`src/aeiou/generalization.py` creates new identifiers, prices, volumes, financing
rates, freight/defect costs, round limits and noisy buyer-side supplier-cost estimates.
Both agent versions receive identical generated inputs. Hidden simulator economics
stay inside the simulator. Arena construction is blocked and no model backend is
used. The baseline is frozen source from `e91326a`, not a reconstructed approximation.

An initial 40-condition development run per version tied. Coverage review then
added a synthetic index-risk template; the final development set has 12 generated
cases and four supplier profiles (48 conditions per version). After freezing and
hashing candidate source, a separate seed generates 24 holdout cases and four
profiles (96 conditions per version). No production changes were made using those
holdout outcomes. Both versions have identical dataset hashes.

| Evaluation | Reference | Candidate | Failures / must-have violations |
|---|---:|---:|---:|
| Development, 48 conditions each | 95.75 | 95.75 | 0 / 0 |
| Holdout, 96 conditions each | 91.59 | 91.59 | 0 / 0 |

**All 96 held-out paired outcomes tied. No score improvement was demonstrated.**
These are variations of six templates with simple scripted opponents; they are
not 24 independent real-world negotiation families. They omit LLM variation and
live latency, and simplify supplier sustainability costs. High synthetic scores
must not be presented as expected Arena scores. The no-regression check passes,
but the suite has limited power to evaluate these changes.

Audit values, the frozen source hashes and limitations are preserved in
`docs/evaluation/generalization-audit.json`. Full local run rows remain under
`data/local/generalization-*.json`. All historical Practice/Sandbox scores above
remain measurements of their original versions.

## Next useful evidence, before further tuning

- Collect disagreements between predicted package choices and actual public
  replies, separating changed conditions, explicit refusals and terminal actions.
  Calibrate on one set of suppliers and evaluate on different suppliers/families.
- Add withheld multi-issue supplier policies that actually price quality, index
  risk, capacity and date changes. Simple cash/price scripts cannot validate these
  dimensions. Keep some policies hidden from controller development.
- Compare timeouts, deal rate, worst-case results, cost and usage alongside mean
  score. A policy that occasionally negotiates brilliantly but loses valid deals
  can perform worse overall.
- Only then consider a small, explicitly budgeted language-supplier evaluation.
  Fine-tuning model weights on a few successful challenge transcripts would risk
  memorizing prices and losing useful negotiation diversity; it is not justified
  by the present dataset.

Only read-only Arena calls were made during this review. No new Practice or
Sandbox session was opened, no scoreboard write was requested, and no paid LLM
call was made.

Final local validation: **158 tests passed, 2 skipped**; Ruff and whitespace
checks passed. Regression coverage includes confounded price changes, unsupported
preference inference, expired signals, renamed unknown cases, invalid estimate
bands and a complete uncertainty-probe path through the economic controller.

## Subsequent commercial portfolio revision

The exact previously composed writer prompt is exported in
`prompts/strategic-before-portfolio.txt`; `prompts/strategic-current.txt` contains
the new default grounded writer prompt. The per-turn JSON context remains dynamic.
The old combined prompt permitted custom packages in one section and prohibited
them in another. The default writer now has one communication-only output contract.
Its system text shrank from 5,197 to 2,188 characters; no token or quality gain is
inferred from character count alone. Ungrounded experimental mode retains its
separate existing prompt.

The commercial planner now offers joint quality/complaint-liability alternatives,
missing index/payment alternatives, and bounded timely-delivery/commitment options.
It preserves buyer-value A, explicit supplier counteroffer probes, full coverage,
must-haves and the global alternative/standing-offer ceiling. Equal-cost probes are
preferred. Where an additional envelope is used, it retains at least 75% of the
anchor's estimated improvement over the current quote. Strategic supplier estimates
must also pass low/central/high cost stresses. These bounds are policy assumptions,
not calibrated acceptance probabilities.

Incoming noncompliant quality quotes can be cost references for remediation but
cannot be accepted; all outgoing options still require legal terms. Prior concessions
on an identical structure cannot be silently withdrawn by the new probes. Existing
index alternatives are retained rather than crowding out payment alternatives.
Explanations of quality, index and delivery trades are composed only when those
trades actually appear in the outgoing packages. The cash-price concession estimator
is now shared by the opponent model and optimizer, preventing changed non-price
conditions from becoming a fictitious price-only concession in either path.

This does **not** establish a materially higher Arena score. P02's opening still
does not admit an indexed package under the supplied cost estimate and conservative
envelope; the historical winning indexed deal is not used to override that estimate.

| Offline comparison | Conditions per version | Frozen 68d714b | Candidate |
|---|---:|---:|---:|
| Multi-issue development | 27 | 81.353 | 81.356 |
| New multi-issue holdout | 54 | 81.539 | 81.539 |
| Existing generalization regression | 96 | 91.587 | 91.593 |

The new holdout comprises 18 generated cases across three families and three
scripted sensitivity policies, including indifferent and reversed preferences.
All 54 paired results tie. The reused 96-condition regression has one improvement
(+0.547 points), one small decline (-0.013 points), and 94 ties. Both versions have
zero execution failures and zero must-have violations in all three comparisons.
These simulations exclude LLM wording effects and live deadline behavior and must
not be presented as hosted performance predictions. Development revisions preceded
the recorded source freeze; no production changes followed holdout inspection.

Validation: **169 tests passed, 2 skipped**; Ruff passed. Audit, dataset hashes,
source hashes and limitations are in `evaluation/commercial-portfolio.json`.
No hosted Arena session or paid LLM call was started for this revision.
