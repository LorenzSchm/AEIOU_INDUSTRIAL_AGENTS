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

Open **http://127.0.0.1:8765**. Pages cover overview, negotiation/replay, evidence, local
evaluations and a separate hosted comparison. All values come from recorded runs and reports.
The adaptive policy needs no API key. Claude calls use Anthropic credits; hosted practice also
uses arena quota. Page load does not start paid work.

Environment variables override `.env`. Defaults: `LLM_PROVIDER=anthropic`,
`LLM_MODEL=claude-sonnet-5-5`, `LLM_TIMEOUT=35`, `LLM_MAX_TOKENS=1800`. See
[.env.example](.env.example). Supplied credentials are configured locally, excluded from Git,
and never sent to the browser. Runtime transcripts are ignored under `data/local/`.

```bash
uv run aeiou doctor
uv run aeiou play case:P01 --agent claude
uv run aeiou practice --agent claude --parallel 3
uv run aeiou play case:S01 --mode local --agent adaptive
uv run aeiou play case:T02 --mode local --agent claude --opponent cashflow
```

## Agent

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
uv run aeiou benchmark --agent strategic-policy --seeds 2 --seed-start 99 --label strategic
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

Open **http://127.0.0.1:8765/#prompts** to inspect both full prompt texts, the shared transport
adapter, paired outcomes, tactic audits and negotiation replays. Page loads never start tests.

```bash
# Six matched numeric/contract scenarios, 12 buyer sessions. Zero hosted arena sessions.
uv run aeiou compare-prompts --supplier scripted --label original-vs-adapted
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
indicators, not private reasoning. All outgoing email wording is preserved. Both variants receive
the same public research, cost calculations, candidate packages, model, token limit and transport.
The transport validates legal terms, buyer must-haves and acceptance against the stated alternative.
It does not force acceptance of final/agreed offers or replace the model with a fallback policy.
One format repair is allowed, then invalid output fails visibly. The original third-offer and
five-repeat acceptance instructions remain together as written, including their potential tension.

Reports under `reports/prompt-comparisons/` archive both strategy texts and assembled system
prompts, hashes, fixed case/seed plans, model settings, every outcome, failures, repairs and token
usage. Failed runs count zero in the headline mean. This is a comparison under a shared harness,
not an exact replay of the earlier production guard and closing policy.

Scripted suppliers test numerical behavior, not language effects. The LLM supplier uses a separate
private seller persona and enforced economic floor, can quote up to three executable packages,
and sees neither buyer objectives nor private research. Its assumptions are synthetic, not Tacto's
hidden supplier prompts. Seeds fix economics; model outputs remain stochastic. These small local
screens do not establish a statistical winner or hosted performance. Local clocks are disabled;
decisions exceeding the hosted 60-second limit are explicitly counted.

## Test and benchmark

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run aeiou benchmark --agent adaptive --seeds 3 --label local
uv run aeiou benchmark --agent adaptive --seeds 5 --seed-start 42 --label heldout
# Actual Claude calls against local simulated suppliers:
uv run aeiou benchmark --agent claude --seeds 1 --seed-start 43 --parallel 3 --label claude
# Hosted naive baseline:
uv run aeiou practice --agent baseline --parallel 3
```

The local suite covers the unmodified upstream contract supplier and five synthetic profiles:
hardliner, cash-constrained, relationship, evidence skeptic and adversarial. Each plays three
upstream S cases and two synthetic quality/coverage cases. Paired agents receive identical
conditions per seed; private floors remain inside the simulator. A profile is beaten when
AEIOU's mean exceeds the naive baseline; individual wins, ties and losses are reported.

Three local policy iterations culminated in **98.3/100 versus 68.6** over 90 paired matches.
A separate 150-match held-out seed run scored **97.6 versus 66.5**, beating the baseline in
all six profiles without broken must-haves. Those are deterministic-policy results.
Claude-specific reports contain actual API calls, errors and fallback counts. Failed development
reports remain visible. See [reports/](reports/) and the dashboard.

The revised Claude-backed suite scored **86.1 versus 60.8** across 30 paired matches (seed 43),
beating the baseline on the mean for all six profiles. It made 161 Claude calls, with zero provider
errors, one guard fallback and no failed runs. Individual losses remain visible in the report.
Hosted practice finished ahead on **10/10 cases**, with best observed means **56.9 versus 10.9**.
Validation before the prompt-comparison extension: **67 tests passed, 2 optional upstream checks skipped**, plus lint, formatting and a
browser-launched local negotiation through to its completed replay.

Hosted comparison uses **best observed score per agent per case**, with attempt counts shown.
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
| `src/aeiou/hosted_evaluation.py` | Observed hosted comparison |
| `src/aeiou/prompt_protocol.py`, `prompt_comparison.py`, `language_sim.py` | Exact original prompt, local matched comparison and model-driven seller |
| `src/aeiou/service.py`, `web/` | FastAPI and responsive dashboard |
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
