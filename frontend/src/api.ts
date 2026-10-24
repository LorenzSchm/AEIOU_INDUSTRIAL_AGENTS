export type Job = { status: "running" | "completed" | "failed"; kind: "negotiation" | "benchmark"; case_id?: string }

export type Status = {
  model: string
  models: { id: string; label: string }[]
  claude_configured: boolean
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
  buyer: {
    need_per_year: number
    current_unit_price: number
    fallback_unit_price: number
    category_goal: string
    must_haves: string[]
  }
}

export type Cases = { local: Case[]; hosted: Case[]; opponents: Record<string, { name: string; description: string }> }

export type Result = {
  deal: boolean
  score: number | null
  c_open?: number | null
  c_deal?: number | null
  c_alt?: number | null
  c_best?: number | null
  must_haves_ok?: boolean
  terms?: Record<string, unknown>
  cost_breakdown?: Record<string, number>
}

export type RunMetrics = { elapsed_s: number; calls: number; fallbacks: number; errors: number; input_tokens: number; output_tokens: number }

export type RunSummary = {
  id: string
  case_id: string
  mode: "local" | "hosted"
  agent: string
  opponent?: string
  created_at: string
  status: "running" | "completed" | "failed"
  result?: Result
  metrics?: RunMetrics
}

export type Message = {
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
  decisions?: { supplier_id: string; source: string; action: string; elapsed_s: number }[]
  error?: string | null
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

export type Stage = "briefing" | "opening" | "negotiating" | "final_offer" | "closed" | "no_deal"

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

export type PromptSide = { run_id: string; status: string; result?: Result; audit: { format_repairs: number } }

export type PromptStats = Record<string, number>

export type PromptComparison = {
  label: string
  status: string
  arena_sessions_used: number
  limitations: string[]
  conditions: {
    supplier_kind: string
    supplier_model?: string
    buyer_model: string
    scenarios: { case_id: string; opponent: string; seed: number }[]
  }
  rows: { case_id: string; opponent: string; seed: number; original?: PromptSide; adapted?: PromptSide }[]
  summary?: { original: PromptStats; adapted: PromptStats }
}

export type Prompts = {
  transport: string
  variants: Record<"original" | "adapted", { strategy: string; strategy_sha256: string }>
}

export type Research = {
  facts: Record<string, unknown>
  documents: { id: string; text: string }[]
  indices: { series: string; weight: number; weighted_change_pct: number; claim: string }[]
  estimates: Record<string, { full_cost: number; confidence_pct: number; source: string }>
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
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail))
  }
  return r.json()
}
