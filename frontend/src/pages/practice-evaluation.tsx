import { useEffect, useState } from "react"
import { Copy, Loader2, Play, RefreshCw, Square } from "lucide-react"
import { toast } from "sonner"

import { api, type PracticeCatalog } from "@/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { RunResults } from "@/components/eval-run-results"
import { BuyerSettings } from "@/components/buyer-settings"
import { useBuyerSettings } from "@/hooks/use-buyer-settings"
import { useWorkbench } from "@/store"

const draftKey = "aeiou.eval-suite.practice-prompt.v1"

export function PracticeEvaluation() {
  const { evalSuite, status, refresh } = useWorkbench()
  const buyer = useBuyerSettings(status)
  const latest = evalSuite?.practice?.latest
  const [draft, setDraft] = useState<string | null>(() => {
    try { return localStorage.getItem(draftKey) } catch { return null }
  })
  const [catalog, setCatalog] = useState<PracticeCatalog | null>(null)
  const [catalogError, setCatalogError] = useState("")
  const [loading, setLoading] = useState(true)
  const [selectedCase, setSelectedCase] = useState("all")
  const [repeats, setRepeats] = useState(1)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [sandboxDrafts] = useState<Partial<Record<"a" | "b", string>>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("aeiou.eval-suite.prompt-drafts.v1") ?? "{}")
      return Object.fromEntries(["a", "b"].filter(key => typeof saved?.[key] === "string").map(key => [key, saved[key]]))
    } catch { return {} }
  })
  const sourceA = sandboxDrafts.a ?? evalSuite?.variants?.find(v => v.id === "a")?.latest?.prompt_text ?? ""
  const sourceB = sandboxDrafts.b ?? evalSuite?.variants?.find(v => v.id === "b")?.latest?.prompt_text ?? ""
  const prompt = draft ?? latest?.prompt_text ?? (sourceB || sourceA)
  const waiting = !!submitted && submitted !== latest?.id
  const running = latest?.status === "running"
  const busy = starting || waiting || running
  const cases = catalog?.cases ?? []
  const selected = selectedCase === "all" ? cases : cases.filter(c => c.case_id === selectedCase)
  const count = selected.length * repeats
  const insufficient = !!catalog?.quota && count > catalog.quota.left
  const canStart = !!prompt.trim() && count > 0 && !!catalog?.quota && !insufficient && !busy && !loading && buyer.ready("practice")

  useEffect(() => {
    let active = true
    api<PracticeCatalog>("/api/eval-suite/practice/catalog")
      .then(value => { if (active) setCatalog(value) })
      .catch(error => { if (active) setCatalogError((error as Error).message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  const reloadCatalog = async () => {
    setLoading(true)
    setCatalogError("")
    try { setCatalog(await api<PracticeCatalog>("/api/eval-suite/practice/catalog")) }
    catch (error) { setCatalogError((error as Error).message) }
    finally { setLoading(false) }
  }
  const edit = (value: string) => {
    setDraft(value)
    try { localStorage.setItem(draftKey, value) } catch { /* The submitted prompt is also saved on the server. */ }
  }
  const start = async () => {
    setStarting(true)
    edit(prompt)
    try {
      const result = await api<{ id: string }>("/api/eval-suite/practice", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, repeats, case_ids: selected.map(c => c.case_id), ...buyer.selection("practice") }),
      })
      setSubmitted(result.id)
      await refresh()
      await reloadCatalog()
    } catch (error) { toast.error((error as Error).message) }
    finally { setStarting(false) }
  }
  const stop = async () => {
    if (!latest) return
    setStopping(true)
    try {
      await api(`/api/eval-suite/${latest.id}/stop`, { method: "POST" })
      await refresh()
    } catch (error) { toast.error((error as Error).message) }
    finally { setStopping(false) }
  }

  return <div className="mx-auto max-w-6xl space-y-6">
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">Hosted practice</h1>
      <p className="mt-1 text-sm text-muted-foreground">Your prompt negotiating with the practice suppliers on <a className="underline underline-offset-2" href="https://arena.tacto.ai/live" target="_blank" rel="noreferrer">Tacto Arena</a>.</p>
    </div>
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <CardTitle><label htmlFor="practice-prompt">Practice prompt</label></CardTitle>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy || !latest?.prompt_text}
            onClick={() => { if (latest?.prompt_text) edit(latest.prompt_text) }}>
            <RefreshCw /> Restore last run’s prompt
          </Button>
          <Button size="sm" variant="outline" disabled={busy || !sourceA} onClick={() => edit(sourceA)}><Copy /> Copy sandbox A</Button>
          <Button size="sm" variant="outline" disabled={busy || !sourceB} onClick={() => edit(sourceB)}><Copy /> Copy sandbox B</Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <BuyerSettings label="Practice" status={status} value={buyer.selection("practice")}
          onChange={value => buyer.change("practice", value)} disabled={busy} />
        <textarea id="practice-prompt" value={prompt} onChange={event => edit(event.target.value)} disabled={busy} maxLength={100000} spellCheck={false}
          className="min-h-80 w-full resize-y rounded-md border bg-background p-4 font-mono text-sm leading-relaxed outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60" />
        <p className="text-xs text-muted-foreground">The exact prompt shown here is saved with every batch. Uses the same controller as the sandbox.</p>
        <div className="flex flex-wrap items-end gap-4">
          <label className="grid min-w-0 flex-1 basis-64 gap-2 text-sm">Cases
            <select aria-label="Practice cases" value={selectedCase} onChange={event => setSelectedCase(event.target.value)} disabled={busy || loading} className="h-9 w-full min-w-0 max-w-full rounded-md border bg-background px-3">
              <option value="all">All {cases.length || "available"} hosted practice cases</option>
              {cases.map(item => <option key={item.case_id} value={item.case_id}>{item.case_id.replace("case:", "")} · {item.title}</option>)}
            </select>
          </label>
          <label className="grid gap-2 text-sm">Repeats per case
            <select value={repeats} onChange={event => setRepeats(Number(event.target.value))} disabled={busy} className="h-9 rounded-md border bg-background px-3">
              {[1, 2, 3].map(value => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <Button variant="outline" disabled={loading} onClick={reloadCatalog}><RefreshCw className={loading ? "animate-spin" : ""} /> Refresh quota</Button>
        </div>
        {catalogError && <p role="alert" className="text-sm text-red-700">Could not load current practice availability: {catalogError}</p>}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="text-xs text-muted-foreground" aria-live="polite">
            <p>{loading ? "Checking arena availability…" : `${selected.length} cases × ${repeats} repeats = ${count} runs · Up to 6 at once`}</p>
            <p className="mt-1">{catalog?.quota ? `${catalog.quota.left} of ${catalog.quota.limit} practice attempts remaining at last check` : "Practice quota unavailable"} · Buyer model credits</p>
            {insufficient && <p className="mt-1 text-red-700">Reduce the cases or repeats to fit the remaining quota.</p>}
          </div>
          {running ? <Button variant="outline" disabled={stopping || !!latest?.stop_requested} onClick={stop}>
            {stopping || latest?.stop_requested ? <Loader2 className="animate-spin" /> : <Square />}
            {stopping || latest?.stop_requested ? "Stopping and closing sessions…" : "Stop practice run"}
          </Button> : <Button disabled={!canStart} onClick={start}>
            {starting || waiting ? <Loader2 className="animate-spin" /> : <Play />}
            {starting || waiting ? "Starting practice…" : `Run ${count} practice negotiations`}
          </Button>}
        </div>
        <p className="text-xs text-muted-foreground">New runs wait when arena session slots are full. Stopping closes this batch’s open sessions; stopped runs are excluded from averages.</p>
      </CardContent>
    </Card>
    <RunResults evaluation={latest} prompt={prompt} waiting={waiting} variant="practice" cases={selected} repeats={repeats} />
    <p className="text-xs text-muted-foreground">Averages include completed official scores, including zero. The arena leaderboard uses the best result per case.</p>
  </div>
}
