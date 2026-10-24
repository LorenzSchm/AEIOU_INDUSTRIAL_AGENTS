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

## Follow-up after P02/P05 on `4d88195`

One real attempt each produced P02 **19.59** and P05 **50.63**, compared with
2.01/16.39 in the preceding batch. P02 admitted eight model amendments and selected
five, but several messages offered different prices for identical commercial
terms. No indexed alternative was sent. P05 contacted all three suppliers and
closed with the initially most expensive one at 31.04; its initial counter still
derived from that supplier's 38.50 opening despite a cheaper executable rival.
These are observations, not an estimate of a repeatable causal score improvement.

The follow-up preserves three distinctions:

- A price amendment replaces the same structure in the outgoing portfolio,
  retaining its executable ID. Other structures remain distinct. The decision
  trace records both the proposed and executed IDs.
- First contact with a supplier may anchor to a cheaper **valid current rival's
  total cost**. Its own terms are repriced, with a bounded search neighborhood.
  Closed or noncompliant rivals are excluded. Previously sent concessions are
  never retracted by this rule.
- When supplier-cost uncertainty blocks every indexed exchange, one explicitly
  marked experimental option may be generated. It must save direct contractual
  costs even against the anchor, improve the weighted cost sensitivities relative
  to the standing quote, and remain within 0.5% of that quote under the point
  model. The global alternative guard still applies. This intentionally accepts
  limited model disagreement; the economic weights are not calibrated
  probabilities, and real score downside remains possible. Selection preserves
  a generated risk alternative alongside an admitted planner amendment.

All terms and references come from the current case and observed messages. No
winning price or hidden benchmark is used. A prefix replay now generates an
indexed alternative in P02 and a materially lower initial offer for the third
supplier in P05. This does not predict the suppliers' responses. Generalization
and multi-issue regressions remain 91.59 and 75.59 on their reused 96/54 conditions.
The subsequent hosted validation is restricted to exactly one new P02 attempt
and one new P05 attempt, with session creation retries disabled.

## Follow-up after `9b124d3`: retain the competitive bidding position

The hosted runs reached P02 60.51 and P05 56.42. P02 accepted the indexed package
immediately; its result includes EUR 32,503.30 of relationship cost, so further
price reductions cannot be assumed to improve its score. P05's second buyer
message jumped from 25.53 to 30.48 on identical cash/delivery terms: competitive
anchoring applied only to first contact and the next portfolio regenerated from
the expensive supplier opening.

The narrow correction applies to multi-supplier RFQs with remaining negotiation
time. A comparable prior buyer offer defines the price floor. Ordinary portfolio
regeneration may move 20% of the remaining gap to an economically equivalent
current counter (35% with four or fewer turns left). It cannot retract prior
concessions or worsen modeled cost. Existing closing behavior remains for terminal
quotes and the final two turns. The planner retains its independently bounded
amendment authority. Single-supplier behavior is unchanged.

Historical-prefix replay produces 26.62 for the previously 30.48 cash package.
That is a proposed counter, not a predicted agreement or score. Validation is
restricted to 57 relevant regression tests and the small discovery clock stress;
the broader unchanged suites are not rerun. The next hosted batch again contains
exactly one P02 and one P05 attempt; P02 is a repeatability/control observation.

## Follow-up after `7b036b9`: protect value across the whole offered portfolio

The last hosted observations are P02 **60.51** and P05 **82.05**. No additional
hosted or Claude calls were made for this revision.

P02 immediately selected the indexed first-contact option. Its actual cost
included EUR 17,446.25 of index risk and EUR 32,503.30 of relationship cost. The
experimental option beat the standing quote under weighted sensitivities, but
was substantially worse than the simultaneously offered fixed-price option.
Beating an expensive opening is therefore an insufficient portfolio guard.

The revision uses only public terms, cost estimates and observed messages:

- On first contact in sustainability categories, search a bounded lower-price
  neighborhood on the existing anchor's exact terms. Weighted sensitivity cost
  must improve, point-model cost stays within the existing 0.25% anchor envelope
  and below an executable standing quote. This does not replace the production
  acceptance model and never retracts an executed price concession.
- If that refinement succeeds, no alternative may give back more than 0.25% of
  the refined anchor's weighted cost. Indexed alternatives are also compared to
  the best fixed-price candidate, not only to the supplier's opening. The same
  limits apply to model amendments and appear in the planner's proposal schema.
- After a supplier counter, explore the supplier's quoted earlier-payment term
  together with the nearest legal delivery relaxation. The price must compensate
  the entire buyer cost, including lateness and financing. There is no extra
  concession envelope for this combined trade; existing legal cash/on-time offers
  remain available in the P05 replay.

Public-opening replay now generates P02 **357.94 / 14 days / fixed price**, rather
than the previous 362.44 fixed-price anchor alongside the 356.92 indexed option.
Anchor weighted cost falls from 2,278,630.07 to 2,271,788.26, while point-model cost
rises from 2,275,297.17 to 2,280,966.65, within its 2,280,985.41 limit. This explicit
model disagreement is a bounded experiment, not a guarantee: true supplier costs
and willingness to accept fixed pricing remain unknown. No hidden scoring inputs
or historical winning price is embedded in runtime code.

P05 counter-prefix replay adds **26.46 / 14 days / 42 days delivery** beside
27.08 / 90 / 35 and 26.62 / 14 / 35. These are proposals, not realized agreements.

Validation: 55 focused regression tests passed, including invented supplier/case
structures, term legality, full trade compensation, terminal-state protection,
prior-concession preservation, and attempts by the model to reintroduce costly
risk or undo the refined anchor. A paired subset of eight existing synthetic
raw-material negotiations (two estimate variants, four supplier policies) stayed
at mean 87.07 for both frozen `7b036b9` and this revision, with no errors or
must-have violations. These reused synthetic regressions show no regression in
those conditions; they do not demonstrate a higher Arena score or LLM reliability.

## Follow-up after `338e989`: control concessions before the closing phase

The next hosted pair regressed: P02 55.93 and P05 56.21. P02 rejected the initial
fixed-price proposal and eventually accepted a more expensive indexed option.
In P05 the planner raised the first cash offer to 30.50, exploiting the previous
halfway-to-opening concession allowance. The prior-price floor then correctly
retained this expensive commitment. Deadline handling shortened the subsequent
negotiation but was not the original source of the costly proposal.

Before the final two moves, planner proposals now fit the existing controlled
portfolio envelope; they cannot independently raise the price on any matching
commercial structure. Coupled trades and buyer-cost improvements remain possible.
The controller's gradual bidding, reciprocity and terminal handling still run.
The existing bounded closing concession envelope remains available after a buyer
turn when at most two moves remain. These rules do not inspect challenge IDs.

The bounded uncertainty risk option is retained alongside the refined fixed-price
anchor: an unaccepted buyer proposal must not be treated as an available supplier
contract. This explicit exception still requires a lower base price, direct cost
and weighted sensitivity improvement over the real standing quote, the existing
0.5% point-cost limit, term legality and prior-price preservation. Ordinary model
risk amendments retain their stricter portfolio checks. This restores exploration,
not a guarantee of acceptance or higher score.

Validation: 59 focused tests passed. Replaying the recorded P05 model proposals
on the historical prefix rejects both early price increases and sends 25.97,
25.53 and 25.80 instead of adding 30.50. P02 public-opening replay yields fixed
357.94 / 14 days and indexed 356.68 / 14 days with the public permitted clause.
All preparation used zero LLM calls and zero Arena sessions. The user authorized
one subsequent hosted P02 and one P05 attempt, with session-start retries disabled.

## P01 / P03 / P04 readiness and contextual communication

After `0d50564`, read-only Arena checks show team bests of P01 70.59, P03 86.21
and P04 86.04. No new hosted attempts or Claude calls were used for this review.
The last first-five batch remains 50.28 / 82.39 / 70.05 on these three cases.

Public-thread evidence identifies different mechanisms. P01 emphasizes the
relationship and negotiates effective dates; generic repeated approval questions
and unsolicited inflation citations do not address that exchange. P03 explicitly
says its pricing is frozen, a form the old `freez` pattern missed. P04 closes
quickly; its earlier 4.94 / 90-day deal scored higher than 4.90 / 45 days because
the scoring range is narrow and financing matters substantially.

The implemented changes are general, not challenge-ID rules:

- When first-contact sensitivity refinement would leave only one commercial
  structure, reprice ordinary date, cash or duration alternatives before removing
  them. Both the expected-cost envelope and bounded point-cost check still apply.
  Existing diverse portfolios are not expanded; P02/P05 opening terms were
  explicitly compared with their successful `0d50564` runs and remain identical.
- Recognize stated price freezes and use an already bounded, one-term improvement
  at the actual quoted price after the first response. The quote stays nonbinding
  until accepted; coverage, validity, total-cost and prior-price guards still apply.
- Compose acknowledgments from the observed context: an established-relationship
  statement, a refused cost breakdown or a price freeze. Do not repeat refused
  requests, invent a history or promise extra concessions. Ask for approval without
  presuming the correspondent needs a superior; avoid claims that our packages
  match the counteroffer when that has not been established.
- Rising computed indices remain in research but are not available for unsolicited
  standalone discount citations. They remain shareable when the supplier requests
  supporting evidence. Ordinary approved evidence and binding terms are unchanged.
- Preserve cost-stressed risk alternatives that already beat the actual quote,
  rather than discarding them solely against an unaccepted hypothetical anchor.

Validation: nine historical-prefix replays, 93 passing focused tests and one
pre-existing synthetic comparison failure. That failure is identical on frozen
`0d50564`: S02/scripted/seed99 gives strategic 85.9182 versus adaptive 99.0061.
It is not waived or represented as a passing check. A separate pre-existing test
for a prematurely discarded risk alternative now passes. Ruff and diff checks pass.
Local audit: `data/local/p134-context-review.json` (includes before/after moves).
These checks establish behavior and bounds, not higher hosted scores or a causal
benefit from warmer language. No new scoreboard attempts were authorized or run.

## After the `7b0bfa1` hosted round: effective dates and financing sensitivity

The subsequently authorized hosted round scored P01 **59.77**, P03 **84.29**,
P04 **86.04**. P04 matched the existing team record; P01 and P03 remain below
their best historical results. This follow-up uses local replay only: zero new
Arena sessions, zero Claude calls.

P01's earlier-date option gave away time at the old price. Its lower nominal
price was not sufficient compensation in actual contract spend. Ordinary date
exploration now orders dates by contractual spend at the same price, then applies
the existing equivalent-package pricing and cost limits. Both policy portfolios
and model amendments reject an earlier date unless the complete direct contract
cost is no higher than the anchor. The planner receives this date-specific limit.
Earlier discounts and genuinely compensated earlier increases remain possible.
The first replayed portfolio replaces 131.87 / November / 14 days with
135.65 / April / 14 days, retaining the January anchor and cash alternative.
These are offers, not evidence that a supplier will accept delayed increases.

P03's final live menu offered 430.88 / 45 days and 431.62 / 60 days, with all other
terms identical. Direct cost favored 60 days by **EUR 6,377.81**. The old 12%
supplier-financing assumption reversed the ranking through estimated relationship
cost. The preceding distinct price/payment trade implied 4.066%; the simultaneous
menu implied 4.108%. Those are quote-implied trade-offs, not measured finance costs.

A new generic closing sensitivity can select a currently acceptable alternative
from the same supplier only when a distinct preceding trade agrees with the
current menu within one percentage point. Contract scope must match, direct cost
must improve, and all nine combinations of the published cost-confidence range
and inferred financing interval must improve. The interval extends the two
observed rates by one percentage point on each side. Original point-model cost
may worsen by at most 0.25%. Repeated identical quotes, buyer claims, changed
scope, inconsistent trade-offs or absent cost uncertainty cannot enable it.
The global model, bidding prices and continuation decisions remain unchanged.
For this P03 prefix, the weakest sensitivity improvement is EUR 4,036.75.
Its true supplier penalty and a new hosted score remain unmeasured.

Validation: **67 focused tests passed**, including synthetic payment trade-offs
at 2%, 4% and 6%, contradictory evidence and bounded-risk rejection. Offline
comparison against frozen `7b0bfa1` covered **24 historical decision points**:
P02 2/2, P05 7/7 and P04 4/4 produced identical deterministic moves and portfolios;
P03 4/5 stayed identical, with only the final acceptance changing. P01 offers
changed while its acceptance on the supplied historical replies stayed the same.
These reused prefixes verify compatibility, not independent generalization or
future stochastic LLM behavior. The previously documented S02 scripted comparison
failure is outside this focused test set and remains unresolved.

Local evidence: `data/local/date-financing-before.json`,
`data/local/date-financing-after.json`, generated by
`data/local/replay_date_financing.py`. No challenge IDs, historical prices, benchmark
scores or hidden supplier parameters were added to runtime logic.

## Contextual commercial explanations before the next hosted trio

The user authorized one new hosted P01, P03 and P04 attempt after adding the
communication layer. New explanations are derived from the actual outgoing
packages and the supplier's latest live menu: a later effective date, a payment
adjustment under a stated price freeze, or a longer contract at the quoted annual
quantity. Each receives one specific approval/exchange question. Explanations
do not invent relationships, future orders, exclusivity, urgency or authority.
Previously sent explanations are not repeated verbatim. Terminal offers cannot
activate new review requests. Existing commercial limits still determine terms.

Validation before hosting: 36 targeted tests passed; all 24 historical decision
points kept identical economic terms and portfolios versus `5c42c78`. All nine
P02/P05 historical moves, including their messages, stayed identical. P01/P03/P04
wording changed at 5/4/3 decision points respectively. No additional model call
is introduced. The hosted trio tests the combined economic and communication
revision; a single run each cannot isolate the causal effect of its wording.

## After `a132673`: preserve payment exploration and respond to date menus

The hosted trio finished at P01 64.40, P03 83.03 and P04 86.04, mean 77.83.
The runner opened exactly three sessions; a separate P18 session accounted for
the fourth increment in shared practice usage. P01's later date saved EUR 2,637.22
against its preceding run. P03's accepted cash option lost EUR 4,243.76 versus its
preceding run: the lower nominal price did not compensate for earlier payment.

Two controller mechanisms explain the missed opportunities. With multiple live
payment quotes, reciprocity selected the cheapest point-model quote and restarted
from its cash terms. This generated requests for discounts on already-priced
payment options instead of learning the next payment step. Later, a model proposal
resubmitted the supplier's prior cash quote; the supplier immediately agreed,
ending exploration even though the controller had chosen to continue. P01 left
one date alternative unanswered while repeating the same low anchors.

Changes are generic and use current legal offers and transcript evidence:

- On an explicit price restriction, group current quotes by identical commercial
  scope and extend the longest quoted payment term by one legal step at that
  quote's price. Require lower direct contract cost than every current quote,
  valid terms, full coverage and prior-price preservation. Original point-model
  cost can worsen at most 0.25% relative to the cheapest current quote and must
  stay inside the external ceiling. This is a bounded experimental offer, not
  an inferred supplier agreement or financing rate. Already-tried exact probes
  are not repeated; terminal offers remain untouched.
- Preserve this probe when admitting another model amendment. Before the closing
  phase, the model cannot resubmit an exact historical supplier quote while this
  exploration is active. Actual acceptance remains the controller's decision;
  the restriction expires when two moves remain. The planner sees the restriction.
- Recognize explicit prohibitions on price cuts, including the wording observed
  in the last P03 replies, while excluding simple negations of that prohibition.
- After two buyer turns, reciprocate separately on each comparable live date
  option, using the existing quarter-gap step and full cost ceiling. Do not wait
  for a repeated identical message to respond to the alternative date.

Validation: 89 focused tests passed, including invented quote menus, quote-order
invariance, direct and point cost bounds, failed-probe repetition, terminal states,
negation and date-menu reciprocity. Twenty-five historical-prefix replays against
frozen `a132673` keep P02 2/2, P04 4/4 and P05 7/7 identical. The P01 third buyer
message now counters both January and April, rather than repeating both prior
prices. The P03 multi-menu replay proposes 430.15 / 45 days from the live
430.15 / 30-day quote. Replaying the captured model proposal that previously
inserted 428.40 / 14 days now rejects that known fallback and retains the
45-day probe, with no provider/guard fallback.

Local artifacts: `data/local/frontier-review.json`, `frontier-before.json`,
`frontier-after.json` and `frontier-model-before.json` / `frontier-model-after.json`.
No new hosted runs or LLM calls were used. Reused histories establish behavior,
not an actual score increase or how a supplier will respond. The previously
documented S02 scripted comparison failure remains outside this focused suite
and unresolved. Runtime rules contain no challenge IDs or benchmark prices.

## Routine supplier reciprocity after the frozen P07 check

Frozen `dac4543` scored P07 63.66 against a prior best of 70.15. The supplier
reduced its main price from 5.74 to 5.60 and extended payment/duration. Our next
counter moved only 4.71 to 4.73; proposed model amendments to 4.85 and 4.80 were
blocked by the fixed early-price guard. The supplier requested more substantial
movement and finalized at 5.58. This is evidence of a rigidity risk, not proof
that a particular alternative counter would have achieved a better score.

The controller now recognizes material price movement in routine purchasing.
Activation requires a cumulative discount of at least 1% on the best complete
supplier quote, a positive latest price step and lower total cost. The public
service/volume terms must be unchanged, and payment/duration cannot deteriorate
against either the opening or preceding quote. The buyer can reciprocate at most
the latest supplier price reduction or one quarter of the remaining price gap,
whichever is smaller, rounded down to cents. The offer must remain below both
the standing quote cost and outside-option ceiling. No new model pricing freedom
is introduced; terminal offers and the final two moves keep existing behavior.
The materiality threshold is a policy heuristic, not a learned supplier floor.

This rule uses public category/contract/price observations, not P07 identifiers.
The failed P07 prefix now produces 4.85 / 90 days / 24 months, a compensated cash
option at 4.76 and a longer-duration alternative. Replaying the old excessive
5.48 model concession still rejects it. Repeated unchanged supplier quotes do
not repeatedly reward the same discount.

Validation: 69 distinct focused tests passed across two targeted runs (29 and 52,
with 12 overlapping tests). All **57** recorded P01-P05 decision points are
identical to frozen `dac4543`, including message bodies and portfolios. The
comparison covers three recent P01/P03/P04 rounds and the successful P02/P05
round. This protects observed behavior; future stochastic outcomes remain
unproven. Local artifacts are `data/local/material-progress-review.json` and
`material-progress-before.json` / `material-progress-after.json`. The user
authorized one subsequent P07 hosted attempt after these checks.

## Complete-kit approval strategy

The reciprocity revision scored P07 68.21: an improvement over 63.66, still below
the prior 70.15 best. Both recent runs ended in a final supplier offer after two
buyer messages. Further arbitrary concession tuning has weak support.

The preparation package provides a different, concrete hypothesis. The May annual
review note records that the account manager described pricing-committee approval
for changes above 2%. The September enquiry asks to consolidate a specified kit
previously sourced across suppliers. These are planning inputs, not proof of the
supplier's present authority or acceptance threshold. Recent outgoing messages
focused on longer duration and did not frame the complete-kit pricing review.

For published bundling cases with multiple parts and complete quantity/horizon
coverage, the composer now asks for approval of a consolidated complete-kit
price or the appropriate pricing review. It does not quote the internal note,
claim a committee currently exists, disclose a threshold, or promise extra volume
or exclusivity. After an actual comparable price increase in our own proposal,
it acknowledges that movement and asks which remaining adjustment permits approval.
No prices, concession limits or acceptance logic change.

Validation: 30 focused tests passed. All 57 P01-P05 historical decisions, messages
and portfolios match frozen `f5b46b4`; both P07 pre-final prefixes preserve their
exact economic terms. Examples with different synthetic part IDs check scope,
coverage, no unsupported authority claims and factual concession acknowledgment.
Local audit: `data/local/bundle-review.json`. This is a source-grounded strategy
experiment, not evidence that a new record is assured. One hosted attempt is the
maximum planned test of this revision; no retries of session creation.

Outcome: the single `5909c98` run scored **66.91**, versus 68.21 for `f5b46b4`
and the historical 70.15 best. The deal was 5.53 / 90 days / 24 months; there
were no model errors or fallbacks and quota increased by exactly one. This does
not establish causal harm from the wording, but it provides no reason to promote
the experiment. Its communication code and tests were restored exactly to
`f5b46b4`. Experimental code remains in Git history; the report is
`data/local/evaluations/practice-p07-5909c98/report.json`. The earlier protection
checks established compatibility, not confidence in beating the record. No
second hosted attempt was run.


## Bounded gap-closing reciprocity

The next experiment changes the economic counter rather than its wording. Recent
P07 transcripts terminate after the second buyer counter while the parties remain
far apart. Literal one-for-one matching of a supplier price step can preserve a
large gap created by asymmetric opening anchors. For the existing routine-buying,
material-progress gate, the buyer may now close at most one quarter of the gap,
capped at twice the latest supplier price step. This multiplier is a policy
hypothesis, not an estimate of willingness to accept or a fitted supplier floor.
Unchanged quotes, worse non-price terms, terminal offers and buyer alternative
cost limits retain their existing protections. No case or supplier IDs are used.

The saved P07 prefix produces 4.93 / 90 days / 24 months instead of 4.85, with a
compensated 4.84 / 14 days alternative. The opener and communication policy remain
unchanged. All 57 protected P01-P05 historical decisions exactly match frozen
f5b46b4 (local bundle-before.json); 71 focused tests pass, plus Ruff and formatting.
This establishes historical compatibility, not future scores. It does not resolve
the previously documented unrelated S02 controller regression. One P07 hosted
attempt is authorized; no automatic new-session retries. Local artifacts:
accelerated-progress-after.json and accelerated-progress-p07.json.


Outcome (`6b8ff24`): exactly one hosted P07 session, `0ce35b5ec7ad1289`,
scored **72.75**, exceeding the previous 70.15 best by **2.60 points** and the
restored f5b46b4 run by 4.55 points. Supplier quotes moved 5.74 -> 5.60 ->
5.51 -> 5.44; unlike recent attempts, the second response remained open.
Buyer counters were 4.71 -> 4.93 -> 5.07 at 90 days / 24 months. The final
5.44 / 90 / 24 deal costs 324252.80, or 2384.00 less than the prior best deal.
Three model calls used 16248 input / 255 output tokens, with zero API errors or
fallbacks. No model amendment was admitted; controller packages drove the bids.
Quota rose 123 -> 124; only P07 changed in the best-score map. Keep this revision,
but treat the observed extra round and record as a single-run result, not proof
of causality or reliable future gains. No further hosted attempt was made.
Dashboard report: data/local/evaluations/practice-p07-6b8ff24/report.json.


## Sustained reciprocal movement experiment

The 72.75 run reached a third exchange, then closed with a 5.44 supplier final.
The buyer's quarter-gap cap shrank its third concession from 0.22 to 0.14. The
next hypothesis is to preserve momentum only after two consecutive comparable
supplier price and cost reductions, with no worsening payment or duration terms.
In that situation the gap cap becomes one half; the separate cap of twice the
latest supplier step and the buyer cost ceilings remain unchanged. A plateau,
reversal or worsened prior terms prevents the sustained signal. No hidden floor,
challenge ID or historic winning price enters the runtime policy. The half-gap
cap is an experimental policy choice, not an empirically established optimum.

75 focused tests pass. All 57 archived P01-P05 decisions remain identical to
c6c2019's archived replay. P07's opening, second counter and terminal acceptance
are identical in an offline replay against frozen c6c2019. Only the third offer
changes, from 5.07 to 5.11 at 90 days / 24 months (cash alternative 5.02). This
cannot prove that a supplier will continue negotiating. One hosted P07 attempt
is planned, without new-session retries. Local audits: sustained-progress-after,
sustained-p07-before and sustained-p07-after JSON files under data/local.


Outcome (`9aaa548`): the sole hosted session `0826f9d47355d316` scored **72.10**,
0.65 points below the 72.75 best. The first two exchanges reproduced the previous
prices; the stronger 5.11 third bid received a final 5.45 / 90 days / 24 months
offer, with total cost 324848.80. It did not obtain an extra round. Three model
calls used 16273 input / 255 output tokens, with zero API errors or fallbacks.
Quota increased 125 -> 126 and the entire best-score map was unchanged. No second
attempt was started. This is not evidence of causal harm, but provides no reason
to promote the more generous rule. Restored concessions.py and its tests exactly
to c6c2019; all runtime code/tests match that version. The experiment remains in
Git history and data/local/evaluations/practice-p07-9aaa548/report.json. Further
bid-increment tuning has weak support; investigate executable package tradeoffs
or defensible valuation evidence before another hosted test.


## Observed cash incentive plus continuity

After the unsuccessful stronger-third-bid experiment, keep the 72.75 concession
path and change the package trade instead. P07 repeatedly quotes a lower price
for earlier payment, while our longer-duration option retains long payment.
The new portfolio rule combines an observed like-for-like cash incentive with
an already-proposed longer contract, replacing only the generic duration option.
It activates only when the existing material reciprocity guard records genuine
progress, not on openings, plateaus or final offers. A/B stay intact. The combined
proposal must be legal, preserve any prior same-structure concession and cost no
more than A under the current buyer cost model. No additional duration benefit
is assumed; acceptance and the supplier's valuation remain unknown. Longer
commitment is explicitly in the terms, not represented as a costless promise.
No supplier IDs, scenario IDs or historical scoring targets enter the rule.

Validation: 96 focused tests passed, including synthetic unseen payment and
contract lengths, unmatched quote structures, protected probes and prior-price
constraints. All 57 protected P01-P05 historical decisions are identical. A P07
replay preserves its opener, A/B, price path and terminal acceptance; the second
C becomes 4.84 / 14 / 36 and third C 4.98 / 14 / 36. Local files:
cash-continuity-protected.json and cash-continuity-p07.json. This is a policy
experiment, not proof of improved expected scores. Exactly one hosted P07 test
is planned, without session-creation retries. The 72.75 runtime is the fallback.


Outcome (`3587083`): exactly one hosted session `1630c98eea2f26ce` tied the
**72.75** record, with the same 5.44 / 90 / 24 deal and 324252.80 total cost.
The supplier's second response quoted 5.49 / 90 / 24 and 5.40 / 14 / 36, explicitly
acknowledging the combined alternative. However, the third buyer message advanced
A/B to 5.07 / 4.98 while retaining the earlier combined 4.84 / 14 / 36 bid. The
new supplier menu no longer contained a like-for-like cash-only pair, and the
portfolio/progress pipeline retained the earlier combined bid instead of advancing
it. The old-history replay could not expose this changed-response path. This is
a follow-up lifecycle limitation, not evidence of a supplier floor; inspect it
before further testing the combined-trade hypothesis.

Three model calls used 16110 input / 273 output tokens, no API errors/fallbacks.
Quota increased 128 -> 129 and best scores were unchanged. No second session was
started. A tie does not justify claiming an improvement: restore runtime/tests
exactly to fd10d6d (the 72.75 baseline), preserving experiment code in Git history.
Report: data/local/evaluations/practice-p07-3587083/report.json.


## Research review: keep supported packages on the current cost frontier

Research reviewed on 25 October 2026. The aim is a transferable policy repair,
not a table of P07 bid prices or a fitted hidden reservation value.

| Primary source | Relevant finding | Application and limits here |
| --- | --- | --- |
| [Leonardelli et al., 2019, MESOs](https://www.sciencedirect.com/science/article/pii/S074959781630557X) | Six experiments found benefits from offering several equivalent first packages, including economic and relational outcomes. | Keep meaningful, similarly valued alternatives. Human first-offer experiments do not establish a later-round effect against an LLM. |
| [Lai, Sycara and Li, 2008](https://publications.ri.cmu.edu/a-decentralized-model-for-automated-multi-attribute-negotiations-with-incomplete-information-and-general-utility-functions) | Multi-attribute counteroffers are chosen on an indifference surface near the opponent's prior offer. | Reprice an explicitly countered structure against today's buyer cost target. We borrow this principle; we do not implement their random-neighborhood algorithm or claim Pareto optimality. |
| [Baarslag et al., opponent-modeling survey](https://link.springer.com/article/10.1007/s10458-015-9309-1) | Reviews learning, reciprocal tactics and preference-model evaluation. | Separate observable quote structures from assumptions about utility. A counter is evidence of a negotiating path, not acceptance or a private floor. |
| [Chang and Fujita, COMB, 2024](https://link.springer.com/article/10.1007/s10726-024-09889-7) | Bayesian preference learning can improve model accuracy, but depends on behavioral assumptions; evaluated over 45 domains. | Publisher abstract/preview reviewed, not inaccessible full text. Our few informative replies do not justify a new calibrated acceptance model. Retain explicit uncertainty. |
| [Tey et al., 2021](https://www.sciencedirect.com/science/article/abs/pii/S0749597821000613) and [authors' explanation](https://knowledge.insead.edu/strategy/negotiators-should-decrease-concessions-across-rounds) | Decreasing concessions can signal a limit and change recipients' expectations. | Do not interpret P07's slowing cuts as proof of a floor, or assume larger buyer concessions are always better. Human distributive experiments are not a prescription for this arena. |
| [Oh et al., MERIT, 2026](https://arxiv.org/html/2602.10467v1) | Utility feedback and explicit opponent-aware reasoning improve bargaining in the authors' settings. | Keep decision feedback economically grounded. The paper is buyer-centric and excludes tool-augmented settings; wholesale prompting/training changes lack direct validation here. |
| [Zhang et al., TERMS-Bench, 2026](https://arxiv.org/html/2605.13909v1) | Separates inference, uncertainty and control failures; better posterior information alone has inconsistent benefits. | First repair how known counteroffers reach executable bids. Synthetic benchmark/oracle experiments are diagnostic inspiration, not evidence of a particular P07 gain. |

### Diagnosis from actual execution

The combined-trade run changed the supplier's menu to 5.49 / 90 / 24 and
5.40 / 14 / 36. Three individually cautious rules interacted badly:

1. Initiating the coupled trade required a live pair differing only in payment;
   the new supplier menu differed in duration too, so that trigger disappeared.
2. counteroffer_probe deliberately skipped an already-proposed matching shape
   (except indexed structures), rather than regenerating that shape at today's
   target cost.
3. preserve_progress retained our old 4.84 / 14 / 36 concession. This avoided
   withdrawing an offer but did not advance it alongside the new A/B prices.

This is a package-follow-up defect supported by traceable code and a transcript,
not a causal explanation of all low scores. The actual supplier response path
must be tested; replaying only the prior baseline cannot expose it.

### Chosen repair and alternatives deferred

Restore the combined proposal, then distinguish exploration from continuation.
Exploration still requires a real like-for-like cash discount plus material
reciprocal progress. Continuation requires the latest supplier quote to match a
combined structure in our immediately previous outgoing menu. Reprice that
structure at the current A cost limit, preserve every prior same-structure price
concession, and enforce legality, finite cost and improvement over live quotes.
A/B remain intact. Terminal messages and protected close/uncertainty/other-issue
probes cannot be displaced. The continuation does not assume equal-value seller
quotes or infer finance rates from a menu that changes several terms.

Deferred: another concession multiplier (last attempt regressed), a new Bayesian
acceptance model (too little identifying evidence), prompt-only persuasion
(does not fix the executable offer), and unrestricted model pricing (would remove
cost/duplicate protections). More market data cannot itself repair this stale
state transition. Existing research tools remain available.

### Validation and bounded experiment

104 focused tests pass, with Ruff and formatting clean. All 57 archived P01-P05
decisions are exactly unchanged. Tests include synthetic identities, different
payment/duration choices and two scaled three-turn trajectories where the seller
changes its menu in response to the proposed combination. They verify continued
movement without increasing modeled buyer cost; they are not score forecasts.
The actual 3587083 trajectory now yields 4.98 / 14 / 36 rather than stale 4.84;
its opener, second menu, A/B and terminal acceptance are unchanged. Artifacts:
data/local/cash-followup-p07.json and cash-followup-protected.json.

Exactly one hosted P07 attempt is authorized, without session-creation retries.
Compare with the 72.75 baseline and the earlier combined-trade tie, reporting
raw scores and terms. Do not infer variance or causality from that single run.
No new P01-P05 sessions are planned. Known unrelated S02 regression remains
outside these focused checks; this is not a claim that the entire suite passes.


Hosted outcome (`3f209fa`): session `b9bd096e33005f30`, **72.75**, exactly tied
with the existing record. The live trace confirms explore -> continue: combined
bids advanced 4.84 -> 4.98 at 14 days / 36 months. Supplier primary prices were
5.74 -> 5.60 -> 5.49 -> 5.44; final alternative 5.35 / 14 / 36 remained available.
The chosen 5.44 / 90 / 24 costs 324252.80; the lower sticker-price alternative
costs 324310.13, so choosing the main quote saves 57.33 under the buyer model.
The stale-offer defect is fixed, but this attempt provides no score improvement.
It also does not establish a private price floor or expected future performance.

Keep this lifecycle repair because its actual behavior and correctness are
verified, unlike the prior speculative concession multiplier. Preserve the
baseline commit fc3d541 for comparison. 104 focused tests and 57 unchanged
protected decision prefixes support compatibility; new P01-P05 hosted outcomes
remain untested. Three model calls used 16304 input / 282 output tokens, no API
errors or fallbacks, 38.31 seconds. Quota rose 129 -> 130, exactly one session;
the best-score map was unchanged. No further attempt was started. Research
hypotheses are not presented as causal performance claims.
Report: data/local/evaluations/practice-p07-3f209fa/report.json.


## Quantified conditional commitment and pricing review

After the repaired portfolio again tied at 72.75, the next bounded experiment
changes the commercial argument while preserving the economic policy. For an
executable combined cash/continuity option only, explain the total quantity over
each contractual duration and the payment trade, then request a price review
with whoever can authorize that complete package. This is different from the
old generic bundle-approval wording: it quantifies actual signed-term alternatives
after the cash/duration trade has been created. No committee, private threshold,
additional annual demand or service change is asserted. Model-supplied claims
are overwritten with code-derived quantities. Every quoted alternative must be
legal; the comparison is suppressed for differing service or annual quantities.

P07's existing 30000 annual units mean 60000 over 24 months versus 90000 over
36 months. The longer option uses 14-day payment versus the main 90-day option.
The arithmetic describes the already-executable commitments, not a promise
outside their terms or a claim of cost-free extra buyer exposure. Supplier value
and a resulting concession remain hypotheses. This is a communication experiment,
not a proven score improvement or a change to the bids themselves.

41 focused tests passed, Ruff clean. All 57 protected archived P01-P05 decisions
are exactly unchanged. All three P07 offer menus in the latest 3f209fa run replay
unchanged; only the second and third messages explain/review the trade. The
opening and terminal acceptance stay unchanged. Local audits:
commitment-protected.json and commitment-p07.json. Exactly one P07 hosted attempt
is planned, without session-creation retries; no other categories are tested.


Outcome (`6f107f3`): sole session `9756614085332d5f` scored **70.15**, 2.60
points below the 72.75 best. Final agreement 5.48 / 90 / 24, total cost 326636.80.
After the quantified 90000-unit contract comparison the supplier requested the
underlying forecast; the next reply discussed index evidence and ended bargaining.
This is an observed trajectory, not proof of a causal wording effect. Three model
calls, 18027 input / 273 output tokens, no API errors/fallbacks, 53.53 seconds.
Quota increased 130 -> 131; the entire best-score map was unchanged. No second
attempt was started. Restore grounding.py and remove this experiment's tests,
returning runtime/tests exactly to 0123709; retain the verified package-follow-up
fix. The experiment and tests remain recoverable in Git history.

A follow-up observation is that broad evidence_requested matching on "provide
... data" can treat a forecast request as permission to volunteer cost-index
cards. The next evidence-selection audit should distinguish these intents; this
turn does not silently modify that separate subsystem or claim it would improve
P07. Report: data/local/evaluations/practice-p07-6f107f3/report.json.


## P08: cost-ranked index exploration

The existing P08 best is 59.1487 (session 7587c332bf02874e): price 5.26,
payment 45, duration 36, index share 45%, deadband 0. Its modeled index and
risk charges total 31414.82. Changing only the deadband to 5 would save
22439.16, but supplier acceptance of that hypothetical change is unknown.

Opening exploration previously used the first legal index enum. For leverage
categories, rank available clauses by modeled buyer cost at a common price,
then apply the existing bounded equal-cost compensation. No challenge IDs,
historical score normalizers, or supplier-specific floors enter the policy.
P08 opening indexed package changes from 4.80/deadband0 to 4.91/deadband5,
with payment90 and duration24 unchanged. The fixed-price anchor is unchanged.
This is a better trade proposal, not a guarantee of a better agreement.
Relationship-oriented categories keep their existing uncertainty policy.

Validation: 108 focused tests passed. Four synthetic cases vary index direction
and enum order; rising indices favor larger deadbands, falling indices favor
smaller ones, always within the protected buyer budget. All 57 archived P01-P05
decisions and portfolios remain exactly identical. One hosted P08 attempt is
planned, with exclusive creation markers and no session-creation retries.


P08 outcome (6daa3e5): exactly one session ada3a253fb544573, score **49.47**,
9.68 points below the prior 59.15 best. Agreement 5.52/payment90/duration24,
AS-14404 share45%/deadband5%; total buyer cost 1049034.05 versus 1032035.14.
Quota 133 -> 134, entire best map unchanged. Three model calls, 19782 input /
304 output tokens, no errors or fallbacks, 102.97 seconds. No repeat attempted.

The supplier adopted the index protection, but higher base price outweighed
payment and index savings. The live model cited the trigger letter instead of
the available fixed-alloy hedge evidence. Two fixed-price menu slots persisted
through repeated demands for indexation; no 36-month exchange was tested.
These are concrete next targets, not proof they will improve scores. Retain the
small cost-ranking correction as enum-order-independent economic behavior,
not as a proven score improvement; all protected P01-P05 replays are unchanged.
Further strategy changes require separate validation. Dashboard report:
data/local/evaluations/practice-p08-6daa3e5/report.json.
