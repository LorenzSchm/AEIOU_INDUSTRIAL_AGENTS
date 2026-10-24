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


## Strategic planner and economics audit — 24 October 2026

Baseline: `b88f961` on `paulstestbranch`. The implementation adds a strategic
Claude proposal path, broader opponent hypotheses, uncertain supplier economics,
bounded response scenarios and strategy-prompt optimization. It does not establish
a higher hosted score. No Arena session or model API call was used for this revision.

### Production behavior

Claude now proposes up to two amendments to existing complete packages. A separate
validator admits at most one into the outgoing portfolio, alongside the protected
buyer-value offer and a policy fallback. It rejects illegal fields/options, unmet
must-haves, reduced policy coverage, repeated packages, retracted comparable price
concessions and excessive full buyer cost. Where an acceptable supplier quote
exists, the proposal must improve it across all available supplier-cost scenarios.
It must retain at least 65% of the anchor's modeled advantage over that quote.
Without an acceptable quote, the existing policy envelope remains the limit.
These are policy limits, not learned agreement probabilities. The model cannot
accept a deal, change suppliers, override a final offer or change the validator.

The planner replaces the writer call and its separate interpretation call; it does
not add another provider call per decision. Existing provider timeout, token cap,
no-retry policy and deterministic fallback remain. The exact active prompt is
`prompts/strategic-current.txt`; the communication-only fallback is separately
preserved in `prompts/strategic-writer-fallback.txt`.

Additional preference hypotheses cover quality/liability, index protection,
delivery and capacity, including competing signs where priorities are ambiguous.
They update from matched package choices. An identical repeated experiment does
not multiply its evidential weight. The learned beliefs advise Claude and rank
admitted planner proposals. They do not automatically replace the established
policy's portfolio selection. A causal intervention test checks that changing the
quality-cost belief can change which validated proposal is sent.

Supplier economics uses the public should-cost error band and a tempered quote
likelihood, without treating a quote as proof of a floor. Repeated quotes do not
manufacture confidence. Scenario costs evaluate the nonlinear relationship penalty
before averaging. These estimates are visible as decision-support diagnostics and
sensitivity checks. Automatic expected-cost control is **off by default**, as is
the new utility-equivalent response rollout controller. Both can be enabled in
explicit evaluations via `strategic-policy:with-economic-uncertainty` and
`strategic-policy:with-response-rollouts` respectively.

### Evidence and rejected variants

An early integration automatically used the expanded opponent ensemble for the
existing deterministic selector. On the reused 54-condition multi-issue suite,
this reduced the mean from 81.539 to 77.824 points. Disabling the new response
rollouts did not recover it; removing the new automatic preference selection did.
That integration was rejected. These ablation data informed implementation and
are not independent holdout evidence.

The expected-cost model correctly reverses the problematic historical P02 ranking
using only the original opening quote to update beliefs: modeled cost changes from
EUR 2,284,923.83 versus EUR 2,322,601.89 to EUR 2,296,160.71 versus EUR 2,293,545.93
(last ten-agent run versus historical best). Actual costs were EUR 2,275,433.52
versus EUR 2,257,068.10. However, its absolute error worsens on P01 and P03. This is
why it is not promoted to the default economic controller or calibrated against
specific challenge targets.

In 100 generated economic conditions, with hidden true costs and finance rates
available only to the evaluator, mean absolute cost error falls from EUR 8,249.48
to EUR 6,998.37 (15.2%). Mean decision regret falls from EUR 2,658.17 to EUR 2,563.41
(3.6%). Pair-ranking error falls from 15.42% to 14.89%. These are cost-estimation
diagnostics on fixed candidate menus, not negotiation scores or a guarantee that
the scenario assumptions transfer to the Arena.

The final runtime source was hashed before the new sealed comparison. The new set
uses different seeds, supplier sensitivities and cross-issue cost interactions;
it still reuses three case families. No production source changed after observing
its results.

| Final offline comparison | Conditions per arm | Frozen b88f961 | Final defaults | Wins / ties / losses |
|---|---:|---:|---:|---:|
| Reused generalization regression | 96 | 91.593 | 91.593 | 0 / 96 / 0 |
| New sealed multi-issue conditions | 54 | 75.189 | 75.189 | 0 / 54 / 0 |

All final conditions have zero execution failures and zero must-have violations.
These runs use no LLM and therefore cannot validate Claude's new commercial
proposal quality, real latency or language effects. Mock-backed tests validate
actual execution, malformed/adversarial proposals, cost limits, terminal offers,
call counts and causal belief effects. Full suite: **188 passed, 2 skipped**;
Ruff and `git diff --check` passed.

### Reproduction and bounded optimization

`python -m aeiou.economics_audit --output data/local/economics-audit.json`
reproduces the synthetic economics diagnosis without network access.
`python src/aeiou/portfolio_evaluation.py --split sealed --data data/package
--output data/local/sealed.json` reproduces the new population; `--agent-source`
can select the frozen baseline. `--disable` supports feature ablations.

`aeiou optimize-strategy` adds bounded reflective search over commercial strategy
instructions, with disjoint train/validation/test base cases, explicit session
limits and API opt-in. Reflection sees only training feedback. It cannot modify
code or validators. Winners are never automatically promoted. The existing
`optimize-writer` explicitly uses the communication-only path so it still measures
writer changes. An explicitly selected, hash-checked strategy result may be loaded
with `AEIOU_STRATEGY_INSTRUCTION_FILE`. No paid optimization was run here. This is
an extension of the existing bounded search, not a reproduction of GEPA, PSRO or
weight fine-tuning; LLM opponent rollouts and reinforcement learning are not added.

Machine-readable evidence, source hashes, historical diagnostics and limitations
are in `evaluation/strategic-planner-v3.json`. The research motivating the design
includes [Cicero](https://ai.meta.com/research/cicero),
[dialogue rollouts](https://aclanthology.org/D17-1259/),
[GEPA](https://arxiv.org/abs/2507.19457) and
[PSRO](https://arxiv.org/abs/1711.00832). Their published gains are not projected onto
our competition score.


## Final readiness revision — 24 October 2026

This revision addresses observed implementation failures, with `1dd5dbc` as the
frozen reference. No new hosted session or paid model call was started. Use the
`strategic` agent for the next user-initiated Arena test.

### Findings from the four sandbox runs

D01 scored 89.07 / 76.78 (mean 82.92); D02 scored 47.05 / 47.05. Both D02 runs
closed at EUR 15.40 under the deadline hook after only three or four planner
turns. The earlier EUR 14.20 reference had eight decisions. New planner decisions
took approximately 2–3 seconds, while complete sessions took 120–137 seconds.
The evidence points to reply waiting and the scheduler as a concrete bottleneck;
it does not isolate an LLM-only causal effect.

The old deadline hook reserved the slowest full supplier reply plus 12 seconds
before allowing any continuation. That reserve is appropriate when switching to
another supplier, whose reply does not pause the incumbent's clock. It is too
restrictive when sending a counter directly to the incumbent, whose own supplier
reply time does not count against its message deadline (Arena API time rules).

D01's weaker run also dropped a previously offered EUR 47.00 / 30-day / January
package from a later email. A rebuilt portfolio asked for less supplier value,
and the supplier explicitly said the packages did not represent progress. The
existing guard protected identical model-proposed structures, but did not retain
prior model concessions when the deterministic portfolio was regenerated.

### Final behavior

When an acceptable incumbent has an open quote, at least one counter plus an
acceptance move remains, and more than eight seconds remain to send, the deadline
hook can prioritize a direct reply to that incumbent. The urgent turn uses code
only and makes no LLM call. Final/agreed offers, exhausted rounds and genuinely
short remaining deadlines still close safely. Timing hints apply to one decision.
The existing stale-offer refresh, bounded recovery and economic acceptance checks
remain active. A Live Final wave cap still has precedence over continuation.

The portfolio retains a previously sent, legal concession on a structure that the
supplier has actually countered on, if it still beats the current quote and global
cost ceiling. Buyer-value A and settlement probes remain protected. Prior offers
that are now too expensive or irrelevant to the current structure are not retained.
Broader ordinary-language price-resistance phrases trigger the existing bounded
concession policy. No demo IDs, historical winning prices or hidden floors are
used in the production rules.

The planner receives a focused supplier history, explicit editable-field schema,
valid current base IDs and recent buyer packages. It still receives the buyer
requirements, full standing costs and approved evidence. On one saved D02 prefix,
serialized context falls from 28,255 to 12,512 characters (55.7%); this is a payload
diagnostic, not measured API token savings or a claim of better language reasoning.
Identical repetitions of fixed terms are removed before validation; changed fixed
terms and unknown fields are rejected. Rejections now distinguish unknown package
IDs from illegal field changes. The submitted base ID and field names are logged
for diagnosis, without recording arbitrary free-form model output.

Uncertain expected-cost control and automatic response rollouts remain disabled
by default; their earlier evidence did not justify production promotion. Supplier
cost uncertainty still informs diagnostics and proposal sensitivity checks.

### Validation

| Offline comparison | Conditions per version | Reference | Final candidate | Wins / ties / losses |
|---|---:|---:|---:|---:|
| RFQ timing, stable supplier order | 24 | 27.77 | 62.66 | 18 / 6 / 0 |
| RFQ timing, changing supplier advantage | 18 | 32.45 | 42.70 | 6 / 12 / 0 |
| Reused multi-issue regression | 54 | 75.19 | 75.59 | 3 / 51 / 0 |
| Reused generalization regression | 96 | 91.59 | 91.59 | 0 / 96 / 0 |

Across 192 conditions: 27 improvements, 165 ties, no declines. The timing evaluator
uses 2–4 suppliers, multiple reply delays, one session per case, current-offer IDs,
60-second per-thread clocks and automatic timeout events. A reply pauses only its
own clock. Private concession schedules remain exclusively in the evaluator. The
second timing profile has fresh seeds/delays and makes initially more expensive
suppliers capable of deeper concessions. All timing cases produce legal deals.

These are synthetic scenario scores, not Arena forecasts. Prioritizing an incumbent
can deliberately let other supplier threads expire: total timeout events in the
stable timing suite rise from 6 to 72 while every case still closes with a valid
deal. That tradeoff remains a limitation, particularly with unknown supplier upside.
The non-timing suites are reused regressions, not independent new holdouts.

Full test suite: **192 passed, 2 skipped**. Ruff and diff checks pass. New tests
verify code-only urgent continuation, final-offer protection, rejection of changed
fixed terms and preservation/retirement of observed concessions. Historical replay
confirms the earlier D01 concession now survives portfolio regeneration. No actual
Claude generation was evaluated after this revision.

Reproduce timing checks with `python src/aeiou/timing_evaluation.py --output
 data/local/timing.json`; add `--profile reversed` for the second population or
`--agent-source data/local/frozen-1dd5dbc/src` for the reference. Machine-readable
paired rows, source hashes, exact defaults, replay and limitations are in
`evaluation/final-readiness.json`. The active prompt is exported in
`prompts/strategic-current.txt`.

## Practice score repair after the five `3c0df55` runs

The five new hosted scores average **44.22/100**, despite five legal deals and no
provider failures. This is a commercial-policy problem, not an API reliability
problem. The published score is the fraction of the opening-to-benchmark cost
gap captured by the deal, clipped to 0–100. A small discount can therefore produce
a very low score, particularly after financing or other terms consume it.

| Case | Score | Saving against opening | Remaining gap to benchmark | Observed failure |
|---|---:|---:|---:|---|
| P01 | 50.28 | EUR 28,639.11 | EUR 28,324.85 | Four identical buyer portfolios; all eight model proposals rejected |
| P02 | 2.01 | EUR 952.89 | EUR 46,400.55 | Three identical portfolios; all six proposals rejected; EUR 23,078.15 financing cost at 14 days |
| P03 | 82.39 | EUR 277,871.65 | EUR 59,399.27 | Four identical outgoing portfolios despite the supplier's price-freeze explanation |
| P04 | 70.05 | EUR 1,623.00 | EUR 694.01 | Supplier closed price negotiations; buyer still sought a large price cut with other term changes |
| P05 | 16.39 | EUR 13,461.84 | EUR 68,658.95 | Only one of three suppliers contacted; timing reserve exceeded the entire message window |

The previous ten-run batch also contains weak P02/P11/P18 outcomes and a P05
transport failure (`not a standing offer`). Nine saved results have scores; the
failed P05 record has no result score. Its zero in the earlier batch aggregate is
a failure penalty, not a measured deal score. Historical team-best scores reflect
unequal attempts and different versions and must not be treated as paired controls.

### Causes and changes

1. **The model was constrained into repeating the policy.** Of 28 submitted
   proposals, four were admitted and three selected. P01/P02 admitted none. A
   35%-of-anchor-gap envelope and a requirement to beat the quote in every cost
   scenario turned uncertain supplier-cost assumptions into vetoes. The planner
   now receives the exact numeric envelope used by validation. Existing legal
   policy concessions remain available; the model may use 50% of the remaining
   anchor-to-quote gap early and 75% after two buyer turns. It still must improve
   the actual standing quote under both the production model and the weighted
   sensitivity model, and remain inside the external alternative ceiling.
   Worst-scenario losses are recorded explicitly. These weights remain assumptions,
   not calibrated probabilities; this change deliberately trades a universal
   worst-scenario guarantee for bounded exploration.

2. **There was no dependable response to a negotiation impasse.** A new recovery
   policy detects repeated buyer portfolios or explicit requests for reciprocity.
   After repeated counters, it moves one quarter of the remaining price gap on
   terms the supplier has actually offered. On an explicit impasse, it can ask
   for one adjacent beneficial non-price change at the observed price. Each
   option must improve complete modeled cost, preserve coverage and earlier
   comparable concessions, and obey all legal terms. A stale price is replaced
   on its structure, rather than offering two prices for identical terms. Active
   early negotiations do not automatically get near-quote offers.
   Historical-prefix replay demonstrates real changes to P01/P02 portfolios.
   For P04 it requests the next payment step on the supplier's existing contract
   structure. At the recorded price, 45-to-60-day payment would save EUR 653.33;
   supplier acceptance remains unknown. This is a counterfactual cost calculation,
   not a new achieved score or a hardcoded case-specific price.

3. **The deadline policy confused a safe deal with a good competitive outcome.**
   P05's reserve reached roughly 62 seconds against a 60-second message clock,
   so the incumbent kept receiving urgent policy turns while competitors were
   never contacted. The revised policy may make one first contact with an
   untouched open supplier when the incumbent stalls or worsens. It protects an
   incumbent whose observed acceptable total cost is still falling by more than
   0.1%. It does not explore against a final/last-round offer, a hard eight-second
   deadline, or an incumbent already carrying an unanswered timeout update.
   Forecast reply time must fit within at most one additional timeout window.
   Urgent decisions remain code-only. Slower-than-observed future replies and
   withdrawals remain possible; this is not a timing guarantee.

4. **A concrete supplier question was ignored.** In P05 the supplier asked for
   more volume, which the case does not allow negotiating. The composer now
   explicitly answers that the annual commitment is fixed instead of ignoring
   the request or offering an illegal change.

5. **Diagnostics and tests hid the failure modes.** Rejected proposals previously
   retained only base IDs and field names; their actual rejected prices cannot
   be reconstructed reliably. New traces retain changes, executable terms,
   modeled costs, the budget and scenario deltas. An offline audit tool separates
   scored runs from unscored failures and replays only historical prefixes with
   no future results exposed to the agent. The timing evaluator now includes
   suppliers whose competitive advantage is revealed only after first contact.

The production agent contains no P/D case-ID switches, benchmark costs, previous
winning terms or hidden supplier floors. Public should-cost and supplier-financing
models remain imperfect. The final estimated costs differ from actual costs by
about EUR 1,650 in P01, EUR 6,588 in P02 and EUR 17,115 in P03. Expanding the planner
does not by itself establish that these estimates are calibrated.

### Verification and limitations

| Offline comparison | Conditions per version | `3c0df55` | Revised |
|---|---:|---:|---:|
| Reused generalization regression | 96 | 91.59 | 91.59 |
| Reused multi-issue regression | 54 | 75.59 | 75.59 |
| Timing: stable advantage | 24 | 62.66 | 62.66 |
| Timing: reversed advantage | 18 | 42.70 | 42.70 |
| Discovery stress used during development | 18 | 40.48 | 48.03 |
| Discovery stress, fresh seeds and delays | 18 | 42.86 | 50.01 |

The new random draws use seeds 719341/230017 and delays 17/36/54 seconds. They test
the same discovery mechanism, not an independent real-world supplier population.
All reported final runs close legally, with no must-have violations. No new Claude
calls, Sandbox sessions or Practice scoreboard attempts were used for this repair.
There is **no newly measured Arena score for the revised version**.

The complete local test suite passes **210 tests with 2 skips**; Ruff and diff
checks pass. Regression tests cover impasse recovery, productive early talks,
invalid standing terms, cost ceilings, terminal offers, weighted cost uncertainty,
first-contact deadlines, ongoing incumbent progress and timeout exhaustion.

Two broader changes were rejected during development: unconditional near-quote
offers reduced the generalization and multi-issue averages to 79.91 and 41.69;
exploring even while the incumbent improved reduced the stable timing average to
53.48. The shipped conditions were narrowed to address those regressions. This
development history is retained in the machine-readable evidence rather than
presenting only successful experiments.

Reproduce the record audit with `python -m aeiou.practice_review
data/local/practice-first-five-3c0df55 --replay --output data/local/review.json`.
Run the new clock profile with `python src/aeiou/timing_evaluation.py --profile
discovery --seeds 719341 230017 --delays 17 36 54 --output data/local/discovery.json`;
use `--agent-source` for a frozen baseline checkout. Paired rows, historical
evidence, limitations and source hashes are in
`evaluation/practice-score-repair.json`; the local dashboard has a matching
**Score repair — observed causes and offline validation** experiment entry.
