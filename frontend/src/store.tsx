import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react"
import { toast } from "sonner"

import {
  api,
  type Benchmark,
  type Cases,
  type EvalSuite,
  type HostedEvaluation,
  type LocalEvaluation,
  type LocalDashboard,
  type PromptComparison,
  type Prompts,
  type RunSummary,
  type Status,
} from "@/api"

export type Scope = "hosted" | "local"
// `live` paces local exchanges for demos; `stay` keeps the current page instead of opening the replay.
export type LaunchPreset = {
  mode?: Scope
  caseId?: string
  live?: boolean
  stay?: boolean
}

type Workbench = {
  ready: boolean
  error?: string
  status?: Status
  cases: Cases
  runs: RunSummary[]
  benchmarks: Benchmark[]
  hosted?: HostedEvaluation
  prompts?: Prompts
  promptReports: PromptComparison[]
  localEvaluations: LocalEvaluation[]
  localDashboard?: LocalDashboard
  evalSuite?: EvalSuite
  scope: Scope
  setScope: (s: Scope) => void
  refresh: () => Promise<void>
  launch: { open: boolean; preset: LaunchPreset }
  openLaunch: (preset?: LaunchPreset) => void
  closeLaunch: () => void
  caseTitle: (id: string) => string
}

const empty: Cases = { local: [], hosted: [], opponents: {} }
const Ctx = createContext<Workbench | null>(null)

export function WorkbenchProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<
    Omit<
      Workbench,
      | "setScope"
      | "refresh"
      | "launch"
      | "openLaunch"
      | "closeLaunch"
      | "caseTitle"
      | "scope"
    >
  >({
    ready: false,
    cases: empty,
    runs: [],
    benchmarks: [],
    promptReports: [],
    localEvaluations: [],
  })
  const [scope, setScope] = useState<Scope>("local")
  const [launch, setLaunch] = useState<{ open: boolean; preset: LaunchPreset }>(
    { open: false, preset: {} },
  )
  const busy = useRef(false)

  const refresh = useCallback(async () => {
    if (busy.current) return
    busy.current = true
    try {
      const [
        status,
        runs,
        benchmarks,
        hosted,
        prompts,
        promptReports,
        localEvaluations,
        localDashboard,
        evalSuite,
      ] = await Promise.all([
        api<Status>("/api/status"),
        api<RunSummary[]>("/api/runs"),
        api<Benchmark[]>("/api/benchmarks"),
        api<HostedEvaluation>("/api/hosted-evaluation"),
        api<Prompts>("/api/prompts"),
        api<PromptComparison[]>("/api/prompt-comparisons"),
        api<LocalEvaluation[]>("/api/local-evaluations"),
        api<LocalDashboard>("/api/local-dashboard"),
        api<EvalSuite>("/api/eval-suite"),
      ])
      setState((s) => ({
        ...s,
        status,
        runs,
        benchmarks,
        hosted,
        prompts,
        promptReports,
        localEvaluations,
        localDashboard,
        evalSuite,
        error: undefined,
      }))
    } catch (e) {
      setState((s) => (s.ready ? s : { ...s, error: (e as Error).message }))
      if (state.ready) toast.error((e as Error).message)
    } finally {
      busy.current = false
    }
  }, [state.ready])

  useEffect(() => {
    api<Cases>("/api/cases")
      .then((cases) => setState((s) => ({ ...s, cases })))
      .then(refresh)
      .then(() => setState((s) => ({ ...s, ready: true })))
      .catch((e) => setState((s) => ({ ...s, error: (e as Error).message })))
    // Load once on mount; polling below keeps the data fresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const t = setInterval(() => {
      if (!document.hidden) refresh()
    }, 5000)
    return () => clearInterval(t)
  }, [refresh])

  const caseTitle = useCallback(
    (id: string) =>
      [...state.cases.hosted, ...state.cases.local].find(
        (c) => c.case_id === id,
      )?.title ?? id,
    [state.cases],
  )

  const value: Workbench = {
    ...state,
    scope,
    setScope,
    refresh,
    launch,
    openLaunch: (preset = {}) => setLaunch({ open: true, preset }),
    closeLaunch: () => setLaunch((l) => ({ ...l, open: false })),
    caseTitle,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWorkbench() {
  const v = useContext(Ctx)
  if (!v) throw new Error("useWorkbench must be used inside WorkbenchProvider")
  return v
}
