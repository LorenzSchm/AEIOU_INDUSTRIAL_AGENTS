# AEIOU — Industrial negotiation intelligence

A Claude-backed buyer agent for Tacto Negotiation Arena, with a responsive dashboard, supplier
research, numerical package optimization, transcript replays and reproducible evaluations.

## Run

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12 (uv can install Python).

```bash
uv sync --dev
# Fresh checkout only; keep your existing .env:
cp .env.example .env
# Set ANTHROPIC_API_KEY and ARENA_API_KEY in .env
uv run aeiou serve
```

Open **http://127.0.0.1:8765**. Pages cover overview, a live deal pipeline, negotiation/replay,
evidence, and a simple prompt evaluation suite. The buyer model can be switched in
the sidebar (stored in `data/local/settings.json`); "Live demo" paces a local run so the pipeline
visibly moves. All values come from recorded runs and reports.
The adaptive policy needs no API key. Claude calls use Anthropic credits; hosted practice also
uses arena quota. Page load does not start paid work.

### Dashboard development

The dashboard is a React + Tailwind + [shadcn/ui](https://ui.shadcn.com) app in `frontend/`,
themed to match Tacto's product (Inter, orange `#ff6414`, Untitled-UI neutrals). Its production
build is committed to `src/aeiou/web/`, so `aeiou serve` and Docker need no Node. To change it
(Node 22+), keep `uv run aeiou serve` running and in a second terminal:

```bash
cd frontend
npm ci
npm run dev     # http://localhost:5173, proxies /api to :8765
npm run build   # writes src/aeiou/web/; commit the result
```

The UI update from `origin/feat/ui` (`e9735a9`) is integrated with local model-supplier evaluations,
walk-away diagnostics, and the versioned prompt comparison. Global "New negotiation"
actions default to local mode and the current user prompt; a specific hosted case can still be
selected explicitly. The original prompt archive and all benchmark records are preserved.
Integration verification passed **128 tests, 2 skipped**, TypeScript/Vite build, Python lint, and
a browser-launched paced local policy run through the pipeline and replay. That UI smoke test
used zero model calls and zero arena sessions. Frontend lint exits successfully with existing
React refresh/effect warnings in the imported UI.

Environment variables override `.env`. Defaults: `LLM_PROVIDER=anthropic`,
`LLM_MODEL=claude-sonnet-5-5`, `LLM_TIMEOUT=35`, `LLM_MAX_TOKENS=1800`. See
[.env.example](.env.example). Supplied credentials are configured locally, excluded from Git,
and never sent to the browser. Runtime transcripts are ignored under `data/local/`.

New Sonnet 5.5 runs use high effort and standard speed; Opus 5.5 uses medium effort
and fast mode. Each evaluation batch saves its settings when launched, so model
changes apply to new batches rather than changing an active comparison midway.
Hosted evaluations allow 50 seconds for a buyer decision, including any validation
repair. Each model call receives only the remaining budget; late actions are discarded.
This replaces the previous 35-second evaluation cutoff and leaves a nominal 10-second
margin for delivery within the arena's 60-second reply window. Network delays can still
cause timeouts; high effort is not a guarantee of meeting the deadline.

GPT-5.5 is available alongside Claude through the OpenAI Responses API. Add
`OPENAI_API_KEY` to the local `.env` file (never to the frontend or a prompt).
The dashboard detects a newly added OpenAI key without restarting. Each sandbox
prompt and the hosted practice form has an independent model and reasoning selector.
GPT-5.5 defaults to medium effort; low, high, extra high and none are also available.
The selected provider, model and effort are saved with the batch, and later setting
changes do not affect it. Historical scores retain their original model labels.

The OpenAI adapter streams internally under a total deadline, includes reasoning
tokens in its usage record, and interrupts an in-flight request when a batch stops.
Only a complete response reaches the existing negotiation controller. Refusals,
incomplete outputs, authentication errors and rate limits remain explicit failures;
there is no automatic provider fallback. Both providers receive the same saved strategy,
research and conversation format. The model determines the provider; `LLM_PROVIDER`
is not used for routing. Missing credentials block a batch before any arena session opens.

```bash
uv run aeiou doctor
uv run aeiou play case:P01 --mode hosted
# Hosted practice: at most two attempts, even when more quota remains.
uv run aeiou practice --prompt-file ./prompt.txt --max-sessions 2 --parallel 2
uv run aeiou play case:S01 --mode local --agent adaptive
uv run aeiou play case:T02 --mode local --agent claude --opponent cashflow
```

## Agent

New negotiations and the final runner default to **Your prompt · buyer walk-aways disabled** (`--agent original`). Hosted practice requires an explicit `--prompt-file` or `--eval-batch` to use the eval controller; legacy agents require an explicit `--agent`. Both the launch dialog and `play` default to the local simulator.
Hosted practice has a separate two-attempt default cap. The active source is `SYSTEM_PROMPT.md`.
At the user's request, revision `no-buyer-walkaway-v2` removes the stock-question sentence
and the preparation phrase asking for a walkaway price. Buyer walk-away actions are unavailable
and rejected before transport. Deadline/round-limit exhaustion is recorded as a protocol limit,
without sending a walk-away or accepting an ineligible offer. The remaining strategy is retained: the NegoMate preparation, verbatim three-offer request, third-offer selection and
five-repeat acceptance rule. The exact previous source remains in
`prompts/archive/SYSTEM_PROMPT.original.md`; evaluation reports retain the tested prompt and its hashes.
Historical scores predate this revision and do not establish its performance.

The three reviewed papers guide controller and evaluation design: the
[game-theoretic survey](https://arxiv.org/pdf/2601.15047) separates objectives, actions and information;
[device-native negotiation](https://arxiv.org/pdf/2601.00911) motivates validation and auditability;
[GameTalk](https://arxiv.org/pdf/2601.16276) motivates whole-conversation evaluation. Their reported
results do not establish performance for this prompt, and no Claude fine-tuning was performed.
The earlier `--agent claude` implementation remains available explicitly; its behavior is described below.

The original prompt's execution controller now:

- Blocks buyer-initiated walk-aways and records protocol-limit reasons, remaining legal actions,
  costs and repeat counts. Suppliers can still withdraw independently.
- Counts consecutive quotations of each complete package separately, regardless of menu
  ordering or changed IDs. Duplicate message IDs do not count as new confirmations.
- Enforces five identical quotations before acceptance of the selected package. With four
  messages left, it reserves the confirmation window when a feasible acceptable package exists.
  The model chooses the package; the controller repeats its exact terms while confirmation is
  needed. A changed or withdrawn package requires a new selection. Supplier cooperation is
  not guaranteed; the five-repeat rule has no deadline exception.
- Inserts the exact required text on the first outgoing email and restores line wrapping if the
  model repeats it. It does not prepend a stock question. Acceptance uses a verified
  live ID or an explicitly selected current menu position; there is no silent first-offer default.
- Gives failed actions one structured repair with legal actions, live offers, counts and
  consequences. Provider refusals and empty responses are reported separately from malformed JSON.

`faithful-v4-no-buyer-walkaway` labels new runs; `faithful-v2` identifies the earlier controller.
This evaluates executable strategies with their declared rules, rather than isolating prompt text.
Production and comparison AEIOU agents record decision diagnostics; historical runs retain their
original records and may have no walk-away reason available.

```bash
uv run aeiou play case:S01 --mode local --agent original
```

1. Retrieve supplier notes, letters, quotes, history, news, scorecards, cost drivers and should-cost.
   Compute index changes and normalize monetary price units; preserve citable IDs.
2. Search complete packages over legal terms. Price the fixed need and horizon, financing,
   freight, quality, lead time, index risk and estimated supplier sustainability using the kit's C.
3. Claude chooses arguments, diagnostic questions, reciprocal trades and structured offers.
   Code validates terms, IDs, must-haves and total cost. It blocks harmful proposals such as a
   discounted overbuy, as well as bad accepts. Provider failure uses the guarded policy.
4. Close a safe standing offer or use the alternative. Near the final-wave cap, bypass the model
   and close through the economic guard. Record emails, attachments, terms, costs, latency,
   model usage, provider errors and fallbacks.

[SYSTEM_PROMPT.md](SYSTEM_PROMPT.md) is preserved. The earlier production rewrite is in
[prompts.py](src/aeiou/prompts.py). It **changes** the original acceptance and package-request
tactics; it is retained as the comparison baseline. `--tactic voss` is another experimental
variant. Neither should be described as a faithful implementation of the original.

## Strategic agent: supplier learning and cost scenarios

The opt-in `strategic` agent adds an evidence-linked supplier state, comparable exploratory
packages and buyer-cost scenario planning. The original `claude` and `adaptive` agents remain
available unchanged for comparisons. Select **AEIOU Strategic** in the dashboard or run:

```bash
# Local policy, no model calls and no hosted arena sessions:
uv run aeiou play case:S03 --mode local --agent strategic-policy
uv run aeiou evaluate --agents adaptive strategic-policy --seeds 99 100 --cases case:S01 --opponents hardliner --max-sessions 4
# Claude writes the negotiation; Python controls the executable packages (API credits):
uv run aeiou play case:S03 --mode local --agent strategic
# Opt in explicitly for the final runner; does not change its default agent:
uv run aeiou final --agent strategic --parallel 10 --waves 5
```

Supplier state is reconstructed from the conversation, including resumed sessions. It records
which offered package a reply followed and links each tentative preference to the actual message
and offer ID. A repeated standard quote or several counteroffers does not establish a preference.
Price and multiple terms can confound a package choice; the UI explicitly shows that uncertainty.

Where legal and economically comparable, the planner preserves the buyer-value and early-payment
packages and uses a third slot to probe freight or duration. It calculates the published case cost
plus explicit stresses: freight +25%, buyer financing +4 percentage points, and fallback price
+10% with fallback risk +3 percentage points. The robust-cost recommendation minimizes worst
regret **among the offered packages**. These are cost sensitivities, not forecasts or calibrated
supplier-acceptance probabilities. The original case and scoring rates are never changed.

Claude selects current package IDs and must retain the buyer-value package. It cannot invent
contracts or independently increase the optimizer's concession schedule. Python adds the exact
offer terms to the outgoing message. Invalid selections and certain unsupported acknowledgements
trigger an audited fallback. These controls do not prove every sentence factually correct.

The replay displays preferences, source messages, scenario costs and guard rejections. Completed
runs expose `GET /api/runs/{run_id}/handoff`: a procurement-review JSON draft containing the
accepted terms, recorded cost comparison and source offer. Local runs are labeled simulations.
This creates no purchase order, legal approval or ERP write; operational approval remains required.

## Local prompt and model comparison

The older prompt comparison remains available through the CLI and its saved reports.
The dashboard now provides the hosted sandbox eval suite described below. Page loads never start tests.

```bash
# Two matched scenarios with an LLM supplier that responds to wording, four buyer sessions.
uv run aeiou compare-prompts --supplier language --label language-comparison
# Later: vary only the buyer model, holding the supplier model and seed fixed.
uv run aeiou compare-prompts --model YOUR_BUYER_MODEL --supplier language \
  --supplier-model claude-sonnet-5-5 --seed 71 --label model-comparison
```

These commands instantiate local simulators and cannot start hosted arena sessions. They use
Anthropic API credits. `--model` overrides the buyer for this experiment without editing `.env`;
`--supplier-model` selects the independent simulated seller. No other model is automatically tested.

The original strategy enters the system prompt byte for byte, followed by an API mapping shared
with the rewrite. Private preparation stays in buyer memory; reports retain section-presence
indicators, not private reasoning. The original's mandatory text is inserted by code; the surrounding
model email is preserved. Both variants receive
the same public research, cost calculations, candidate packages, model, token limit and transport.
The transport validates legal terms, buyer must-haves and acceptance against the stated alternative.
It does not force acceptance of final/agreed offers or replace the model with a fallback policy.
One format repair is allowed, then invalid output fails visibly. In protocol v2, the original's
ONLY IF restriction requires five identical quotations of the selected package, including a third
offer. Other alternatives changing does not reset that package's count. The earlier rewrite does
not acquire a five-repeat requirement.

Reports under `reports/prompt-comparisons/` archive both strategy texts and assembled system
prompts, hashes, fixed case/seed plans, model settings, every outcome, failures, repairs and token
usage. Buyer/transport failures count zero in the headline mean; a failed supplier simulation excludes both sides of its pair. This is a comparison under a shared harness,
not an exact replay of the earlier production guard and closing policy.

Scripted suppliers test numerical behavior, not language effects. The LLM supplier uses a separate
private seller persona and enforced economic floor, can quote up to three executable packages,
and sees neither buyer objectives nor private research. Its assumptions are synthetic, not Tacto's
hidden supplier prompts. Seeds fix economics; model outputs remain stochastic. These small local
screens do not establish a statistical winner or hosted performance. Local clocks are disabled;
decisions exceeding the hosted 60-second limit are explicitly counted.

For a completed hosted run, `uv run aeiou verify-replay RUN_ID` checks the recorded buyer requests
and supplier responses entirely offline, with zero model calls and zero arena sessions. The
`hosted_replay.public_setup` fixture preserves the exact public case and opening messages, including
binding offer IDs and captured attachment text, without later quotes or post-run results. A new
buyer message, changed wording, changed terms or changed offer ID stops replay: the recording
does not contain a counterfactual supplier response. This supports regression checks; it is not
a new performance benchmark. Replay does not reproduce wall-clock timing. Tacto's upstream kit
explicitly withholds the hosted supplier agents, so their private constraints and future reactions
cannot be copied from the public simulator. The public cost formula is shared, while private
supplier economics and the true attainable best cost remain unknown before a hosted result.

The 24 October follow-up used exactly one hosted P01 session with the original prompt, four
reliability fixes and Sonnet 5.5. It scored **0/100, no deal**. The supplier reached EUR 134.90
at 14-day payment, then withdrew on the third confirmation request: only three identical
binding packages had been observed, while acceptance required five. The buyer did not walk
away; one round remained, with no provider errors or fallbacks. The earlier legacy P01 attempt
scored 53.5, under a different closing policy. This single attempt does not compare planning methods
against the original. Quota moved from 26 used / 174 left to 27 used / 173 left. The paper review,
predeclared test plan, source hashes, result, withdrawal diagnosis and offline fixtures are saved
under `reports/research-and-hosted/20261024T132900-single-original/`.

The source comparison makes the earlier changes explicit. The stock-question row now reflects
the user-approved active revision; the saved scores below used its archived predecessor:

| Tactic / structure | Original | Earlier production rewrite |
| --- | --- | --- |
| Preparation | Nine analysis sections and six strategy entries | Implicit preparation; forbids preparation tags |
| Three-offer request | Mandatory exact message | Optional, with different wording |
| Third offer | Instructs acceptance of the third offer | Chooses the best safe standing package |
| Repeated stock question | Removed in the active revision; retained in the archived original | General diagnostic questions |
| Five identical offers | Required before acceptance | Explicitly subordinated to deadlines and safe acceptance |

Local screening on 24 October, using `claude-sonnet-5-5` for both buyers:

| Experiment | Paired scenarios scored | Original / 100 | Earlier rewrite / 100 |
| --- | ---: | ---: | ---: |
| Scripted supplier screen | 6 | 23.9 | 62.8 |
| First model-driven supplier screen | 1 (one pair excluded for supplier failure) | 95.1 | 72.3 |
| Repeated model-driven screen after clearer supplier repair feedback | 2 | 56.8 | 85.4 |

All attempts remain in the reports. The scripted rewrite had one buyer/transport failure, counted
zero. The repeated model-driven screen had no failed runs, no policy fallbacks and no buyer
must-have violations. The original still accepted before five repeats on one of those two runs;
both sent the requested message with changed line wrapping. Scores do not establish full tactic
compliance. No additional buyer models have been evaluated in this screen.

Subsequent protocol-v2 screens and model-compatibility probes are saved separately under
`reports/prompt-comparisons/` and `reports/model-compatibility/`. The first two-case faithful Sonnet
screen scored **77.3/100**, with two deals, exact required text in both runs, zero early acceptances,
six confirmation messages and no repairs or fallbacks. The earlier rewrite scored **93.8**.
These model-driven supplier outputs remain stochastic; this small screen does not establish
hosted improvement or a model ranking.

The Opus 5.5 screen failed to produce executable original-strategy actions; a subsequent probe
identified a provider refusal for the assembled strategy and adapter. The original text on its own
received a prose response, so this does not establish that the original prompt alone is refused.
Opus 4.8 produced executable actions, but both matched pairs were excluded for simulated seller
constraint failures. Its one completed original run scored 86.9, which is not a paired comparison.
Sonnet 5.5 remains the configured model. No provider-refused strategy is replaced silently.

The 32 local buyer sessions across these six experiments consumed **zero hosted arena sessions**.
Hosted quota was last observed at 26 used / 174 available. Hosted checks remain conditional on
credible local improvement and clean tactic compliance; the next hosted check is capped at two
sessions. Extension verification includes the protocol, supplier failures, provider refusal,
hosted attempt limits, API and browser checks: **97 tests passed, 2 optional upstream checks
skipped**. Lint, formatting and JavaScript syntax checks pass.

## Current local suppliers (24 October 2026)

All interactive local runs, prompt comparisons and benchmark entry points now use a Claude
supplier. Scripted supplier behavior and fixed-round withdrawal rules have been removed.
Unit tests use explicit transport doubles; their outcomes are never performance evidence.

The bounded local suite uses the current user prompt and four execution fixes. It covers ten
recorded public arena cases three times each (30 attempts). Cases retain their recorded companies,
grouped by public ownership: family-owned, corporate group, founder-led and privately held:

```bash
uv run aeiou benchmark --agent original --parallel 2 --label local-three-repeats
```

This plans 30 local negotiations and no hosted sessions. It starts one canary, then runs at the
selected concurrency. Any execution failure stops scheduling further attempts; completed runs
and unstarted rows remain in the report. There are no automatic retries. Public cases, opening emails,
terms and captured attachment text come from `data/local/public-setups.json`, extracted from the
oldest available recording per case without selecting by outcome. Hashes and a copy are saved in
each evaluation report. Later seller replies are generated independently; private buyer objectives,
alternatives and prior final scores do not enter the supplier prompt. Seller personas and reservation
prices are synthetic assumptions, not recovered Tacto policies. Clocks are disabled and model
outputs are stochastic. Normalized scores use synthetic floors; costs and deal rates are also reported.
This is exploratory evidence on familiar case families. It creates no hosted sessions.

Previous run histories and reports are recoverable under `data/local/history-archive/`.
`data/local/history-reset.json` identifies the original archive; the subsequent
`*-superseded-scripted` archive contains the cancelled batch. Prompts, research, settings,
datasets and credentials were retained. The historical results below describe retired experiments.

## Simple evaluation suite

Open **http://127.0.0.1:8765/#evaluations**. Prompt A and Prompt B each have an editor, individual
run scores, and their arithmetic mean. Prompt B starts as an editable copy of A. Start either
panel independently, including while the other is running, or use **Run both prompts** to start
both concurrently on the same model. Each panel launches six fresh sessions in parallel (12 total
for both), with separate agents, transcripts, failures and means. A shared worker pool allows
up to 10 runs across both panels; the remaining runs wait for a slot. All sandbox requests share
an eight-per-second rate limit, including retries and attachment downloads. Existing single-prompt batches
appear in A. Editing a prompt preserves the previous scores with an explicit saved-prompt label;
drafts persist in this browser, and each batch retains the exact submitted prompt on disk.

The suite calls **Tacto's hosted sandbox** through the pinned
[Arena Kit client](https://github.com/tacto-playground/negotiation-arena-kit/tree/75eecd19bf42c4f4b5a93f01139fa2a757ec5c07).
It runs D01 (one foundry) and D02 (two competing shaft suppliers), three times each in fresh
sessions: **six negotiations per evaluation**. Supplier replies and final scores come from Tacto.
Sandbox sessions do not consume the practice quota or affect the leaderboard. The buyer uses the
selected Claude model and the existing prompt controller, and consumes model credits. Both
ANTHROPIC_API_KEY and ARENA_API_KEY must be configured; ARENA_URL must point to https://arena.tacto.ai.
The suite never falls back to the local scripted simulator. These are demo suppliers, not the
hidden final cases. See the [Tacto FAQ](https://arena.tacto.ai/faq).

The exact prompt, model, kit revision, case snapshots, session IDs, scores and transcript links are saved locally under
`data/local/eval-suite/` and `data/local/runs/`. Prompt edits apply to the evaluation without
overwriting `SYSTEM_PROMPT.md`. The latest evaluation and its prompt reload together. A changed
prompt is marked separately from its previous results. Completed scores, including zero,
count toward the mean; failed and unstarted runs do not. Each row shows its repeat number and
individual score, and the panel shows how many runs are active or queued. An execution failure
stops queued runs from starting while other active runs finish; a missing official score is never replaced
with a local estimate. Old S01–S03 results are not included in the sandbox suite. If the buyer
controller cannot make a legal move, the failure retains the remote session ID; it does not send
a buyer walk-away or invent a no-deal score.

Every supplier reply containing alternative packages is checked against the API's registered
standing offer before the agent decides, including reissued menus after a clarification.
If Tacto still rejects an acceptance as "not a standing offer", the runner refreshes the session and
all supplier threads, marks the rejected ID unavailable, and asks the agent to decide again.
The agent can request confirmation of the exact quoted package or choose another eligible offer;
the runner never substitutes an offer. Rejected requests are audited separately and consume no
email round. Recovery is limited to two attempts and respects cancellation and ended sessions.
The evaluation UI shows recorded scores and averages only. Post-run counterfactual analyses
are retained separately in diagnostic reports.

**Stop prompt A**, **Stop prompt B**, or **Stop all runs** cancel the selected batches without
stopping the web server. Queued sessions do not start, and no further buyer messages are sent.
In-flight requests finish before workers exit, so the controls may briefly show **Stopping…**.
Completed scores and recorded conversations are retained; cancelled runs do not affect the mean.
The workbench closes the affected remote test sessions through the arena's walk-away endpoint
and records this administrative cleanup separately from negotiated results. Execution failures
also close their sessions. Failed/stopped sessions owned by this workbench are reconciled before
the next evaluation; active, completed and final sessions are never cleaned up this way.
New sessions check remote sandbox/practice occupancy under a shared admission lock. Full capacity
and open-session-limit 429 responses queue the run until a slot is free or the user stops it.

### Hosted practice

Choose **Hosted practice** in the eval suite. Edit its independent prompt or copy the current
sandbox A/B draft. Select all hosted practice cases or a single case, and one to three repeats
(default: one). The button states the total number of paid-model runs and practice attempts.
Case availability and quota come from the arena; non-hosted buyer examples cannot be selected.
The submitted prompt uses the same neutral controller as sandbox evaluations, with no automatic
reference-prompt tactics. Prompt text, model settings, plan and official scores are saved together.
Up to six practice runs execute concurrently; remaining runs queue. Per-case and overall averages
include completed official zeros and exclude failures/cancellations. Practice results are separate
from sandbox results. Click a case to follow its conversation live.

The same path is available from the CLI with `practice --prompt-file ./prompt.txt`, or
`practice --eval-batch BATCH_ID` to copy a saved eval's exact prompt and model settings.
`--max-sessions` caps the total attempts; `--repeats` defaults to one. Merely opening the practice
tab refreshes the case catalog and quota without starting negotiations.

Click a running case to follow its live conversation. It refreshes every second, separates supplier
threads, and follows new messages automatically. Turn off **Follow newest** to read earlier messages;
**Back to evaluations** returns to the individual scores and mean without interrupting the batch.
Each supplier thread shows its opening quote, latest quoted packages, and the buyer's latest
priced proposal, including unit prices, terms, and estimated costs over the full buying period.
Estimates follow the pinned kit's public cost formula and quote standard terms; hidden supplier
relationship costs are excluded. They never replace a hosted score. The final outcome shows
Tacto's official opening, deal, and best attainable costs; the hidden best cost stays undisclosed
until the run ends.

New sandbox and practice runs also save a **Buyer decision summary** above each buyer message.
Expand it for changes in the supplier's response, source-linked claims and inferences, unresolved
questions, and the intended next move. Each supplier has its own assessment, updated in the same
model response as the email; memory is isolated to that run. These are concise conclusions, not
private chain of thought. Existing transcripts are not backfilled with invented assessments.

The controller rebuilds an offer ledger from the actual transcript and computes changes in total
buyer cost, including payment, delivery and contract terms. Only must-have-valid packages enter
comparisons; historical offers do not become eligible for acceptance. Summaries are never sent to
suppliers. Source references and declared economic calculations are checked before sending, using
the existing single repair attempt within the shared 50-second decision budget. These checks do
not establish the truth of every free-text claim. Tactics remain in the editable prompt, and only
official arena results contribute to evaluation scores.

### Provider failures

Claude's `reasoning_extraction` refusal was reproduced with the old preparation request. The active
prompt now asks for a concise decision brief of facts, conclusions and intended actions, retaining the
nine section headings and negotiation tactics. The archived original is unchanged. Refusal categories,
provider explanations and request IDs are recorded; fallback credentials and internal reasoning are not.
Failed executions show no negotiation outcome, rather than a fictitious no-deal score.

## Test and benchmark (historical context)

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run aeiou benchmark --agent original --seeds 1 --label language-local
# Actual Claude calls against local simulated suppliers:
uv run aeiou benchmark --agent claude --seeds 1 --seed-start 43 --parallel 3 --label claude
# Hosted naive baseline:
uv run aeiou practice --agent baseline --parallel 3
```

The retired local suite covered the unmodified upstream contract supplier and five scripted profiles:
hardliner, cash-constrained, relationship, evidence skeptic and adversarial. Each plays three
upstream S cases and two synthetic quality/coverage cases. Paired agents receive identical
conditions per seed; private floors remain inside the simulator. A profile is beaten when
AEIOU's mean exceeds the naive baseline; individual wins, ties and losses are reported.

Three local policy iterations culminated in **98.3/100 versus 68.6** over 90 paired matches.
A separate 150-match held-out seed run scored **97.6 versus 66.5**, beating the baseline in
all six profiles without broken must-haves. Those are deterministic-policy results.
Claude-specific reports contain actual API calls, errors and fallback counts. Failed development
reports remain in the archive. See the history archive for these retired results.

The revised Claude-backed suite scored **86.1 versus 60.8** across 30 paired matches (seed 43),
beating the baseline on the mean for all six profiles. It made 161 Claude calls, with zero provider
errors, one guard fallback and no failed runs. Individual losses remain visible in the report.
Hosted practice finished ahead on **10/10 cases**, with best observed means **56.9 versus 10.9**.
Validation before the prompt-comparison extension: **67 tests passed, 2 optional upstream checks skipped**, plus lint, formatting and a
browser-launched local negotiation through to its completed replay.

The historical comparison used **best observed score per agent per case**. The current Hosted
benchmarks tab instead shows all attempts grouped by case, agent, model and recorded prompt hash,
with mean and best scores separately. Pre-reset hosted history is opt-in and never merged with fresh runs.
It is adaptive practice, not a blinded statistical trial. No practice win establishes victory over
hidden final suppliers or every other team's agent.

## Final runner

After the Sunday release becomes public, refresh data and start the autonomous runner:

```bash
uv run arena data
uv run aeiou doctor
uv run aeiou final --parallel 10 --waves 5
```

It waits on pre-final `403`, resumes existing sessions on `409`, follows cap extensions and
closes before the cap with a latency margin. It reads your cases and sessions only, without
live feeds, leaderboards or results. A two-wave test covers waiting, parallel cases, resume
and audit persistence. The actual final has not been started during development.

For an HTTP rehearsal, run `uv run arena-sim serve --final-rehearsal --data data/package`,
register a simulator key with `ARENA_URL=http://127.0.0.1:8000 uv run arena register AEIOU sim`,
then run `aeiou final --waves 2` with the simulator URL and key in the environment.
Confirm event times with organizers: the upstream README and live FAQ have differed.
The runner follows API wave state, not hard-coded event times.

## Source map

| Path | Purpose |
| --- | --- |
| `src/aeiou/agent.py`, `prompts.py` | Claude backend, negotiation and guards |
| `src/aeiou/research.py`, `optimizer.py` | Evidence and full-cost package search |
| `src/aeiou/runner.py` | Hosted execution and durable audit |
| `src/aeiou/simulation.py`, `benchmark.py` | Local opponents and paired evaluation |
| `src/aeiou/eval_suite.py` | Prompt evaluations against the pinned Arena Kit simulator |
| `src/aeiou/hosted_evaluation.py` | Observed hosted comparison |
| `src/aeiou/prompt_protocol.py`, `prompt_comparison.py`, `language_sim.py` | Exact original prompt, local matched comparison and model-driven seller |
| `src/aeiou/service.py`, `web/` | FastAPI and the built dashboard |
| `frontend/` | Dashboard source (React, Tailwind, shadcn/ui) |
| `data/package/`, `docs/arena/` | Procurement dataset, case files and challenge requirements |
| `vendor/arena-kit/` | Pinned kit client, C function, simulator and tests |
| `reports/` | Versioned benchmark evidence |

Docker packaging is included; supply credentials at runtime:

```bash
docker build -t aeiou .
docker run --rm --env-file .env -p 127.0.0.1:8765:8765 -v "$PWD/data/local:/app/data/local" aeiou
```

The Docker build was attempted but could not be verified because the local Docker daemon was stopped.

Negotiation assumptions and paper references are in [docs/RESEARCH.md](docs/RESEARCH.md).
The opt-in strategic upgrade, research search, ablations and offline writer optimization
are documented in [docs/STRATEGIC_V2.md](docs/STRATEGIC_V2.md), including measured results
and the limits of the current evidence.
Should-cost and supplier financing remain estimates. The Sunday release was not yet public
during the initial build. No presentation file was present locally or upstream to review.

Code is MIT; data and challenge docs are CC BY 4.0. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [data/LICENSE](data/LICENSE).
