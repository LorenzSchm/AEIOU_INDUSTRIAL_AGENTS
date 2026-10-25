import { type EvalBatch } from "@/api"
import { Card, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Empty } from "@/components/workbench"
import { dateTime, pct } from "@/lib/format"

type Variant = "a" | "b"
const labels = {
  queued: "Queued", running: "Running", completed: "Completed",
  failed: "Failed", cancelled: "Stopped", not_started: "Not started", waiting: "Waiting for slot",
}

export function RunResults({ evaluation, prompt, waiting, variant, cases, repeats }: {
  evaluation?: EvalBatch | null; prompt: string; waiting: boolean; variant: Variant | "practice"
  cases: { case_id: string; title: string }[]; repeats: number
}) {
  const rows = evaluation?.rows ?? []
  const label = variant === "practice" ? "Practice" : `Prompt ${variant.toUpperCase()}`
  const active = rows.filter(row => row.status === "running").length
  const queued = rows.filter(row => row.status === "queued" || row.status === "waiting").length
  const resultCases = rows.length ? [...new Map(rows.map(row => [row.case_id, row])).values()] : cases
  const caseAverages = resultCases.map(item => {
    const caseRows = rows.filter(row => row.case_id === item.case_id)
    const scores = caseRows.flatMap(row => row.status === "completed" && row.score != null && Number.isFinite(row.score) ? [row.score] : [])
    return {
      ...item,
      completed: scores.length,
      planned: caseRows.length || repeats,
      mean: scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : null,
    }
  })
  return <Card className="gap-0 overflow-hidden py-0 shadow-xs">
    <CardHeader className="flex flex-wrap items-center justify-between gap-4 border-b py-5">
      <div>
        <CardTitle>{label} runs</CardTitle>
        {evaluation && <p className="mt-1 text-xs text-muted-foreground">{evaluation.model}{evaluation.effort && ` · ${evaluation.effort} effort`}</p>}
        <p className="mt-1 text-xs text-muted-foreground" role="status">
          {waiting ? "Starting negotiations…" : evaluation ? `${evaluation.completed} of ${rows.length} completed` : "No runs yet"}
          {evaluation?.status === "running" && !waiting && ` · ${active} running · ${queued} queued`}
        </p>
      </div>
      <div className="text-right" aria-live="polite" aria-atomic="true">
        <p className="text-xs text-muted-foreground">Overall mean</p>
        <p className="mt-1 text-3xl font-semibold tracking-tight tabular-nums">
          {pct(evaluation?.mean_score)}<span className="ml-1 text-sm font-normal text-muted-foreground">/ 100</span>
        </p>
      </div>
    </CardHeader>
    {evaluation && evaluation.prompt_text !== prompt && <p className="border-b bg-amber-50 px-6 py-3 text-xs text-amber-800">Prompt edited. These scores belong to the saved prompt from the last run.</p>}
    <div className={`grid grid-cols-2 gap-3 border-b bg-muted/20 px-6 py-4 ${variant === "practice" ? "sm:grid-cols-5" : ""}`} aria-live="polite" aria-label={`${label} case averages`}>
      {caseAverages.map(item => <div key={item.case_id} title={item.title}>
        <p className="text-xs font-medium text-muted-foreground">{item.case_id.replace("case:", "")} average</p>
        <p className="mt-1 text-2xl font-semibold tracking-tight tabular-nums">
          {pct(item.mean)}<span className="ml-1 text-xs font-normal text-muted-foreground">/ 100</span>
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{item.completed} of {item.planned} runs scored</p>
      </div>)}
    </div>
    {rows.length ? <Table>
      <TableHeader><TableRow>
        <TableHead className="w-14 pl-6">Run</TableHead>
        <TableHead>Case</TableHead>
        <TableHead>Repeat</TableHead>
        <TableHead>Status</TableHead>
        <TableHead className="pr-6 text-right">Score</TableHead>
      </TableRow></TableHeader>
      <TableBody>{rows.map((row, index) => <TableRow key={`${row.case_id}-${row.repeat}`}>
        <TableCell className="pl-6 text-muted-foreground tabular-nums">{String(index + 1).padStart(2, "0")}</TableCell>
        <TableCell title={row.title}>
          {row.run_id
            ? <a href={`#evaluations/run/${row.run_id}`} aria-label={`${label}, ${row.case_id}, repeat ${row.repeat}: ${row.status === "running" ? "watch live" : "view conversation"}`} className="font-medium text-primary underline underline-offset-2">{row.case_id.replace("case:", "")}{row.status === "running" && <span className="ml-2 inline-block size-1.5 animate-pulse rounded-full bg-emerald-500" />}</a>
            : <span className="font-medium">{row.case_id.replace("case:", "")}</span>}
        </TableCell>
        <TableCell className="text-muted-foreground tabular-nums">{row.repeat}</TableCell>
        <TableCell>
          <span className={row.status === "failed" ? "text-red-700" : "text-muted-foreground"}>{labels[row.status]}</span>
          {row.error && <p className="mt-1 max-w-md whitespace-normal text-xs text-red-700">{row.error}</p>}
          {row.wait_reason && <p className="mt-1 text-xs text-muted-foreground">{row.wait_reason}</p>}
          {row.cleanup_error && <p className="mt-1 text-xs text-red-700">{row.cleanup_error}</p>}
        </TableCell>
        <TableCell className="pr-6 text-right font-medium tabular-nums">{pct(row.score)}</TableCell>
      </TableRow>)}</TableBody>
    </Table> : <Empty>{waiting ? "Preparing the first hosted run…" : "Run this prompt to see its scores here."}</Empty>}
    <div className="space-y-2 border-t px-6 py-3 text-xs text-muted-foreground">
      <p>Click a case for live messages, prices, and terms.</p>
      {evaluation && <details>
        <summary className="cursor-pointer hover:text-foreground">Saved prompt · {dateTime(evaluation.created_at)}</summary>
        <p className="mt-2">Model: {evaluation.model}{evaluation.effort && ` · ${evaluation.effort} effort`}{evaluation.speed && ` · ${evaluation.speed} mode`}</p>
        <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-muted p-3 whitespace-pre-wrap">{evaluation.prompt_text}</pre>
      </details>}
      {evaluation?.stop_reason && <p className="text-red-700" role="status">{evaluation.stop_reason}</p>}
    </div>
  </Card>
}

