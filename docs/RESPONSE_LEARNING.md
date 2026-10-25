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
