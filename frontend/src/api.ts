export type Job = {
  status: "running" | "completed" | "failed"
  kind: "negotiation" | "benchmark" | "evaluation"
  variant_id?: "a" | "b" | "practice"
  case_id?: string
}

export type Status = {
  model: string
  provider: "anthropic" | "openai"
  effort: string
  speed: "standard" | "fast"
  models: { id: string; label: string; provider: "anthropic" | "openai"; efforts: string[]; default_effort: string }[]
  claude_configured: boolean
  openai_configured: boolean
  llm_configured: boolean
  providers: Record<"anthropic" | "openai", { configured: boolean }>
  arena_configured: boolean
  facts: number
  tables: Record<string, unknown>
  sunday_release: boolean
  jobs: Record<string, Job>
}

export type Case = {
  case_id: string
  type: string
  title: string
  briefing?: string
  suppliers: string[]
  parts: string[]
  negotiable: string[]
  round_limit: number
  hosted?: boolean
  fixed_terms?: Record<string, unknown>
  term_options?: Record<string, unknown[]>
  buyer: {
    need_per_year: number
    horizon_years?: number
    current_unit_price: number | null
    fallback_unit_price: number
    category_goal: string
    must_haves: (string | { field: string; op: string; value: unknown })[]
    reference_payment_days?: number
    need_lead_time_days?: number
    interest_rate?: number
    freight_eur_per_unit?: Record<string, number>
    late_cost_per_day?: number
    cost_per_defect?: number
    fallback_risk_pct?: number
    index_paths?: Record<string, number>
    index_risk_pct?: number
    relationship_eur_per_point?: number
    sustainable_margin_pct?: number
    alternative?: { kind: string; terms?: Record<string, unknown> }
  }
}

export type Cases = {
  local: Case[]
  hosted: Case[]
  opponents: Record<string, { name: string; description: string }>
}

export type Result = {
  deal: boolean
  supplier_id?: string | null
  offer_id?: string | null
  score: number | null
  c_open?: number | null
  c_deal?: number | null
  c_alt?: number | null
  c_best?: number | null
  must_haves_ok?: boolean
  terms?: Record<string, unknown>
  cost_breakdown?: Record<string, number>
}

export type RunMetrics = {
  elapsed_s: number
  calls: number
  fallbacks: number
  errors: number
  input_tokens: number
  output_tokens: number
  reasoning_tokens?: number
  cached_input_tokens?: number
  resolved_model?: string | null
  refusals?: number
  refusal_details?: { category?: string; explanation?: string; model?: string; request_id?: string }[]
}

export type RunSummary = {
  id: string
  case_id: string
  mode: "local" | "hosted"
  agent: string
  opponent?: string
  created_at: string
  status: "running" | "completed" | "failed" | "cancelled"
  result?: Result
  metrics?: RunMetrics
  model?: string
  seed?: number
  prompt?: PromptMetadata
  supplier_version?: string
  supplier_model?: string
  error?: string | null
  failure_origin?: string
  history?: { retired: boolean; reason: string }
}

export type Message = {
  message_id?: string
  from: string
  subject?: string
  body?: string
  status?: string
  action?: { type: string } | null
  term_sheets?: { term_sheet_id?: string; terms: Record<string, unknown> }[]
  claim_results?: { result: string }[]
  attachments?: { name: string; text?: string }[]
}

export type Run = RunSummary & {
  case?: Case
  threads?: Record<string, Message[]>
  decisions?: Decision[]
  prompt?: PromptMetadata
  error?: string | null
  termination?: { origin: string; reason: string; buyer_action_sent: boolean }
  remote_cleanup?: { status?: string; error?: string }
  audit?: { contract_rejections?: string[] }
}

export type Decision = {
  supplier_id: string
  source: string
  action: string
  elapsed_s: number
  message_index?: number
  assessment_version?: string
  assessment?: {
    observed_message: string
    changes: string[]
    evidence: { claim: string; kind: "public_record" | "supplier_claim" | "inference"; source_refs: string[]; confidence: string }[]
    open_questions: string[]
    response_to_last_move: string
    adaptation: string
    next_move: string
  }
  numeric_checks?: { calculation_id: string; value: number; quote: string; description: string }[]
  economics?: {
    supplier_change: OfferChange | null
    buyer_change: OfferChange | null
    current_best_valid_offer: { cost: number } | null
    cost_basis: string
  }
  walk_away?: {
    reason_code: string
    model_reason?: string
    rounds_left?: Record<string, number>
    offers?: Record<
      string,
      {
        term_sheet_id: string
        repeat_count: number
        cost: number
        must_haves_ok: boolean
      }[]
    >
    [key: string]: unknown
  }
}

export type OfferChange = {
  from_ref: string
  to_ref: string
  cost_delta: number
  term_changes: { term: string; before: unknown; after: unknown }[]
}

export type BenchmarkOpponent = {
  id: string
  name: string
  description: string
  score: number
  baseline_score: number
  delta: number
  wins: number
  ties: number
  losses: number
  beaten: boolean
}

export type Benchmark = {
  label: string
  agent: string
  created_at: string
  seed_start: number
  seeds: number
  matches: number
  mean_score: number
  baseline_score: number
  failures: number
  must_have_violations: number
  llm_calls: number
  llm_errors: number
  fallbacks: number
  opponents: BenchmarkOpponent[]
  rows: unknown[]
}

export type HostedEvaluation = {
  source: "live" | "snapshot"
  selection: string
  limitation: string
  cases: number
  paired_cases: number
  mean_score: number | null
  baseline_score: number | null
  wins: number
  ties: number
  losses: number
  rows: {
    case_id: string
    run_id?: string
    score: number | null
    baseline_score: number | null
    delta: number | null
    claude_attempts: number
    baseline_attempts: number
    claude_failures: number
    deal?: boolean | null
  }[]
}

export type Stage =
  "briefing" | "opening" | "negotiating" | "final_offer" | "closed" | "no_deal"

export type Deal = {
  id: string
  case_id: string
  title: string
  type?: string
  mode: "local" | "hosted"
  agent: string
  opponent?: string
  status: RunSummary["status"]
  stage: Stage
  created_at: string
  supplier_id: string | null
  suppliers: number
  rounds: number
  round_limit?: number
  asks: number[]
  bids: number[]
  start: number | null
  now: number | null
  bid: number | null
  target?: number
  walk_away?: number
  score?: number | null
  saved?: number | null
}

export type PromptSide = {
  run_id: string
  status: string
  result?: Result
  audit: { format_repairs: number }
  failure_origin?: string
}

export type PromptStats = Record<string, number>

export type PromptComparison = {
  label: string
  status: string
  arena_sessions_used: number
  limitations: string[]
  conditions: {
    protocol_version?: string
    supplier_kind: string
    supplier_model?: string
    buyer_model: string
    scenarios: { case_id: string; opponent: string; seed: number }[]
  }
  rows: {
    case_id: string
    opponent: string
    seed: number
    original?: PromptSide
    adapted?: PromptSide
  }[]
  summary?: { original: PromptStats; adapted: PromptStats }
  prompts?: { original: PromptMetadata; adapted: PromptMetadata }
}

export type PromptMetadata = {
  strategy_sha256: string
  system_sha256?: string
  strategy_revision?: string
  protocol_version?: string
  required_repeats?: number
}

export type Prompts = {
  transport: string
  variants: Record<
    "original" | "adapted",
    PromptMetadata & { strategy: string }
  >
  reference_original?: PromptMetadata & { strategy: string }
  revision_note?: string
  research_sources?: { title: string; url: string; application: string }[]
}

export type EvalBatch = {
  id: string
  suite_id: string
  variant_id?: "a" | "b" | "practice"
  comparison_id?: string | null
  created_at: string
  prompt_text: string
  model: string
  provider?: "anthropic" | "openai"
  effort?: string
  speed?: "standard" | "fast"
  kit_revision: string
  status: "running" | "completed" | "stopped"
  completed: number
  mean_score: number | null
  stop_reason?: string
  stop_requested?: boolean
  case_set?: "sandbox" | "practice"
  repeats?: number
  rows: {
    case_id: string
    title: string
    repeat: number
    status: "queued" | "waiting" | "running" | "completed" | "failed" | "cancelled" | "not_started"
    run_id?: string
    score: number | null
    error?: string
    wait_reason?: string
    cleanup_error?: string
  }[]
}

export type EvalSuite = {
  suite_id: string
  source_url: string
  kit_revision: string
  cases: { case_id: string; title: string }[]
  repeats: number
  planned_runs: number
  parallel_runs?: number
  max_parallel_runs?: number
  draft_prompt: string | null
  latest: EvalBatch | null
  variants?: { id: "a" | "b"; label: string; latest: EvalBatch | null }[]
  practice?: PracticeCatalog & { latest: EvalBatch | null; parallel_runs: number }
}

export type PracticeCatalog = {
  cases: { case_id: string; title: string }[]
  quota: { used: number; left: number; limit: number; resets_at?: string } | null
  checked_at?: string
}

export type LocalEvaluation = {
  label: string
  status: string
  arena_sessions_used: number
  prior_attempts: number
  new_attempt_limit: number
  conditions: { buyer_model: string; supplier_model: string; supplier_version: string }
  summary: PromptStats & { supplier_calls: number; supplier_failures: number }
  rows: { case_id: string; opponent: string; seed: number; retained?: boolean;
    run?: PromptSide & { id: string; failure_origin?: string } }[]
  limitations: string[]
  stop_reason?: string
}

export type EvaluationStats = {
  attempts: number; completed: number; failures: number; supplier_failures: number; cancelled: number; running: number;
  deals: number; refusals: number; mean_score: number | null; best_score: number | null;
  median_score: number | null; score_stddev: number | null; reliability_score: number | null;
}
export type SupplierProfile = { id: string; name: string; description: string; case_ids: string[]; source: string }
export type LocalDashboard = {
  attempts: RunSummary[]; summary: EvaluationStats; profiles: SupplierProfile[];
  cases: number; repeats: number; planned_runs: number; grouping_note: string; scoring_note: string;
  versions: { id: string; agent: string; model?: string; prompt: PromptMetadata; supplier_version?: string; supplier_model?: string;
    current: boolean; summary: EvaluationStats; run_ids: string[];
    profiles: (SupplierProfile & { summary: EvaluationStats; cases: (EvaluationStats & { case_id: string; required: number })[] })[] }[];
}
export type HostedBenchmarks = {
  scope: string; historical_attempts: number; current_attempts: number; cases: number;
  summary: EvaluationStats; selection: string; limitations: string; available_data: string[]; unavailable_data: string;
  rows: { case_id: string; agent: string; model: string; prompt_hash: string; summary: EvaluationStats; run_ids: string[] }[];
  attempts: RunSummary[];
}

export type Research = {
  facts: Record<string, unknown>
  documents: { id: string; text: string }[]
  indices: {
    series: string
    weight: number
    weighted_change_pct: number
    claim: string
  }[]
  estimates: Record<
    string,
    { full_cost: number; confidence_pct: number; source: string }
  >
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init)
  if (!r.ok) {
    let detail: unknown = r.statusText
    try {
      detail = (await r.json()).detail
    } catch {
      // Non-JSON error body; keep the status text.
    }
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    )
  }
  return r.json()
}
