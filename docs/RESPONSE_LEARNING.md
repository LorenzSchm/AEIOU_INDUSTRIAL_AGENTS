# Local learning from hosted negotiation histories

## Decision: keep the trained model offline

On 2026-10-25 we trained two regularized logistic response models on the existing
local hosted-run archive. Neither met the predefined predictive gate on withheld
challenges. No production agent imports these modules or reads their artifacts.
P2/P5 and the existing deterministic closing rules remain unchanged. No API calls,
Arena sessions, external training uploads or new dependencies were used.

The pipeline is available for future, better-supported experiments:

```powershell
.venv/Scripts/python.exe -m aeiou.response_training --runs data/local/runs --output data/local/response-learning-new
```

Use a new output directory; existing evaluation reports cannot be overwritten by
the command. The command writes dataset.json, predictions.json, model.json,
report.json and a readable report.html. The dataset contains numerical features,
labels and provenance IDs rather than message bodies or credentials. Artifacts
remain in ignored local runtime storage; source, tests and this audit are tracked.

## Data and labels

87 hosted logs existed; one lacked a case needed for extraction. The remaining
86 sessions supplied335 clean outgoing-menu/next-response pairs across12 challenge
groups. All2905 local simulated logs were excluded. One missing reply and two
offers sent after a terminal supplier state were excluded. The latter include
the P1 experimental closing error: it must not be learned as successful conduct.

Each example represents an entire menu, not an independently tried package:

- `package_matched`: the supplier's immediate response contains a package matching
  every explicitly proposed field of at least one menu option. This is not final
  agreement probability. There are17 positive examples across6 groups.
- `direct_cost_improved`: the immediate counteroffer has lower direct buyer cost
  than the preceding live quote. There are267 positive examples across12 groups.
  This omits unknown supplier-health penalties and is not the final Arena score.

Features use only the preceding public case/messages and the candidate outgoing
menu: relative prices, payment/date/duration/index changes, prior concession,
direct-cost gap and a few public text cues. Neither case/supplier identity, final
score, future text, future attachments nor inferred private cost enters features.
Labels naturally read the next response; features do not. Missing/nonmatching
response data and technical recovery messages are not silently negative labels.

Session IDs deduplicate logs. Connected case/supplier groups enforce that a
supplier shared across multiple challenges cannot leak between train and test.
The evaluation holds out each complete group in turn. Feature scaling and all
coefficients fit only its training fold. Equal group weighting and equal session
weighting inside groups prevent repeated attempts from dominating the score.

## Measured results

Session-then-group-averaged Brier error; **lower is better**:

| Prediction target | Trained model | Training-fold base rate | Similar-history retrieval | Fixed rule |
| --- | ---: | ---: | ---: | ---: |
| Package matched | 0.09558 | **0.09135** | 0.09683 | 0.09639 |
| Direct cost improved | 0.20790 | 0.16983 | **0.15431** | 0.17230 |

The model's errors are4.64% and34.73% higher than the respective strongest
baseline. Exploratory group-bootstrap95% intervals for baseline error minus
learned error are[-0.02021,0.00742] and[-0.08120,-0.02611]. These are error
differences, not Arena-point changes. Few groups and small cases limit precision.

The predefined gate requires at least8 groups, positives in at least5 groups,
at least5% relative Brier improvement over the strongest baseline, a positive
lower group-bootstrap bound, and lower log-loss. Neither target passes.
Even a passing predictive model would require a separate policy evaluation before
being allowed to change live offers. Historical response accuracy cannot tell
us what would have happened after an unobserved alternative action.

Retrieval performs better than the learned model on cost progress, but this does
not establish that using it to choose actions improves negotiation results. Its
neighbors are drawn only from the training fold, with at most one per session.

## Validation and provenance

The initial v1 extraction skipped96 otherwise usable steps because the quote
parser represents missing effective dates as the literal string `none`. The v2
correction treats that as an absent date. No features, hyperparameters or gates
were tuned to improve held-out performance. Both reports are preserved, and this
second evaluation should still be treated as exploratory rather than an untouched
prospective test.

Final dataset SHA256:
`a4801d820fb069724d5db38779703c999112011ff1937f1711da9492edae5993`.

Artifacts: `data/local/response-learning-v2/`.
Tests cover menu-level labels, future-data independence, terminal-state and
transport exclusions, stale sheets on withdrawal, missing-date serialization,
session deduplication, transitive supplier grouping, model serialization, learning
a known synthetic signal and rejection of insufficiently supported promotion.

## Next evidence needed

Preserve the pipeline and the existing successful agent. Collect more varied,
clean responses during already-authorized future runs, especially real examples
of supplier acceptance and refusal at comparable states. Keep entire unseen
supplier/scenario families sealed for the next evaluation. Do not launch a batch
of paid Arena attempts just to increase training-set size. Do not fine-tune a
language model on all successful transcripts and treat every message as optimal.

## Larger text-and-terms neural model: completed experiment

The user explicitly requested a larger model despite the first negative result.
We expanded the source archive before testing it. A read-only GET exporter fetched
143 additional completed practice sessions, retaining86 locally recorded sessions
(including sandbox runs). The archive contains229 unique sessions;224 yield706
clean paired steps across the same12 challenge groups. There are27 immediate
package matches and568 direct-cost improvements. Fourteen proposals after a
terminal state and one missing response are excluded. Practice usage was204 both
before and after the export. No new negotiations or model API calls occurred.

The new predictor is a CPU multilayer perceptron with256 and64 hidden neurons,
ReLU activations, two output probabilities and546,754 trainable parameters. It
trains for60 fixed epochs using regularization and group/session sample weights.
Inputs combine the existing22 numeric/public-signal features with up to2048
TF-IDF word and two-word features from the conversation prefix, including the
outgoing offer. This is a response classifier, not a pretrained generative LLM.

Only training-fold text fits each vocabulary and numeric scaler. The next supplier
reply, result and future attachments are excluded from input. Identifiers, numeric
prices, email addresses and credential-like strings are removed from the text;
relative numeric terms remain explicit features. Natural-language names can still
occur in bodies, which is one reason entire supplier groups remain held out.

All competitors are retrained on the same expanded data and identical held-out
groups: the original small numeric model, training-fold base rate, similar-state
retrieval, and a regularized linear text+terms model. Architecture, seed741 and
60-epoch budget were fixed before seeing these scores; no test-score search was
performed. There is no random message-level train/test split or random early-
stopping validation split. Earlier experiments already inspected these challenge
families, so this remains diagnostic grouped validation, not a sealed prospective
test.

Session-then-group-averaged Brier errors on the expanded archive:

| Target | Larger neural | Small numeric | Base rate | Retrieval | Linear text+terms |
| --- | ---: | ---: | ---: | ---: | ---: |
| Package matched | 0.074780 | 0.077387 | 0.068139 | **0.068039** | 0.088444 |
| Direct cost improved | 0.204919 | 0.185655 | **0.176349** | 0.178566 | 0.218257 |

The neural model slightly beats the small trained model for package matching,
and its log-loss for this target also improves. However, Brier error is9.91%
higher than the strongest baseline. Direct-cost prediction error is16.20% higher
than its strongest baseline. Exploratory baseline-minus-neural Brier intervals
are[-0.02206,0.00465] and[-0.07770,0.02126]. Neither passes the existing gate.
These are prediction metrics, not Arena score changes. No weights are promoted
to the production agent, and P2/P5 remain unchanged.

The larger archive alone changes the evaluation distribution; numbers from the
first86-session experiment must not be treated as a controlled before/after
comparison. The table above provides the comparable same-data baselines.

Reproduction (optional isolated dependencies; the application runtime is untouched):

```powershell
.venv/Scripts/python.exe -m pip install --target data/local/learning-deps -r requirements-learning.txt
.venv/Scripts/python.exe -m aeiou.response_archive --output data/local/response-corpus-expanded
.venv/Scripts/python.exe -m aeiou.response_neural --dependency-root data/local/learning-deps --runs data/local/response-corpus-expanded/runs --output data/local/response-neural-new
```

The exporter only reads existing sessions. To freeze the exact measured corpus,
reuse the saved archive rather than fetching newly completed sessions. Optional
library versions are pinned in requirements-learning.txt. The deployed runtime
has no imports of these experiment modules.

Artifacts: `data/local/response-neural-v1/` includes the trusted locally generated
model.joblib, model metadata, full report, prediction rows and dataset. Only load
trusted pickle/joblib files. The original smaller model remains available.
Seventeen tests cover both pipelines, including prefix truncation, redaction,
training-only vocabulary/scaling and a saved neural-model prediction roundtrip.

## Refined training: better predictions, still below deployment evidence threshold

Following the user's request to improve the model, we inspected the prior errors.
For example, the neural model averaged0.931 probability of a direct-cost improvement
on P4, while only11 of31 recorded responses improved direct cost. That is a diagnostic
on already observed data, not a newly sealed evaluation.

The refinement adds8 prefix-feedback features to the original22: prior response
count, smoothed progress frequency, latest progress, last-two progress, stagnation
streak, prior package-match frequency, latest match and missing-history indicator.
Only response outcomes already visible before the outgoing offer may enter these
features. A previous response from the same conversation is available at inference
time; the current or future response is not. Tests explicitly mutate future labels
to verify that they cannot change the current feature vector. Sessions and supplier
threads cannot share feedback accidentally.

Estimator choice and probability shrinkage use nested groups. For each of12 outer
held-out challenge/supplier groups, the remaining groups are split into3 inner
folds. A fixed set compares a base rate, a smoothed conversation-history estimate,
regularized logistic regression, shallow boosted trees and128 randomized trees.
Predictions can shrink toward the training-fold prior at fixed strengths25/50/75/100%.
Only inner out-of-group predictions select the estimator and strength; outer labels
are never available for these choices. Numeric preprocessing is training-only.
No further model search was performed after seeing the following outer scores.

Comparison on the exact same706 examples and same outer groups as the neural run:

| Target | Previous neural | Refined selection | Strongest simple comparator |
| --- | ---: | ---: | ---: |
| Package matched | 0.074780 | 0.069031 | history0.067764 |
| Direct cost improved | 0.204919 | **0.164806** | history0.174697 |

These are session-then-group-averaged Brier errors, not Arena points. The refined
procedure reduces error7.69% and19.58% relative to the previous neural model.
Against the newly included simple history comparator, package matching is1.87%
worse, while direct-cost prediction is5.66% better. The latter's exploratory95%
group-bootstrap error-difference interval[-0.00555,0.02545] includes zero. The
package-match interval[-0.00334,0.00059] also includes zero. Neither target passes
the existing full deployment-evidence gate. The gate was not relaxed to promote it.

Fitting the final offline artifact on all data selects the history estimator for
package matching and a50% randomized-forest /50% prior blend for cost progress.
This full-data selection is distinct from the differing choices made inside each
outer evaluation fold; reported errors assess the selection procedure. Reliability
bins, every inner split and every outer choice are retained in the report.

Artifacts: `data/local/response-refinement-v2/` (report.html, report.json,
predictions.json, model.json and trusted local model.joblib). The v1 run emitted
numeric roundoff warnings in the library's weighted variance for constant features.
We replaced that normalization with an explicitly nonnegative centered variance
calculation, added a regression test and repeated the identical experiment. V2
has the same model selections and Brier results to floating-point precision; no
hyperparameter or feature changes were made for that repeat. Both reports remain.

Twenty-four focused tests pass. No training API, upload, dependency change or Arena
attempt was needed in this refinement. Production/P2/P5 behavior remains untouched.
The next validation of policy effectiveness still needs evidence beyond repeatedly
inspecting these same challenge families; these numbers support better response
prediction, not a demonstrated increase in negotiation scores.

```powershell
.venv/Scripts/python.exe -m aeiou.response_refinement --dependency-root data/local/learning-deps --dataset data/local/response-neural-v1/dataset.json --previous-predictions data/local/response-neural-v1/predictions.json --output data/local/response-refinement-new
```

Method references: [nested model-selection evaluation](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html)
and [probability evaluation and calibration](https://scikit-learn.org/stable/modules/calibration.html).

## Authorized hosted test: one attempt each P1â€“P5

On 2026-10-25 the user requested one run on tracks 1â€“5. An isolated runner used
commit `826f408` and the trusted refinement-v2 artifact (SHA256
`660351c1b7f6f6a25c9bbf5c08efcead84442533743629ba844917090a243447`).
The learned adapter ranks menus of already admitted packages by predicted next
direct-cost progress; it cannot invent terms or override terminal/deadline closing.
Seven focused adapter tests passed before the hosted batch. Production factory
and existing agent behavior remain unchanged; the adapter was injected only in
this experiment process. The existing Claude planner remained active.

| Track | Score / 100 | Historical team best before | Difference | Session |
| --- | ---: | ---: | ---: | --- |
| P1 | 56.7740 | 70.5890 | -13.8150 | ed752082eceee03e |
| P2 | 60.6569 | 73.5404 | -12.8835 | aa6353dd06573733 |
| P3 | 83.0077 | 86.2062 | -3.1986 | e5dc0c5d39faaa07 |
| P4 | 86.0445 | 86.0445 | 0.0000 | 59edca498616ef20 |
| P5 | **92.3787** | 82.0453 | **+10.3334** | 3f3e05748d75ab8f |

All five completed with deals and valid must-haves. Mean across the five tracks:
75.7723. Quota moved from 206 to 211; all five newly listed sessions match the
per-track one-request creation markers. There were no replacement sessions.
Buyer usage: 17 Claude calls, 95,767 input tokens and 1,571 output tokens. No
planner fallbacks or model-selection errors were recorded. P1 did have a transport
rejection: accepting alternative `offer:70110-5-a` returned HTTP 422; recovery
excluded that alternative and accepted the primary April offer at EUR 139.50.
Its body had offered EUR 134.10 for November, but that alternative could not be
accepted via its term-sheet ID. This is an execution limitation, not evidence
that the model's predicted progress probability was correct.

P2 and P3 had no learned menu changes. P2's historical best came from a separate
90-day calibrated strategy, whereas this general agent accepted 14-day terms.
P4 changed menus but tied its record. P5 accepted supplier 70250 at EUR 27.00,
14 days payment, 35 days lead time, 24 months, DAP. The winning supplier's menus
were NOT changed by the learned selector: its only actually guarded menu change
was for supplier 70240 (A/B/C to A). Repeated controller audits for supplier 70240
while considering other suppliers must not be counted as distinct sent changes.
The final P5 concession cited free capacity and quick payment. Thus the new P5
record is real, but cannot be attributed to the learned model from this run.

This is not a paired experiment and historical maxima are not controls. One run
per track cannot estimate variance or disentangle model selection, Claude choices,
supplier variability and deadline timing. The challenge families appeared in the
training archive; this does not establish unseen-challenge generalization. The
adapter remains experimental rather than replacing the production agent.

Local artifacts: `data/local/practice-p01-p05-learned-v1/` contains the five raw
runs, runner/adapter/model snapshots, creation markers, quota audit, JSON and HTML
reports. The dashboard copy is
`data/local/evaluations/practice-p01-p05-learned-v1/report.json`.

## Remaining five hosted tracks: same experiment, no retraining

The follow-up request authorized one attempt on each of the other five hosted
tracks. Availability preflight established that these are P07, P08, P10, P11 and
P18; P06 and P09 are not hosted. An initial P06â€“P10 preflight stopped before any
session creation. The actual batch used commit `0f7a9e8`, unchanged adapter code
and the same refinement-v2 model hash as above. No strategy changes or retraining
were made between the two batches.

| Track | Score / 100 | Historical team best before | Difference | Session |
| --- | ---: | ---: | ---: | --- |
| P7 | 72.7504 | 72.7504 | 0.0000 | 1800006a0874c4d6 |
| P8 | 53.7966 | 62.4556 | -8.6591 | c0609a5ab592b871 |
| P10 | 56.2462 | 70.2080 | -13.9618 | cc0ac2b624c033dc |
| P11 | **61.9591** | 61.1230 | **+0.8362** | 9b3297f46b768c41 |
| P18 | 38.6079 | 68.4565 | -29.8486 | 0d6610ca6ea71215 |

Mean across these five: 56.6720. All five completed with deals and valid
must-haves. No planner fallbacks, model-selection errors or transport events were
recorded. Buyer usage: 15 Claude calls, 91,534 input tokens, 1,578 output tokens.
The batch's five exclusive creation markers each record exactly one request and
match five distinct completed sessions. Shared quota rose 211 to 223: the saved
session audit contains these five plus seven additional sessions outside this
runner, so the entire quota delta must not be attributed to this experiment.

P7 and P10 had no model menu changes. P8 had one controller recommendation change
but no guarded outgoing menu changes. P11 had two guarded changes for its sole
supplier; it closed at EUR 45.50, 60-day payment, 1,500 PPM, FCA. P18 had one
guarded change and closed at EUR 5.50, 90-day payment, AS-14404 share45% with zero
deadband and 24 months. Its evaluated index cost and index risk totaled EUR
8,212.05. P8 closed at EUR 5.48 with a5% index deadband and90-day payment. P10
closed at EUR33.70, 23,400units, 36months, 30-day payment. These are outcome
diagnostics, not evidence that any one intervention caused the score differences.

The two batches together achieved10 deals and a66.2222 mean. P5 and P11 exceeded
their historical bests; P4 and P7 tied; the other six were lower. Historical
maxima are optimistic, unpaired comparators, so this does not estimate causal
degradation either. The evidence does not justify globally promoting this learned
selector. Keep it experimental and investigate the existing execution/decision
limitations before spending additional Arena attempts.

Artifacts: `data/local/practice-remaining-five-learned-v1/` contains all five
runs, frozen model and source snapshots, creation markers, quota audit and the
HTML/JSON reports. Dashboard copy:
`data/local/evaluations/practice-remaining-five-learned-v1/report.json`.

## Value-model upgrade: complete packages, observable signals, magnitude and exits

The next user request was to improve the model. Implemented a separate offline
model in `response_value.py` and `response_value_features.py`; the production
factory and the P5-record experiment remain unchanged. No Arena session or model
API call was used. The prior model artifact was not modified.

Inputs grow from30 to86 features: the original22 and8 prefix-history features,
10 evidence-backed literal business signals,4 context/cost-exposure features,
and3 canonical complete-package slots with14 fields each. Slots preserve the
association between price, payment, date, duration, lead time, volume, quality,
chargeback, tooling, freight, index exposure and total direct cost. Reordering
a menu does not change the representation; swapping payment terms between two
different prices does. More than3 packages fails explicitly, without truncation.

Signals include quick payment, free capacity, capacity pressure, internal approval,
competition, acknowledgment of evidence and closing warnings. Matched clauses
and polarity are retained for auditing; basic negation prevents explicit denials
from being counted as positive signals. This deterministic phrase extractor is
not a general semantic model and cannot resolve all ambiguous or quoted language.
No supplier/case identity, private cost, final score or future reply enters inputs.

Three prediction heads:

- `direct_cost_improved`: retain the original label for an exact old-model comparison.
- `quoted_cost_gain`: signed `(previous_primary_cost - next_primary_cost) /
  previous_primary_cost`, using the standard terms visible in the prefix. The
  primary quote is used rather than treating every alternative as executable.
  Worsening quotes have negative labels; gains are not clipped. Private supplier
  relationship penalties are unknown and excluded, so this is not final utility.
- `supplier_exit`: observed next response `walked_away` or `no_bid`. Stale quote
  sheets on exits never provide a gain label. No next quote means missing gain,
  not a fabricated zero or a fabricated loss. This does not predict every possible
  later failure or execution error.

The frozen706-step corpus includes224 usable sessions from229 archived sessions
and12 connected challenge/supplier groups. There are704 gain labels and only2
supplier exits, in2 groups. Final model selection is trained solely on this
archive; the latest10 test sessions are excluded from fitting. Estimator/shrinkage
selection uses3 inner whole-group folds within each outer leave-one-group-out
evaluation. Fixed candidates: training prior, observed-prefix history, forest on
the old30 features, forest on86 features and shallow boosted trees on86 features.
Candidates and shrinkage grid were fixed before inspecting their outer results.

| Target | Nested selected MSE | Strongest comparator | Interpretation |
| --- | ---: | ---: | --- |
| Immediate progress | 0.162142 | old-feature forest0.156690 | 3.48% worse than strongest comparator;1.62% better than prior refinement0.164806 |
| Signed quoted-cost gain | 0.001757 | old-feature forest0.001728 | 1.71% worse;50.92% less error than constant prior0.003580 |
| Supplier exit | 0.00227504 | history0.00227489 | Rare events; insufficient evidence for operational risk estimates |

None passes the predeclared predictive gate (at least8 groups,5 positive groups,
5% improvement over the strongest baseline, positive95% group-bootstrap lower
bound). We did not select whichever outer-test candidate scored best after seeing
the results. Final full-archive choices are old-feature forest at50% strength for
progress, rich boosted model at100% for gain, and prefix history for exits. These
all-data choices differ from some outer-fold selections; outer metrics evaluate
the selection procedure. Empirical90th-percentile absolute errors are descriptive
residual references, not confidence guarantees for untried offers.

Additional diagnostic: all32 usable steps from the latest10 hosted test sessions
were replayed locally, with no fitting or candidate selection on those outcomes.
The feature design did follow earlier inspection of these runs, so this is NOT a
fresh sealed holdout. Progress Brier error0.176460 vs previous0.176709 (0.14% lower).
Gain MSE0.001204 vs old-feature forest0.001439 (16.29% lower) and training-prior
0.004252 (71.68% lower). The rich forest's diagnostic MSE0.000992 was better still,
but we did not switch to it after seeing this check. There were no exits in these
10 sessions, so low exit Brier error is not evidence of useful risk detection.

`live_row()` constructs identical features using only an observed prefix and a
proposed menu, with current outcomes explicitly missing. Every recent replay
input matched the training extractor, and predictions matched within1e-12
floating-point tolerance. `forecast()` returns predictions, signal evidence,
residual references, predictive gates and an explicit `retain_incumbent` decision.
It cannot authorize a changed offer, acceptance or a broader action policy.
Training causal acceptance/clarification/supplier-switch decisions still needs
better action coverage and prospective action-selection logging; these menu logs
do not identify those unobserved outcomes.

Validation:23 focused tests across value/refinement/portfolio modules, including
9 value-model tests. They cover package association/order invariance, negation,
future-label isolation, signed gain, stale sheets on exits, session isolation,
group-balanced metrics, nested split separation, serialization, one-class targets,
live/training parity and unchanged closing protections. Ruff checks pass.

Artifacts: `data/local/response-value-v1/` contains the trained trusted
`model.joblib`, full nested report, predictions, enriched frozen dataset, source
snapshots and recent diagnostic. The old P5 artifact and production remain intact.

```powershell
.venv/Scripts/python.exe -m aeiou.response_value --dependency-root data/local/learning-deps --dataset data/local/response-neural-v1/dataset.json --runs data/local/response-corpus-expanded/runs --previous data/local/response-refinement-v2/predictions.json --output data/local/response-value-new
```

## One authorized value-controlled P10 trial

The user requested one track on which the new model might beat a record. Added
`ValuePortfolioNegotiator`, an opt-in experimental subclass with no production
registration. It ranks existing admitted menus by the selected gain head, requires
a0.005 predicted gain advantage (fraction of prior quoted cost), and rejects menus
with a predicted progress-probability drop greater than0.05. These fixed exploratory
bounds are not confidence guarantees. Anchor/protected probes and existing final
offer/deadline closure are preserved. The poorly supported exit head is not used
to authorize risks. Activation requires `experimental=True`; model metadata and
the failed predictive gates are unchanged. Eight new selector tests plus seven
previous portfolio tests passed before the trial.

Local replay of ten archived openings found changed actual proposals on P5 and
P10. Chose P10: the new selection removes the more expensive36-month package B,
leaving24-month A/C packages at27.69/42-day and26.97/56-day terms. Predicted primary
cost gain moves from0.172087 to0.183752, while predicted progress changes from
0.890442 to0.888320. Track selection is exploratory, not a reliable estimate of
record-breaking probability. No model retraining or threshold sweep preceded it.

One hosted attempt on commit `98cf03b`, session `a0cb3e6d1895c62f`, run
`a2bb1836b76c497ea78456bcb0db79ac`:

- Score64.7294 vs historical best70.2080: **-5.4786 points**. Previous learned-menu
  P10 run scored56.2462, so this is8.4832 points higher; these are unpaired outcomes.
- Deal: EUR31.90,23,400units/year,24months,56-day lead time,30-day payment,DAP.
- Direct cost EUR1,541,808, including EUR33,600 late-delivery cost.
- The outgoing menu actually changed to A/C. Controller and message guard each
  logged the same change; this is one intervention, not two independent changes.
- Supplier immediately returned `final_offer`, rejecting the requested prices
  but offering full volume and24months. The incumbent closing rule accepted it.
- One Claude call,4,021input and85output tokens,16.5seconds, no model/planner or
  transport errors. Quota223â†’224; one creation marker and one new session in audit.

This did not beat the record. The delivery penalty suggests studying the joint
price/delivery response, not assuming a cheaper unit price makes a superior deal.
The same price at a shorter lead time was NOT offered, so removing the penalty
in a cost calculation would only be a hypothetical, not a missed available deal.
The prior production strategy and P5 record artifact remain untouched. No second
attempt was started. Artifacts: `data/local/practice-p10-value-v1/`, with frozen
model, source, preflight, complete run, quota audit and HTML/JSON reports.

## Sunday release integration (25 October 2026)

Installed the official eight-file Sunday release under `data/release-sunday/`.
Archive SHA256: `e708cfaf20483873c10e9343119640b8e11ff62af2c4e8195431cb7b0243b5bd`.
The ten additional source IDs are factual context, not negotiation outcomes;
no model weights were retrained and no synthetic response labels were created.

Research now merges full release news bodies and letters with the base package.
Relevant supplier and global context reaches the strategic planner, with internal
memos excluded from supplier-facing evidence cards. Supplier letters and relevant
news can be cited through the existing exact-excerpt guard; the recycling article
marked as noise is excluded from release planning and retrieval. Material-cover
dates remain part of the quotation rather than implying immediate cost changes.

Explicit case `as_of` dates take precedence. Final cases (`set=hidden` in the API,
or `final`) can use the installed release through 23 October; historical practice,
sandbox and unspecified cases retain the 30 September cutoff. Retrieval corpus
statistics also respect that cutoff, preventing future documents from changing
historical rankings. The original database and index series remain untouched.
New processes load the release automatically; an already-running dashboard caches
its package and needs a restart to load this code and data.

Validation: 13 release-specific tests passed, including the actual planner call
and outgoing-message guard. Across five relevant test files, 75 tests passed and
two existing simulation score assertions failed identically on committed baseline
`e498de8`. Ten archived hosted openings were also replayed locally: all ten actions
matched with and without release activation. This checks compatibility, not a
live score improvement. No Arena sessions or model API calls were used.

## Observed liability trade: one P11 trial

Revision `830a902` protects a lower-cost liability option after comparable
alternatives in the same supplier reply demonstrate a price premium that exceeds
its buyer benefit. Only a fresh quote on the same complete structure permits a
bounded counter roughly one percent below that price. A stale historical quote
can protect the option but cannot cause a price concession. Buyer-value A,
must-haves, external cost ceiling, terminal handling and previous comparable
concessions remain protected. The learned selector and strategic planner cannot
silently discard or replace the protected option. There are no case IDs,
supplier IDs or historical record prices in the rule, and no retraining.

57 focused tests passed. Archived decision-prefix replay left the checked P2/P5
actions unchanged and changed the last open P11 decision to include EUR45.05 at
1500 ppm and zero complaint charges. Replay does not predict supplier acceptance.

One authorized hosted P11 attempt then selected that actual option:

- Session `6ff8181a488575b8`, run `6ceb7ea46f204766bf78a33b2cbfe951`.
- Score **69.4845**, previous personal best **61.9591**, gain **7.5254 points**.
- EUR45.05 instead of EUR45.50, with the remaining final terms unchanged.
  Buyer cost EUR751,513.50, a reduction of EUR7,155 against that earlier deal.
- Existing progress model SHA256
  `660351c1b7f6f6a25c9bbf5c08efcead84442533743629ba844917090a243447`.
- Four Claude calls, 25,577 input / 390 output tokens, 32.58 seconds,
  no fallbacks, provider errors or transport recovery events.
- Practice quota224 to225; one exclusive creation marker and one new session.

This is a new personal record, not a verified overall competition record or a
causal estimate from repeated paired trials. No second attempt was started.
Artifacts: `data/local/practice-p11-observed-v1/`, including source/model snapshot,
preflight replay, full transcript, result, quota audit and HTML report.

## P11 follow-up: two-percent counter

Revision `3ac04bb` changes only the observed-quote discount request from one to
two percent. Nine targeted tests passed; local comparison against the frozen
previous rule changed only the last open P11 decision (EUR45.05 to EUR44.59).
Earlier P11 and checked P2/P5 decisions were unchanged. No retraining occurred.

Exactly one hosted attempt, session `18888ab201021651`, run
`9058e21bc3884b17b264836c4018354f`: the EUR44.59 proposal was sent, but the supplier
countered with a final EUR45.00 offer at1500ppm and zero complaint charges. The
agent accepted. Score **70.3207**, up **0.8362 points** from69.4845, a new personal
record. All other final terms match the prior deal; buyer cost fell another
EUR795 to EUR750,718.50. The supplier's claim that EUR45.00 is its bottom line is
an observed statement, not proof of an absolute reservation price.

Four Claude calls,25,577input/337output tokens,39.38seconds, no fallbacks or
provider errors. Quota226 to227 used; one creation request and one new session.
No P10 attempt or further P11 attempt was started. Artifacts and HTML report:
`data/local/practice-p11-observed-v2/`. The smaller marginal gain and final-offer
response support prioritizing the P10 price/delivery trade next, rather than
repeatedly increasing this discount request.

## P10: complete-cost delivery response

Revision `789ff5f` adds a response to actual supplier service packages for
secure-supply cases. A faster option is not automatically preferred: both current
quotes must cover full demand, and the slower complete package must cost less
after delay and other buyer costs. The policy then preserves a bounded counter
on the cheaper schedule, optionally with a longer commitment already admitted
in its existing portfolio. The requested discount is two percent, with prior
concessions, valid terms, full-horizon coverage and buyer ceiling respected.
It does not infer that a bundled price difference measures delivery alone.

48 focused tests passed. Archived replay changed P10's second message to include
EUR31.16, full volume,36months,56-day lead time. The opening and checked P2/P5/P11
decisions remained unchanged. No training or model change occurred; the trial
used the existing progress selector rather than the earlier P10 value selector.

Exactly one hosted attempt on session `7e1dfe42025de534`, run
`7be56e84c932463d9bce8584a74f80df`, produced **67.0711**, below the standing personal
record **70.2080** by **3.1370 points**. The new package was actually sent. Supplier
countered at EUR31.60,23,400units/year,36months,56days and declared a final offer;
the agent accepted. Buyer cost EUR1,527,674.40, including EUR33,600 delay cost.
The previous progress-selector run scored56.2462 and the value-selector run
64.7294, but these are unpaired observations and do not establish causal uplift.

Two Claude calls,11,950input/172output tokens,40.97seconds, no errors or fallbacks.
Quota227 to228 used; one exclusive creation marker and one new session in audit.
No further attempt was made. Artifacts: `data/local/practice-p10-delivery-v1/`.
The result confirms use of the intended trade but does not establish superior
overall policy performance or eliminate delivery penalties. Keep the historical
record baseline; do not label this revision a record-beating P10 solution.

## P1 renewal-focus experiment

The user reported another team's1.0 score; their deal terms and transcript were
not available or independently verified. Our own previous runs switched between
November, January and April dates and sometimes received an expensive April
primary quote with a cheaper alternative that could not be executed.

Revision `5d63aa9` adds an isolated `RenewalFocusedNegotiator`, not registered in
the production factory. One explicitly referenced supplier contract determines
the earliest permitted renewal date after expiry. Outgoing portfolios focus on
that date; if it disappears from the ordinary portfolio, a bounded cost-improving
counter to a live quote can preserve it. No case/supplier ID or winning price is
hard-coded. No model training occurred. Crucially, the original term options and
cost horizon remain unchanged: restricting the case options themselves would
incorrectly change the published cost function's horizon start.

13 tests passed. Replay kept the checked P1 proposals on January1 and left the
checked P2/P5/P11 decisions unchanged. One hosted practice attempt followed:

- Session `f27ff9824bd334a2`, run `35240d8857984c0cafdd966c14167a90`.
- New personal best **73.8751**, previously70.5890: **+3.2862 points**.
- Deal EUR134.20,14-day payment, January1,2027; remaining terms unchanged from
  the prior personal-best EUR134.50 deal. Total cost EUR1,111,697.53, downEUR1,871.93.
- Five Claude calls,28,230input/443output tokens,154.69seconds; no provider errors,
  fallbacks or transport recovery events. Quota228 to229, one new session.

The actual final counter was EUR133.55 on January terms; supplier responded with
a final EUR134.20 offer, which was accepted. Score1.0 was not reached. The remaining
reported benchmark cost gap is EUR14,881.75; it is not a prediction of attainable
savings. This single exploratory result does not isolate causality or generalize
to all renewal negotiations. Artifacts: `data/local/practice-p01-renewal-focus-v1/`.

## P4 intermediate-payment experiment: failed

Revision `4a76f72` adds an isolated `FinancingBridgeNegotiator`, with no production
factory registration. Comparable same-message cash/credit quotes support a request
for an intermediate legal payment term and next longer commitment. The proposed
complete cost must improve on the quoted packages by at least the saving from a
one-cent unit-price reduction. It preserves prior concessions, coverage and A;
no challenge IDs, historical winning prices or benchmark targets enter the rule.

38 focused tests passed. Replay proposed EUR4.88,45-day payment,36months after
the supplier's EUR4.87/14-day and EUR4.94/90-day quotes, and preserved checked
P2/P5/P11 decisions. No model training or P18 work was performed.

One hosted attempt, session `f9529f49198d3481`, run
`949fbdec9ddf40d3bd09ca11781df06b`, ended **without a deal: score0**. The existing
personal best **86.0445** remained unchanged. The new EUR4.88/45-day package was
sent, alongside A and an extra planner-generated EUR4.80/60-day/36-month option.
The supplier countered at EUR4.90,45days,36months with status open and a statement
that this was its best price for that structure. Our next message repeated
EUR4.88 alongside aggressive90-day options atEUR4.14 andEUR4.30. The supplier
then withdrew. There was no transport failure or invalid acceptance attempt.

The experimental proposal therefore did not secure the hoped-for saving, and
the continuation policy failed to secure the intermediate counteroffer. Its
presence would not have beaten the historical record anyway. The extra planner
option also limits causal attribution to the intended payment exchange alone.
Do not promote this variant or call it a score improvement. It remains isolated.

Three Claude calls,15,921input/355output tokens,35.28seconds; zero errors/fallbacks.
Quota229 to230 used, one exclusive creation marker and one new session. No repeat
attempt was made. Full failed transcript, model/source snapshot, preflight and
HTML report: `data/local/practice-p04-financing-v1/`.

## Cross-track archive review and observed settlement control

Reviewed the locally available archive after the failed P4 financing attempt:
245 distinct sessions, with 174 duplicate files removed. This is an inventory of
mixed historical policies, not 245 independent challenges or a fresh evaluation.
P18 remains the teammate's track and is excluded from policy replays. Outside
P18, 616 eligible response examples form only 11 connected case/supplier groups;
just 3 eligible replies are walkaways. This is insufficient evidence to claim a
well-calibrated learned exit probability. No new training or hosted calls occurred.

Two implementation changes:

- `settlement.py` can accept the sole live supplier's primary quote after two
  exchanges when it matches the latest buyer proposal on every non-price term,
  improves on the opening, passes the existing cost/alternative checks, and is
  within 0.5% of the buyer's last proposed complete cost. Any positive premium
  additionally requires explicit closing resistance. Both modeled complete cost
  and public direct cost must pass. General price freezes and conditional refusals
  do not qualify. Neither a cheap generated anchor nor an LLM menu can override
  this observed closing decision. The 0.5% threshold is a policy assumption,
  not a learned acceptance threshold or supplier floor.
- `responsive_packages` no longer replaces a higher currently planned price
  with a lower quarter-gap bid on the same structure. The previous comparison
  protected indexed offers only, despite its general no-reset comment. Existing
  validity, coverage, ceiling and past-concession checks still apply. This fixes
  inconsistent controller scheduling; a higher bid is not inherently a better
  scored deal.

Local validation against baseline `c596a42`:

- 484 open historical prefixes across 167 single-supplier sessions replayed without
  errors. 27 multi-supplier sessions were excluded from this broad replay because
  their chronological interleaving cannot be inferred from separate threads.
- The settlement rule triggered once: the latest P4 EUR 4.90/45-day/36-month quote,
  immediately before the real run lost the supplier. Its public complete cost is
  EUR 197,223.33 versus EUR 196,420.67 for our EUR 4.88 bid. Acceptance was not actually
  tested, and that quote would still be worse than the existing EUR 196,852.67
  record deal. Do not describe this safeguard as a new record.
- The reciprocity correction changed 8 historical controller inputs: 4 on P3, 3 on P1,
  1 on an older P2 run. Replay diagnoses behavior; it does not measure resulting
  supplier responses or score improvement.
- 17 reconstructed decision prefixes from the P1/P2/P5/P11 personal-best runs
  produced identical deterministic base-controller before/after actions. This
  includes the actual multi-supplier ordering recorded in the P5 decisions.
  The language model was not rerun, so this is not a live non-regression guarantee.
- 72 focused tests passed. Expanding to the strategy integration tests yielded
  89 passed and one existing S03 score assertion failure: 0.977067 versus 1.0.
  Isolating both changes and restoring both baseline behaviors reproduced that
  exact same failure; the expected score was not weakened to make it pass.

For the next record-seeking experiment, prioritize the **P3 payment exchange**.
Its archived best is 86.2062 with EUR 431.61/60 days; EUR 428.40/14 days scores 83.0349
despite the lower unit price. Current proposals should therefore price a legal
payment extension from observed comparable quotes instead of repeating an
unsupported price discount. The observed 30-to-60-day pair EUR 430.15â†’431.61 gives
an illustrative 90-day price of EUR 433.07 under a linear extrapolation. That would
reduce published direct cost by EUR 13,072.54 versus EUR 431.61/60 days, but neither
acceptance nor relationship cost at those new terms is established. This is a
candidate for a bounded experiment, not a forecast or an implemented pricing rule.
Preserve P2/P5 baselines and leave P18 untouched.

Detailed local artifacts: `data/local/all-runs-review-v1/` contains the manifest,
aggregate audit, prefix replays, record-path checks, data-quality counts and HTML
report. Archived outcomes are used only after replay decisions for comparison;
the runtime policy does not consume session results, benchmark costs or case IDs.

## P3 compensated payment-extension experiment

`PaymentExtensionNegotiator` is an explicit experimental subclass, not registered
in the production factory. A comparable current cash/credit quote pair supplies
a price-per-day secant. It compensates the next permitted extension by at most
30 days and twice the observed span, rounded upward to cents. No case ID, winning
price or benchmark cost enters the policy. The original case/horizon is unchanged.

Admission requires lower published direct cost, lower complete cost across nine
quote-implied financing/production-cost sensitivities, and no more than 0.25%
downside under the existing point model; the alternative ceiling still applies.
It preserves prior comparable concessions and terminal/closing handling. Once
admitted, the experiment sends only A and the protected exchange, so an extra
planner proposal cannot silently introduce an executable fallback. It relies on
comparable quote terms rather than a particular supplier's wording.

38 focused tests passed before the wording-independent refinement; its seven
module tests passed again afterward. Full planner/learned-selector replay on the
archived P3 prefix emitted EUR 433.07/90 days from the EUR 430.15/30-day and
EUR 431.61/60-day pair, alongside A. Opening behavior had no experimental probe.
Model weights are unchanged. The user authorized one hosted P3 attempt.

The attempt at revision `6d5b2bc` completed with a deal but no record: **83.0077**
versus the existing **86.2062** (-3.1986 points). Session `e1ee605771664ac7`, run
`f54b93a4ebb44f628ddc08cc6c2646bf`. Final terms: EUR 428.41, 14-day payment,
24 months, 4,580 units/year; total cost EUR 3,966,353.28. Must-haves passed.

The experimental rule activated **zero times**. Both opening packages requested
14-day payment. The supplier quoted only that structure after opening and held
it through the next two replies. Later buyer menus requested longer payment
while also demanding a discount, without eliciting comparable priced alternatives.
In the fourth message, A offered the standing EUR 428.41 cash price for 24 months;
the supplier accepted it before the payment exchange could develop. This does
not test acceptance of the locally proposed compensated 90-day offer.

Before another hosted attempt, develop and locally check an early quote-discovery
step: request comparable prices on payment alternatives without also asking for
a price reduction or supplying an immediately acceptable cash fallback. Do not
promote the current experimental variant on this result. No additional attempt
was made. Four Claude calls, 22,710 input/327 output tokens, 42.28 seconds; zero
errors/fallbacks. Practice usage 237â†’238, with exactly one new P3 session in the
session audit. Artifacts: `data/local/practice-p03-payment-extension-v1/`.

## P3 record campaign: 90-day opening focus succeeded

The user authorized up to ten attempts. The campaign counted the just-completed
83.0077 trial as attempt one, leaving at most nine additional sessions. Attempt
two, revision `24e46c7`, achieved a new personal best **89.9834**, up from
**86.2062** (+3.7772 points). The campaign stopped with eight attempts unused.

`PaymentFocusedNegotiator` is another explicit opt-in subclass; no production
factory or other track variant was changed. It requests the longest legal
payment period from the opening, retaining full quantity/horizon coverage and
the original case options. Its experimental opening envelope is the current
quote/alternative complete-cost ceiling rather than the old cash-anchor envelope.
If ordinary packages cannot remain below that ceiling, it can make a bounded
counter on a current quote already using the target payment. It preserves prior
concessions and final offers; the planner cannot add a cash fallback. No case ID,
known winning price, hidden benchmark cost or changed model weight enters it.

Six focused tests and an opening planner/selector replay passed. The live opening
offered EUR 422.66/90 days/36 months and EUR 415.13/90 days/24 months. The supplier
countered at EUR 433.08/90 days/36 months. After one EUR 430.91 counter, the supplier
repeated EUR 433.08; the controller accepted the current quote. Final full cost
**EUR 3,942,826.22**, versus EUR 3,955,565.46 for the old record: **EUR 12,739.24**
saved. The higher unit price is outweighed by the financing benefit. All must-haves
passed; scoring horizon and required annual quantity were unchanged.

Session `088a1043abce3a20`, run `7554585a35184a4982de6fb5256a1702`. The server's
`best_after` confirmed the new personal record. Two Claude calls, 10,045 input /
155 output tokens, 48.92 seconds, no errors or fallbacks. Practice usage 240â†’241;
the audit shows exactly one new P3 session for this trial. Other team usage between
trials is not counted as our campaign attempts. Across the two campaign trials:
six calls, 32,755 input / 482 output tokens. No third attempt was started.

Artifacts: `data/local/practice-p03-payment-focus-v1/`; campaign ledger and winning
profile: `data/local/p03-record-campaign.json`. This is one winning observation,
not evidence of repeatability or a verified overall hackathon record. Keep the
variant opt-in and retain the other track baselines.

## First ten-attempt campaign completed; additional budget authorized

The resumed first campaign used its remaining eight attempts. None exceeded its
prior account best. All eight completed with valid deals, zero provider errors
and zero fallbacks: 24 Claude calls, 123,265 input and 1,908 output tokens.

| Attempt | Track / isolated experiment | Score | Best before |
| --- | --- | ---: | ---: |
| 3 | P11, quality/liability focus from opening | 61.9591 | 70.3207 |
| 4 | P10, delivery focus and half-gap response | 64.7294 | 70.2080 |
| 5 | P11, same focus and half-gap response | 61.9591 | 70.3207 |
| 6 | P10, public current-price opening floor | 67.0711 | 70.2080 |
| 7 | P1, April price start / 14-day payment | 58.4688 | 73.8751 |
| 8 | P7, 36-month / 90-day focus | 72.7504 | 72.7504 |
| 9 | P3, 36-month / 90-day focus and half-gap response | 89.9834 | 89.9834 |
| 10 | P8, 36-month indexed focus | 49.4670 | 62.4556 |

P11 held EUR 45.50 through four further counters. P7 followed the same price
sequence despite greater buyer concessions and an extra contract year. P3 again
quoted EUR 433.08 and repeated it after EUR 427.87, yielding the same 89.9834.
P1's delayed increase required EUR 139.30, offsetting the delay's financing benefit.
P10's less aggressive opening avoided an immediate final offer, but did not beat
its established record. These adaptive single runs cannot establish causality.

`StructureFocusedNegotiator` remains opt-in; do not replace the successful
profiles with it. It checks legal complete terms and live-quote/alternative cost
ceilings, preserves prior comparable concessions, and discards stale scenario
metadata after changing commercial terms. Optional reciprocal movement uses only
live comparable quotes; its one-half gap is a policy setting, not an inferred
acceptance threshold. Revisions: `100248b`, `707421b`, `c2d62e6`.
23 focused regression tests passed; Ruff passed. Results and audit:
`data/local/record-campaign-continuation/report.html` and `report.json`.

The user subsequently authorized **ten additional attempts**, extending the
overall ledger cap from ten to twenty. The next experiments preserve successful
openings and test a single small closing counter only while the quote remains
open. `closing_probe.py` never reopens a final offer, honors explicit final-offer
wording/deadlines and previous concessions, and allows only one use per supplier.
It requires lower direct cost and caps modeled downside at 0.25%; a standing
offer can still be lost. This is explicit experimental risk, not a guaranteed
improvement. The production factory and P18 remain untouched.

## Second ten-attempt campaign: no new personal records

The second explicit authorization was completed: attempts 11-20, exactly ten
new sessions. No record improved. P3 and P4 matched their bests; all other
results were lower. One P1 session ended by timeout during an arena connection
failure and was recovered read-only, without creating a replacement session.

| Additional attempt | Track / experiment | Score | Best before |
| --- | --- | ---: | ---: |
| 1 | P03 / p03-closing-probe-v1 | 89.9834 | 89.9834 |
| 2 | P05 / p05-payment-focus-v1 | 88.2121 | 92.3787 |
| 3 | P04 / p04-memory-opening-v1 | 86.0445 | 86.0445 |
| 4 | P10 / p10-memory-opening-v1 | 65.5247 | 70.2080 |
| 5 | P01 / p01-memory-opening-v1 | 0.0000 | 73.8751 |
| 6 | P10 / p10-memory-evidence-v1 | 57.0268 | 70.2080 |
| 7 | P05 / p05-memory-opening-v1 | 77.2277 | 92.3787 |
| 8 | P08 / p08-memory-followthrough-v1 | 27.8194 | 62.4556 |
| 9 | P11 / p11-memory-followthrough-v1 | 61.9591 | 70.3207 |
| 10 | P10 / p10-memory-followthrough-v1 | 67.8664 | 70.2080 |

The one-time P3 EUR 432.00 closing counter actually activated; the supplier
repeated EUR 433.08. P4 held EUR 4.94 despite the one-cent lower opening.
P5 confirms that reusing a final deal as an opening is not equivalent to the
competitive negotiation that produced it. P8 also gave much weaker concessions
from the higher initial anchor and small subsequent price steps. Retain the
previous winning policies; do not promote these experimental profiles.

`MemoryOpeningNegotiator` retrieves a prior successful complete agreement only
when supplier, parts, buyer economics, fixed terms and proposal options match.
It does not fit new model weights or use hidden benchmark costs. A prior
agreement alone is not proof of present acceptability. The implementation and
these known-track experiments do not establish unseen-agent improvement.

The API permits supplier standard values outside the suggested options. P10
had previously agreed 54-day delivery, but the original grid-only validation
discarded it. The memory experiment now permits this lead-time exception only
when the same value occurs in BOTH the confirmed historical deal and the current
structured supplier quote. All cost calculations retain the unchanged original
case; buyer must-haves still apply. No arbitrary out-of-grid terms are admitted.

The opening-only wrapper then exposed another limitation: returning to the
normal planner immediately could discard the offered commercial structure.
In the P10 evidence trial the arena verified the EUR 24.31 buyer estimate, but
the next menu switched to 42-day delivery and induced EUR 33.60, scoring 57.03.
An explicit bounded follow-through option keeps the structure for at most two
small price concessions, respects current quotes and final-offer language, and
caps its price at the prior agreed price. The last trial held 54 days and ended
at EUR 31.60, scoring 67.8664: better than these earlier variants, still below
the 70.2080 record. The last trial made zero client Claude calls; the hosted
supplier still ran normally. Do not call this a record or a controlled uplift.

P1 session `90743291b13fee91` ended by timeout, score 0. The original failure and
server result are both preserved in its experiment folder. Exclude this final
outcome from supplier-rejection training labels. The campaign runner now uses
shorter connect and GET timeouts while retaining long supplier-response reads.

Across the additional ten: 22 Claude calls, 126,062 input / 2,008 output tokens,
zero provider errors/fallbacks, plus the separately recorded arena transport
failure. Shared team quota changes are not treated as our attempt count.
38 focused regression tests passed and Ruff passed. All new policies remain
opt-in; production factory, prior winning profiles, P2 and P18 were untouched.

Artifacts: `data/local/record-campaign-round2/report.html`, `report.json`, and
`data/local/p03-record-campaign.json`. All twenty campaign attempts are consumed;
no session remains active and no additional session should be created without
a new user authorization.
