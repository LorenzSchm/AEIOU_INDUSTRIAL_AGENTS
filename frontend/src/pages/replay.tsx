import { useEffect, useState } from "react"
import { ArrowLeft, Bot, Building2, CircleCheck, CircleX, Paperclip } from "lucide-react"

import { api, type Message, type Run } from "@/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { runOutcome } from "@/components/runs-table"
import { Empty, Loading, runTone, StatusBadge } from "@/components/workbench"
import { go } from "@/hooks/use-hash-route"
import { eur, human, num, pct } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useWorkbench } from "@/store"

const termNames: Record<string, string> = {
  unit_price: "Unit price",
  payment_days: "Payment",
  lead_time_days: "Lead time",
  contract_months: "Term",
  incoterm: "Incoterm",
  volume_per_year: "Volume / yr",
  ppm_commitment: "Quality PPM",
  chargeback_eur_per_complaint: "Chargeback",
  effective_date: "Effective",
  index_clause: "Index clause",
  tooling_eur: "Tooling",
}

function termValue(k: string, v: unknown) {
  if (v == null) return "none"
  if (typeof v === "object") return JSON.stringify(v)
  if (k === "unit_price" || k === "tooling_eur" || k === "chargeback_eur_per_complaint") return eur(Number(v), 2)
  if (k.endsWith("_days")) return `${v} d`
  if (k === "contract_months") return `${v} mo`
  if (typeof v === "number") return num(v)
  return String(v)
}

function TermSheets({ sheets }: { sheets: NonNullable<Message["term_sheets"]> }) {
  const keys = [...new Set(sheets.flatMap(s => Object.keys(s.terms ?? {})))]
  if (!keys.length) return null
  return (
    <div className="overflow-hidden rounded-md border">
      <Table className="text-xs">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-7 w-24 pl-3">Package</TableHead>
            {keys.map(k => (
              <TableHead key={k} className="h-7 text-right">
                {termNames[k] ?? human(k)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sheets.map((s, i) => (
            <TableRow key={i} className="h-8">
              <TableCell className="pl-3 font-medium">#{i + 1}</TableCell>
              {keys.map(k => (
                <TableCell key={k} className="text-right tabular-nums">
                  {k in (s.terms ?? {}) ? termValue(k, s.terms[k]) : <span className="text-muted-foreground">—</span>}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

export function Mail({ m, index }: { m: Message; index: number }) {
  const buyer = m.from === "buyer"
  return (
    <Card className={cn("gap-0 overflow-hidden py-0 shadow-xs", buyer && "border-orange-200")}>
      <div className={cn("flex items-center gap-2 border-b px-4 py-2 text-xs text-muted-foreground", buyer ? "border-orange-200 bg-orange-50" : "bg-muted/50")}>
        {buyer ? <Bot className="size-3.5 text-primary" /> : <Building2 className="size-3.5" />}
        <span className="font-semibold text-foreground">{buyer ? "AEIOU · Buyer" : m.from}</span>
        <span>Message {index + 1}</span>
        {m.status && (
          <Badge variant="outline" className="ml-auto rounded-full capitalize">
            {human(m.status)}
          </Badge>
        )}
      </div>
      <div className="space-y-3 px-4 py-3">
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90 [overflow-wrap:anywhere]">{m.body || (m.action ? human(m.action.type) : "Awaiting message…")}</p>
        {!!m.term_sheets?.length && <TermSheets sheets={m.term_sheets} />}
        {!!m.claim_results?.length && <p className="text-xs text-muted-foreground">{m.claim_results.map(c => c.result).join(" · ")}</p>}
        {m.attachments?.map(a => (
          <details key={a.name} className="text-xs text-muted-foreground">
            <summary className="flex cursor-pointer items-center gap-1.5 hover:text-foreground">
              <Paperclip className="size-3" /> {a.name}
            </summary>
            <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-muted p-3 font-mono whitespace-pre-wrap">{a.text ?? "Attachment text is available during agent processing."}</pre>
          </details>
        ))}
      </div>
    </Card>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  )
}

export function Replay({ runId }: { runId: string }) {
  const { caseTitle, cases } = useWorkbench()
  const [run, setRun] = useState<Run>()
  const [error, setError] = useState("")
  const [thread, setThread] = useState<string>()

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    setRun(undefined)
    setError("")
    setThread(undefined)
    const load = async () => {
      try {
        const r = await api<Run>(`/api/runs/${encodeURIComponent(runId)}`)
        if (!alive) return
        setRun(r)
        // Keep polling while the agent is still negotiating.
        if (r.status === "running") timer = setTimeout(load, 2500)
      } catch (e) {
        if (alive) setError((e as Error).message)
      }
    }
    load()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [runId])

  const back = (
    <Button variant="ghost" size="sm" className="-ml-2 w-fit text-muted-foreground" onClick={() => go("negotiations")}>
      <ArrowLeft /> Back to negotiations
    </Button>
  )
  if (error)
    return (
      <>
        {back}
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          {error}
          {error === "Run not found" && <span className="text-red-600/80"> · Transcripts live in data/local on the machine that ran them.</span>}
        </div>
      </>
    )
  if (!run) return <Loading>Loading replay…</Loading>

  const threads = Object.keys(run.threads ?? {})
  const active = thread ?? threads[0]
  const events = run.threads?.[active] ?? []
  const result = run.result
  const breakdown = Object.entries(result?.cost_breakdown ?? {}).filter(([, v]) => v)

  return (
    <>
      {back}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="font-mono text-xs font-semibold text-orange-700">
            {run.case_id} · {run.mode.toUpperCase()}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{run.case?.title ?? caseTitle(run.case_id)}</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {run.agent === "claude" ? "Claude / AEIOU" : human(run.agent)} · {run.opponent ? (cases.opponents[run.opponent]?.name ?? human(run.opponent)) : "Hosted Tacto supplier"}
          </p>
        </div>
        <StatusBadge tone={runTone(run.status, result?.deal)}>{run.status === "completed" && result?.deal ? "Agreement reached" : runOutcome(run)}</StatusBadge>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="min-w-0 space-y-3">
          {threads.length > 1 && (
            <Tabs value={active} onValueChange={setThread}>
              <TabsList>
                {threads.map(s => (
                  <TabsTrigger key={s} value={s}>
                    {s} · {run.threads![s].length}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          )}
          {run.error && <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{run.error}</div>}
          {events.length ? events.map((m, i) => <Mail key={i} m={m} index={i} />) : <Empty>The agent is opening the session. This view refreshes automatically.</Empty>}
        </section>

        <aside className="lg:sticky lg:top-4 lg:self-start">
          <Card className="gap-4 shadow-xs">
            <CardHeader>
              <p className="text-xs font-semibold text-orange-700">The outcome</p>
              <CardTitle className="text-4xl font-semibold tracking-tight tabular-nums">
                {pct(result?.score)}
                <span className="ml-1 text-sm font-medium text-muted-foreground">/ 100</span>
              </CardTitle>
              <p className="text-sm text-muted-foreground">{result?.deal ? "Accepted package score" : run.status === "running" ? "Negotiation in progress" : "Alternative / no-deal score"}</p>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Row label="Opening cost" value={eur(result?.c_open)} />
                <Row label="Deal cost" value={eur(result?.c_deal)} />
                <Row label="Alternative" value={eur(result?.c_alt)} />
                <Row label="Best attainable (post-run)" value={eur(result?.c_best)} />
              </div>
              <p className="flex items-center gap-1.5 text-sm">
                {result?.must_haves_ok === false ? <CircleX className="size-4 text-red-600" /> : <CircleCheck className="size-4 text-emerald-600" />}
                {result?.must_haves_ok === true ? "All must-haves satisfied" : result?.must_haves_ok === false ? "Must-have violation" : "Must-haves checked before acceptance"}
              </p>
              {breakdown.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-semibold text-muted-foreground">Total cost components</p>
                  {breakdown.map(([k, v]) => (
                    <Row key={k} label={human(k)} value={eur(v)} />
                  ))}
                </div>
              )}
              {run.metrics && (
                <div>
                  <p className="mb-1 text-xs font-semibold text-muted-foreground">Execution</p>
                  <Row label="Runtime" value={`${run.metrics.elapsed_s}s`} />
                  <Row label="Claude calls / fallbacks" value={`${run.metrics.calls} / ${run.metrics.fallbacks}`} />
                  <Row label="Tokens in / out" value={`${num(run.metrics.input_tokens)} / ${num(run.metrics.output_tokens)}`} />
                </div>
              )}
              {!!run.decisions?.length && (
                <div>
                  <p className="mb-1 text-xs font-semibold text-muted-foreground">Decision log</p>
                  <div className="max-h-56 overflow-auto">
                    {run.decisions.map((d, i) => (
                      <div key={i} className="border-b py-1 text-xs text-muted-foreground last:border-0">
                        <span className="font-medium text-foreground">{d.source}</span> · {d.action} · {d.elapsed_s}s
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </aside>
      </div>
    </>
  )
}
