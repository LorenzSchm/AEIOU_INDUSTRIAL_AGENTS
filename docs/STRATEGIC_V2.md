# Strategic controller upgrade

Use `strategic-policy` for the complete local controller without model calls, or
`strategic` for the same controller with a Claude communication writer. Existing
`adaptive`, `claude` and baseline agents retain their behavior. The default agent
is unchanged; choose the new agent explicitly in the UI or CLI.

## Implemented behavior

1. Adaptive bidding uses observed price movement, resistance and remaining turns
   to resolve full packages. Published buyer goals and observed behavior select
   balanced, continuity, liquidity, term-exchange or closing strategies.
2. A Bayesian ensemble updates six assumed supplier preference types after
   identifiable package responses. Counteroffers provide tempered evidence;
   ambiguous responses and repeated status messages do not add observations.
   The posterior affects package selection after two observations.
3. Bounded two-step lookahead compares acceptance with hypothetical responses
   across preference types and observed price-step scenarios. A 100 ms budget
   limits planning. These are uncalibrated assumptions, not a supplier digital twin.
   Independent guards preserve materially better legal counters, other suppliers'
   continuation opportunities, and terminal final offers.
4. The controller selects the supplier, action and executable package IDs. Claude
   selects tone, diagnostic question and approved evidence IDs. Free-form factual
   prose does not reach suppliers in grounded mode. Exact contract summaries come
   from code. Invalid model output falls back to a deterministic communication act.
   The deterministic writer introduces one fresh approved source at opening or
   when the observed price stalls or worsens. It preserves evidence while price
   concessions continue, avoids repeated source claims, and explains that one
   market index does not establish a supplier's total costs.
5. Local hybrid retrieval combines BM25 with explicit business-concept matching,
   supplier/part/date filters, source IDs and exact-content chunk hashes. Optional
   supplied dense embeddings are supported; no embedding service is called.
   The default is not a learned vector model or hosted vector database.
   Internal notes remain private; only complete approved letter excerpts and
   computed index claims may appear in supplier-facing text. The data cutoff
   defaults to the competition's 30 September 2026 world.
6. The research UI answers focused questions from the supplied competition data.
   It exposes uncertainty and unanswered questions. No live market scraping or
   real supplier contact occurs.
7. Offline writer optimization separates train, validation and test base cases.
   Reflection sees training traces only; validation selects one instruction;
   the held-out test runs once. Instructions are text, never executable code.
   An explicit environment setting loads a selected hash-checked result.
8. Evaluation supports feature ablations, repeated runs, multiple language supplier
   models, case-cluster bootstrap intervals, score tails, latency, tokens, failures
   and fallbacks. A supplier failure excludes the entire paired condition from
   score comparison; resource totals still include excluded attempts. Buyer
   failures count as zero. There is no best-of-attempt selection.

## Local commands

```powershell
aeiou play case:S03 --mode local --agent strategic-policy
aeiou research case:S01 "What supports a cash-for-price exchange?"
aeiou evaluate --agents adaptive strategic-policy --seeds 99 100 101 --max-sessions 180
aeiou evaluate --agents adaptive strategic-policy --seeds 102 --ablations --max-sessions 240
aeiou evaluate-retrieval tests/fixtures/retrieval-gold.json
aeiou optimize-writer --agent strategic-policy --rounds 0
```

These commands use zero Arena sessions. The first five use no model API. The
last command exercises the split/report pipeline without reflection. Model-backed
evaluation and reflection require `--allow-api`; session counts are checked before
execution. Session limits do not represent a monetary spend limit.

With configured Anthropic credentials, explicitly requested paid experiments can
use `--agents adaptive strategic --allow-api`, `--supplier language`, a fixed
`--buyer-model` and one or more `--supplier-models`. Writer optimization with
reflection uses `aeiou optimize-writer --rounds 2 --allow-api`. To apply a selected
result deliberately:

```powershell
$env:AEIOU_WRITER_INSTRUCTION_FILE = "absolute/path/to/prompt-optimization/report.json"
```

Search is `GET /api/research/{case_id}/search?q=...`. Saved experiments are
`GET /api/evaluations`; the evaluation UI loads these without starting runs.
Default experiment reports and full run traces live in ignored `data/local/`.
`--output` can choose another local report directory.

## Evidence and limits

The first 120-session comparison exposed premature acceptance in S02:
adaptive 96.44 vs strategic 91.43. A feature-ablation run isolated lookahead.
An intermediate correction also intercepted terminal offers and regressed further;
the final guard explicitly preserves terminal handling. Both issues have regression tests.

The corrected implementation matched adaptive exactly across 90 paired conditions
(five cases, six profiles, three seeds; 180 sessions): **97.4644/100 each**, zero
failures and zero must-have violations. A separate seed-102 ablation matrix ran
240 sessions; all eight arms tied at **95.1015/100**. This does not establish a
score improvement, causal feature value, or generalization beyond these cases.
These cases were used during development and are not an untouched final test set.

An additional comparison on seeds 103 and 104 ran 120 sessions after the fixes:
both agents scored **99.9669/100**, with no failures or must-have violations.
These are new seeds on the same five cases, not new independent base cases.

### Follow-up: measured evidence-feedback improvement

After those ties, the evidence-feedback rule above improved the development
comparison on seeds 99/100 from **96.4362 to 97.3139/100**. A preselected follow-up
used seeds 105–114, which had not been used to develop that rule. It ran 900 local
sessions: adaptive, strategic-policy and strategic-policy without grounded language
on all five cases and all six supplier profiles.

| Metric | Adaptive | Strategic with evidence feedback |
| --- | ---: | ---: |
| Mean score | 97.5996 | 98.2108 |
| 10th percentile | 90.3924 | 91.2316 |
| Failures / must-have violations | 0 / 0 | 0 / 0 |
| Decision p95 | 0.016 s | 0.047 s |

The new policy won 24, tied 276 and lost zero paired conditions. The mean gain was
**0.6112 points**; the case-cluster bootstrap interval was **+0.2212 to +0.9258
points**, across only five base cases. Removing grounded language and its evidence
feedback returned exactly to adaptive's score. The gain came from the simulated
index-skeptical profile, whose response rule rewards novel verified evidence.
That profile-specific mechanism is a material limit; this is not proof of better
results against human suppliers, Claude suppliers or hidden competition cases.

Full condition-level evidence is in `docs/evaluation/evidence-feedback-new-seeds.json`;
the development comparison and earlier equal-score results are retained alongside it.

Claude writer quality, dense retrieval lift, live prompt optimization and
cross-model negotiation performance have not been measured in this upgrade.
The four-query retrieval fixture is a smoke benchmark, not a representative
search-quality study. A five-case dataset gives weak generalization evidence;
additional unseen supplier cases and multiple models are the next validation step.

The architecture follows the separation of bidding, opponent modeling and
acceptance discussed in automated negotiation research. It is an engineering
implementation, not a reproduction of a paper's reported performance.

### Hosted sandbox feedback: D02 and P18

Public supplier feedback now produces a legally valid counteroffer package with
price compensation that protects the buyer's target cost. Indexed counteroffers
remain available across rounds. The controller relaxes unnecessarily fast delivery
when the buyer's deadline and cost model permit it. Grounded communication avoids
reusing cited sources and repeating unanswered cost-breakdown questions.

Exactly two hosted sandbox D02 sessions were run: one before and one after these
changes. Score rose from **25.99 to 47.05/100**, unit price fell from **EUR 16.40 to
15.40**, and evaluated total cost fell from **EUR 199,339.73 to 187,217.07**.
Both deals met mandatory requirements. Practice quota remained **172**; neither
sandbox session consumed a Practice attempt. Combined buyer usage was 13 Claude
calls, 141,579 input tokens and 875 output tokens, with no errors or fallbacks.

This is one observed before/after comparison, not statistical or causal proof:
supplier responses vary. P18's index behavior was checked using public-message
replays, without another Practice attempt. The final index-retention adjustment
was replay-tested separately and does not affect D02's non-indexed schema.
The Python suite passed 117 tests (2 skipped); the subsequent index-retention
check passed all 5 feedback tests. All 5 JavaScript tests passed.
Results and run identifiers are in `docs/evaluation/sandbox-feedback.json`.

### Conditional single-term exchanges

The planner now reserves package B for a single non-price change paired with a
compensating price reduction, while preserving protected package A and the optional
bundled counteroffer C. It considers payment, duration, effective date and Incoterm
options only when legal and economically compensable. Total modeled buyer cost must
not exceed A; the buyer's existing concession schedule and terminal acceptance
policy remain separate from these additional experiments.

Public replies matching exactly one proposed structure update an exchange history.
Only contrasts differing in one non-price term count; ambiguous or bundled replies
are excluded. Observed price improvement ranks future experiments. A value with two
consecutive matched responses without price improvement is retired from this
single-term experiment selector. Observations are reconstructed per supplier from
history and include message indices; no hidden floors or post-run scores are used.
The association is not a causal estimate or calibrated acceptance probability.

Grounded messages make both changes conditional on the complete package. Local
regression tests cover D01 opening decisions, compensated costs, selection, ambiguous
responses, learning and retirement, alongside the existing P18/D02 replays. No new
hosted evaluation has been run for this change; the earlier 68.89 D01 and 47.05 D02
scores describe the previous version, not measured performance of this addition.

### Research-driven controller revision

Comparable offers now preserve previous price concessions across portfolio changes.
Explicit price resistance prompts a bounded 20% move across the remaining gap to
the public standing price. This is a heuristic, not a learned supplier floor.
Package compensation searches both price directions and accounts for the U-shaped
relationship penalty. Exploration stays within 80–120% of the base price and does
not shorten coverage below the buyer's horizon. These bounds are plausibility
heuristics, not estimates of supplier acceptance.

After two rounds, observed resistance can enable a settlement alternative based on
the supplier's current terms. It must improve the standing quote and respect the
global cost ceiling; it need not equal the ambitious base package's cost. Later
effective dates are considered. Index-counter alternatives remain protected.

Public language signals are recorded with exact evidence spans. Claude can add
bounded interpretations before planning (at most two per supplier, one per turn,
12-second timeout); unknown kinds and invented quotations are discarded. These
remain interpretations, never confirmed costs or executable actions. Local rules
remain available on provider failure. Liquidity affects strategy selection and
resistance affects bidding. Messages address resistance, authority and settlement.

RFQ conversations keep open suppliers in play before closing terminal threads,
use standing cost to break scheduling ties and can cite real competitive standing
offers with their conditions. Rival references are used once per recipient.
Important rival/contract evidence is not withheld just because prices are falling.
Missing supplier-cost estimates prompt one initial breakdown request in strategic
categories. The lookahead now uses the same supplier estimate as package pricing;
its response probabilities remain explicitly uncalibrated assumptions.

Validation before hosted evaluation: 125 tests passed, 2 skipped; all five targeted
revision tests passed after adding the interpretation-cache regression. No hidden
floors or post-run reference scores are inputs to the agent.

Hosted comparison (fixed agent commit `bfd470c`, two sessions per case):

| Case | Reference scores | New scores | New mean | Mean change |
| --- | --- | --- | --- | --- |
| D01 | 68.89 / 68.89 | 77.20 / 92.07 | 84.63 | +15.74 points |
| D02 | 47.05 / 47.05 | 44.94 / 0.00 | 22.47 | -24.58 points |

D01 obtained an April price start in both runs; the second also closed at EUR 50.20.
D02 repetition 2 suffered a local Windows file-lock failure while recording a move,
then timed out on the server. Its real zero score is included. An earlier HTTP 429
rejected its start before a session existed; it was retried after capacity freed.
Exactly four new sessions were created, and Practice quota stayed at 172. Recorded
buyer usage was 34 model calls, 254,385 input tokens and 2,878 output tokens; these
input counters exclude separately reported provider cache-token fields.

This is a mixed result, not a general improvement claim or an isolated LLM variance
measurement. Multiple changes are bundled and there are only two runs per case.
The complete comparison reports are `docs/evaluation/controller-revision-D01.json`
and `docs/evaluation/controller-revision-D02.json`.

After the hosted runs, local fixes added bounded file-replacement retries and a
readable recovery snapshot when Windows locks the primary run file. A regression
also prevents generic delivery probes from reintroducing unnecessarily fast terms.
These two follow-ups have local validation only; no additional paid runs were made.
Final validation: 129 Python tests passed, 2 skipped; all 6 JavaScript tests passed.
