import { ArrowUpRight, Plus } from "lucide-react"
import { RunsTable } from "@/components/runs-table"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Kpi, KpiGrid, PageHeader, ScopeTabs } from "@/components/workbench"
import { go } from "@/hooks/use-hash-route"
import { num, pct } from "@/lib/format"
import { useWorkbench } from "@/store"

export function useScopedRuns() {
  const { runs, scope } = useWorkbench()
  return runs.filter(r => r.mode === scope && r.agent !== "baseline")
}

export function RunKpis() {
  const { scope, localDashboard, status } = useWorkbench()
  const scoped = useScopedRuns()
  const version = localDashboard?.versions.find(v => v.current)
  const completed = scoped.filter(r => r.status === "completed" && r.result?.score != null)
  const score = scope === "local" ? version?.summary.mean_score : null
  return <KpiGrid>
    <Kpi highlight label={scope === "local" ? "Current version mean" : "Hosted attempts"} value={scope === "local" ? pct(score) : scoped.length}
      unit={scope === "local" ? "/ 100" : undefined} foot={scope === "local" ? `${version?.summary.completed ?? 0} completed with current prompt and simulator` : "Compare case and buyer versions in Evaluations"} />
    <Kpi label="Attempts in history" value={scoped.length} foot={`${scoped.filter(r => r.status === "failed").length} failed · ${scoped.filter(r => r.status === "cancelled").length} cancelled`} />
    <Kpi label="Completed negotiations" value={completed.length} foot={`${completed.filter(r => r.result?.deal).length} deals · ${scoped.filter(r => r.status === "running").length} in progress`} />
    <Kpi label="Evidence at hand" value={num(status?.facts)} foot={`${Object.keys(status?.tables ?? {}).length} tables · supplier letters & contracts`} />
  </KpiGrid>
}

export function Overview() {
  const { localDashboard, openLaunch, scope } = useWorkbench()
  const scoped = useScopedRuns()
  const version = localDashboard?.versions.find(v => v.current)
  return <>
    <PageHeader eyebrow="Your negotiation command center" title="Close with confidence." sub="Negotiations, execution reliability and comparable evidence."
      actions={<><Button variant="outline" onClick={() => go("pipeline")}>Deal pipeline <ArrowUpRight /></Button><Button onClick={() => openLaunch({ mode: scope })}>New negotiation <Plus /></Button></>} />
    <ScopeTabs /><RunKpis />
    <Card><CardHeader><CardTitle>{scope === "local" ? "Current local evaluation" : "Hosted evidence"}</CardTitle>
      <CardDescription>{scope === "local" ? "Three repeats per public case. Current-version scores only; earlier attempts remain in history." : "Real arena outcomes are kept separate from local simulations."}</CardDescription></CardHeader>
      <CardContent className="space-y-4">
        {scope === "local" && <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{localDashboard?.profiles.map(p => {
          const s = version?.profiles.find(v => v.id === p.id)?.summary
          return <div className="rounded-lg border p-3" key={p.id}><p className="font-medium">{p.name}</p><p className="mt-2 text-2xl font-semibold">{pct(s?.mean_score)}</p><p className="text-xs text-muted-foreground">{s?.completed ?? 0} completed · {s?.failures ?? 0} failed</p></div>
        })}</div>}
        <Button variant="outline" onClick={() => go(`evaluations/${scope}`)}>Open evaluations and all attempts <ArrowUpRight /></Button>
      </CardContent>
    </Card>
    <RunsTable rows={scoped.slice(0, 6)} title="Recent negotiations" compact />
  </>
}
