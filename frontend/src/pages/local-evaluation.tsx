import { useState } from "react"
import { Copy, Loader2, Play, Square } from "lucide-react"
import { toast } from "sonner"

import { api, type EvalBatch } from "@/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RunResults } from "@/components/eval-run-results"
import { BuyerSettings } from "@/components/buyer-settings"
import { useBuyerSettings } from "@/hooks/use-buyer-settings"
import { useWorkbench } from "@/store"
import { PracticeEvaluation } from "@/pages/practice-evaluation"

type Variant = "a" | "b"
type Drafts = Partial<Record<Variant, string>>
const draftKey = "aeiou.eval-suite.prompt-drafts.v1"
const variants: Variant[] = ["a", "b"]

function savedDrafts(): Drafts {
  try {
    const saved = JSON.parse(localStorage.getItem(draftKey) ?? "{}")
    return Object.fromEntries(variants.filter(id => typeof saved?.[id] === "string").map(id => [id, saved[id]]))
  } catch { return {} }
}

export function LocalEvaluationPage() {
  const [mode, setMode] = useState<"sandbox" | "practice">("sandbox")
  return <div className="space-y-6">
    <div className="flex gap-2" aria-label="Evaluation mode">
      <Button variant={mode === "sandbox" ? "default" : "outline"} aria-pressed={mode === "sandbox"} onClick={() => setMode("sandbox")}>Sandbox</Button>
      <Button variant={mode === "practice" ? "default" : "outline"} aria-pressed={mode === "practice"} onClick={() => setMode("practice")}>Hosted practice</Button>
    </div>
    {mode === "sandbox" ? <SandboxComparison /> : <PracticeEvaluation />}
  </div>
}

function SandboxComparison() {
  const { prompts, evalSuite, status, refresh } = useWorkbench()
  const buyer = useBuyerSettings(status)
  const [drafts, setDrafts] = useState<Drafts>(savedDrafts)
  const [submitted, setSubmitted] = useState<Partial<Record<Variant, string>>>({})
  const [starting, setStarting] = useState<Variant[]>([])
  const [stopping, setStopping] = useState<Variant[]>([])
  const latest: Record<Variant, EvalBatch | null> = {
    a: evalSuite?.variants?.find(v => v.id === "a")?.latest ?? evalSuite?.latest ?? null,
    b: evalSuite?.variants?.find(v => v.id === "b")?.latest ?? null,
  }
  const initial = latest.a?.prompt_text ?? evalSuite?.draft_prompt ?? prompts?.variants.original.strategy ?? ""
  const text = { a: drafts.a ?? latest.a?.prompt_text ?? initial, b: drafts.b ?? latest.b?.prompt_text ?? initial }
  const count = evalSuite?.planned_runs ?? 0
  const waiting = (id: Variant) => !!submitted[id] && submitted[id] !== latest[id]?.id
  const running = (id: Variant) => latest[id]?.status === "running"
    || Object.values(status?.jobs ?? {}).some(job => job.kind === "evaluation" && job.variant_id === id && job.status === "running")
  const busy = (id: Variant) => starting.includes(id) || waiting(id) || running(id)
  const stopPending = (id: Variant) => stopping.includes(id) || !!latest[id]?.stop_requested
  const stoppable = variants.filter(id => latest[id]?.status === "running" && !stopPending(id))

  const stop = async (ids: Variant[]) => {
    setStopping(current => [...current, ...ids])
    try {
      await Promise.all(ids.map(id => api(`/api/eval-suite/${latest[id]!.id}/stop`, { method: "POST" })))
      await refresh()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setStopping(current => current.filter(id => !ids.includes(id)))
    }
  }

  const edit = (id: Variant, value: string) => {
    // Capture both defaults on the first edit, so editing/running A cannot silently change B.
    const next = { ...text, [id]: value }
    setDrafts(next)
    try { localStorage.setItem(draftKey, JSON.stringify(next)) } catch { /* Editing still works without storage. */ }
  }

  const start = async (ids: Variant[]) => {
    setStarting(current => [...current, ...ids])
    setDrafts(text)
    try { localStorage.setItem(draftKey, JSON.stringify(text)) } catch { /* The server snapshots submitted prompts. */ }
    try {
      const batches = ids.length === 2
        ? (await api<{ batches: { id: string; variant_id: Variant }[] }>("/api/eval-suite/compare", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt_a: text.a, prompt_b: text.b,
            model_a: buyer.selection("a").model, effort_a: buyer.selection("a").effort,
            model_b: buyer.selection("b").model, effort_b: buyer.selection("b").effort }),
        })).batches
        : [await api<{ id: string; variant_id: Variant }>("/api/eval-suite", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: text[ids[0]], variant_id: ids[0], ...buyer.selection(ids[0]) }),
        })]
      setSubmitted(current => ({ ...current, ...Object.fromEntries(batches.map(batch => [batch.variant_id, batch.id])) }))
      await refresh()
    } catch (error) {
      toast.error((error as Error).message)
    } finally {
      setStarting(current => current.filter(id => !ids.includes(id)))
    }
  }

  return <div className="mx-auto w-full max-w-7xl space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Eval suite</h1>
        <p className="mt-1 text-sm text-muted-foreground">Compare prompts and models against Tacto’s hosted sandbox suppliers.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {variants.some(running) && <Button variant="outline" onClick={() => stop(stoppable)} disabled={!stoppable.length}>
          <Square /> Stop all runs
        </Button>}
        <Button onClick={() => start(variants)} disabled={!count || variants.some(id => busy(id) || !text[id].trim() || !buyer.ready(id))}>
          {starting.length === 2 ? <Loader2 className="animate-spin" /> : <Play />}
          {starting.length === 2 ? "Starting both…" : "Run both prompts"}
        </Button>
      </div>
    </div>
    <p className="text-xs text-muted-foreground">
      <a href={evalSuite?.source_url} target="_blank" rel="noreferrer" className="underline underline-offset-2">Tacto sandbox</a>
      {` · 2 cases × ${evalSuite?.repeats ?? 3} repeats per prompt · ${count * 2} runs for both · Same controller · Buyer model credits`}
    </p>

    <div className="grid items-start gap-6 lg:grid-cols-2">
      {variants.map(id => <section key={id} aria-label={`Prompt ${id.toUpperCase()} evaluation`} className="min-w-0 space-y-4">
        <Card className="gap-4 shadow-xs">
          <CardHeader className="flex min-h-8 flex-wrap items-center justify-between gap-2">
            <CardTitle><label htmlFor={`eval-prompt-${id}`}>Prompt {id.toUpperCase()}</label></CardTitle>
            {id === "b" && <Button variant="ghost" size="sm" onClick={() => edit("b", text.a)} disabled={busy("b")}><Copy /> Copy A</Button>}
          </CardHeader>
          <CardContent className="space-y-4">
            <BuyerSettings label={`Prompt ${id.toUpperCase()}`} status={status} value={buyer.selection(id)}
              onChange={value => buyer.change(id, value)} disabled={busy(id)} />
            <textarea
              id={`eval-prompt-${id}`} value={text[id]} onChange={event => edit(id, event.target.value)}
              disabled={busy(id)} maxLength={100000} spellCheck={false} aria-describedby={`eval-plan-${id}`}
              className="min-h-72 w-full resize-y rounded-md border bg-background p-4 font-mono text-sm leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60"
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p id={`eval-plan-${id}`} className="text-xs text-muted-foreground">{count} negotiations{evalSuite?.parallel_runs === count && " in parallel"} · Fresh sessions</p>
              {latest[id]?.status === "running" ? <Button variant="outline" onClick={() => stop([id])} disabled={stopPending(id)}>
                {stopPending(id) ? <Loader2 className="animate-spin" /> : <Square />}
                {stopPending(id) ? "Stopping…" : `Stop prompt ${id.toUpperCase()}`}
              </Button> : <Button onClick={() => start([id])} disabled={!text[id].trim() || !count || busy(id) || !buyer.ready(id)}>
                {busy(id) ? <Loader2 className="animate-spin" /> : <Play />}
                {starting.includes(id) || waiting(id) ? "Starting…" : `Run prompt ${id.toUpperCase()}`}
              </Button>}
            </div>
          </CardContent>
        </Card>
        <RunResults evaluation={latest[id]} prompt={text[id]} waiting={waiting(id)} variant={id} cases={evalSuite?.cases ?? []} repeats={evalSuite?.repeats ?? 3} />
      </section>)}
    </div>
    <p className="text-xs text-muted-foreground">Each prompt runs independently.{evalSuite?.max_parallel_runs && ` Up to ${evalSuite.max_parallel_runs} runs at once across both prompts; additional runs wait for a slot.`} Means use completed Tacto scores, including zero. Failed runs are excluded.</p>
  </div>
}
