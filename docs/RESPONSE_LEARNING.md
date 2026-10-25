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

## Authorized hosted test: one attempt each P1–P5

On 2026-10-25 the user requested one run on tracks 1–5. An isolated runner used
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
P18; P06 and P09 are not hosted. An initial P06–P10 preflight stopped before any
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
