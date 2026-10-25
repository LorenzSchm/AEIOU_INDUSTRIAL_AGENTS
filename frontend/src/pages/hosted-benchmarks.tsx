import { useEffect, useState, type ReactNode } from "react"
import { api, type HostedBenchmarks } from "@/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Empty, Kpi, KpiGrid, Notice, PageHeader } from "@/components/workbench"
import { human, pct } from "@/lib/format"
import { RunsTable } from "@/components/runs-table"

export function HostedBenchmarksPage({ navigation }: { navigation: ReactNode }) {
  const [archive, setArchive] = useState(false)
  const [data, setData] = useState<HostedBenchmarks>()
  const [error, setError] = useState("")
  useEffect(() => {
    let active = true
    const load = () => api<HostedBenchmarks>(`/api/hosted-benchmarks?archive=${archive}`)
      .then(r => { if (active) { setData(r); setError("") } })
      .catch(e => { if (active) setError((e as Error).message) })
    void load()
    const timer = setInterval(() => { if (!document.hidden) void load() }, 5000)
    return () => { active = false; clearInterval(timer) }
  }, [archive])
  return <>
    <PageHeader eyebrow="Hosted arena" title="Hosted benchmarks" sub="Actual Tacto outcomes, kept separate from local simulations. No hosted sessions start from this page." />
    {navigation}
    <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={archive} onChange={e => { setData(undefined); setArchive(e.target.checked) }} />Show pre-reset hosted history ({data?.historical_attempts ?? 0} attempts)</label>
    {error && <Notice>{error}</Notice>}
    <KpiGrid><Kpi label="Recorded attempts" value={data?.summary.attempts ?? 0} foot={archive ? "Historical policies · before history reset" : "Fresh hosted history"} />
      <Kpi label="Completed" value={data?.summary.completed ?? 0} foot={`${data?.summary.failures ?? 0} execution failures`} />
      <Kpi label="Deals" value={data?.summary.deals ?? 0} foot="See each case and prompt version below" />
      <Kpi label="Cases" value={data?.cases ?? 0} foot="No aggregate score across different buyer versions" /></KpiGrid>
    <Notice>{data?.selection} {archive && data?.limitations}</Notice>
    <Card className="overflow-hidden py-0"><CardHeader className="border-b py-4"><CardTitle>Case and buyer version</CardTitle></CardHeader>
      {!data?.rows.length ? <Empty>No hosted runs since the reset. Iterate locally first; prior hosted evidence is available with the history switch above.</Empty> :
        <Table><TableHeader><TableRow><TableHead>Case / buyer</TableHead><TableHead>Attempts</TableHead><TableHead>Mean</TableHead><TableHead>Best observed</TableHead><TableHead>Deals / completed</TableHead><TableHead>Failures</TableHead><TableHead>Replays</TableHead></TableRow></TableHeader><TableBody>
          {data.rows.map(r => <TableRow key={`${r.case_id}-${r.agent}-${r.model}-${r.prompt_hash}`}>
            <TableCell>{r.case_id} · {human(r.agent)}<p className="text-xs text-muted-foreground">{r.model} · {r.prompt_hash.slice(0, 10)}</p></TableCell>
            <TableCell>{r.summary.attempts}</TableCell><TableCell>{pct(r.summary.mean_score)}</TableCell><TableCell>{pct(r.summary.best_score)}</TableCell><TableCell>{r.summary.deals} / {r.summary.completed}</TableCell><TableCell>{r.summary.failures}</TableCell>
            <TableCell><div className="flex flex-wrap gap-2">{r.run_ids.map((id, i) => <a className="text-primary underline" key={id} href={`#run/${id}`}>#{i + 1}</a>)}</div></TableCell>
          </TableRow>)}
        </TableBody></Table>}
    </Card>
    <Card><CardHeader><CardTitle>What the hosted arena returns</CardTitle></CardHeader><CardContent className="space-y-3 text-sm">
      <ul className="list-disc space-y-1 pl-5">{data?.available_data.map(item => <li key={item}>{item}</li>)}</ul>
      <p className="text-muted-foreground">{data?.unavailable_data}</p>
    </CardContent></Card>
    {!!data?.attempts.length && <RunsTable rows={data.attempts} title="Hosted attempt history" />}
  </>
}
