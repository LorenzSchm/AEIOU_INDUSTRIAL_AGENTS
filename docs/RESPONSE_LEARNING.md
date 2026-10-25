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
