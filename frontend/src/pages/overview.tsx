import { ArrowUpRight, Plus } from "lucide-react"

import { BenchmarkChart } from "@/components/benchmark-chart"
import { RunsTable } from "@/components/runs-table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Kpi, KpiGrid, PageHeader, ScopeTabs } from "@/components/workbench"
import { go } from "@/hooks/use-hash-route"
import { eur, num, pct } from "@/lib/format"
import { useWorkbench } from "@/store"

export function useScopedRuns() {
  const { runs, scope } = useWorkbench()
  return runs.filter(r => r.mode === scope && r.agent !== "baseline")
}

export function RunKpis() {
  const { scope, benchmarks, status, hosted } = useWorkbench()
  const scoped = useScopedRuns()
  const done = scoped.filter(r => r.status === "completed" && r.result?.score != null)
  const facts = <Kpi label="Evidence at hand" value={num(status?.facts)} foot={`${Object.keys(status?.tables ?? {}).length} tables · supplier letters & contracts`} />

  // Hosted transcripts live on the machine that played them; use the committed report until local ones exist.
  if (scope === "hosted" && !done.length && hosted?.paired_cases) {
    const deals = hosted.rows.filter(r => r.deal).length
    return (
      <KpiGrid>
        <Kpi
          highlight
          label="Mean case score"
          value={pct(hosted.mean_score)}
          unit="/ 100"
          foot={
            <>
              <span className="font-semibold text-emerald-700">+{num(((hosted.mean_score ?? 0) - (hosted.baseline_score ?? 0)) * 100, 1)} pts</span> vs. naive baseline
            </>
          }
        />
        <Kpi label="Cases ahead of baseline" value={hosted.wins} unit={`/ ${hosted.paired_cases}`} foot={`${hosted.ties} ties · ${hosted.losses} losses`} />
        <Kpi label="Deals reached" value={deals} unit={`/ ${hosted.cases}`} foot="Recorded hosted practice report" />
        {facts}
      </KpiGrid>
    )
  }

  const last = benchmarks[0]
  const local = scope === "local" && last
  const avg = local ? last.mean_score : done.length ? done.reduce((n, r) => n + (r.result!.score ?? 0), 0) / done.length : null
  const saved = done.reduce((n, r) => {
    const open = r.result?.c_open ?? 0
    return n + Math.max(0, open - (r.result?.c_deal ?? r.result?.c_alt ?? open))
  }, 0)
  return (
    <KpiGrid>
      <Kpi
        highlight
        label="Mean case score"
        value={pct(avg)}
        unit="/ 100"
        foot={
          local ? (
            <>
              <span className="font-semibold text-emerald-700">+{num((last.mean_score - last.baseline_score) * 100, 1)} pts</span> vs. naive baseline
            </>
          ) : (
            `Across ${done.length} completed practice runs`
          )
        }
      />
      <Kpi label="Negotiated savings" value={eur(saved)} foot="Opening cost less deal / alternative cost" />
      <Kpi label="Deals reached" value={done.filter(r => r.result?.deal).length} foot={`${done.length} completed · ${scoped.filter(r => r.status === "running").length} in progress`} />
      {facts}
    </KpiGrid>
  )
}

export function Overview() {
  const { benchmarks, openLaunch, scope } = useWorkbench()
  const scoped = useScopedRuns()
  const report = benchmarks[0]
  return (
    <>
      <PageHeader
        eyebrow="Your negotiation command center"
        title="Close with confidence."
        sub="An autonomous buyer. Every fact checked. Every package priced."
        actions={
          <>
            <Button variant="outline" onClick={() => go("pipeline")}>
              Deal pipeline <ArrowUpRight />
            </Button>
            <Button onClick={() => openLaunch({ mode: scope })}>
              New negotiation <Plus />
            </Button>
          </>
        }
      />
      <ScopeTabs />
      <RunKpis />

      <Card className="shadow-xs">
        <CardHeader>
          <CardTitle>A measurable negotiating edge</CardTitle>
          <CardDescription>Local stress suite · {report?.matches ?? 0} paired matches · mean case score per supplier profile</CardDescription>
          <CardAction>
            <Badge variant="secondary" className="font-mono">
              {report?.label ?? "No evaluation yet"}
            </Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          <BenchmarkChart report={report} height={340} />
        </CardContent>
        <CardFooter className="justify-between border-t text-xs text-muted-foreground [.border-t]:pt-4">
          <span>{report ? `${report.opponents.filter(o => o.beaten).length} of ${report.opponents.length} opponent profiles outperformed` : "Benchmark pending"}</span>
          <Button variant="link" className="h-auto p-0 text-xs text-orange-700" onClick={() => go("evaluations")}>
            {report?.must_have_violations ?? 0} must-have violations · all evaluations <ArrowUpRight />
          </Button>
        </CardFooter>
      </Card>

      <RunsTable rows={scoped.slice(0, 6)} title="Recent negotiations" compact />
    </>
  )
}
