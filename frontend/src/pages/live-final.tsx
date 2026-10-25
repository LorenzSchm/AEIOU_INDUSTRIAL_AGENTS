import { ArrowUpRight, Flag } from "lucide-react"

import type { FinalRunnerStatus } from "@/api"
import { RunsTable } from "@/components/runs-table"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Kpi, KpiGrid, PageHeader, StatusBadge } from "@/components/workbench"
import { go } from "@/hooks/use-hash-route"
import { useWorkbench } from "@/store"

const labels: Record<FinalRunnerStatus["state"], string> = {
  not_started: "Not started", starting: "Starting", waiting: "Waiting for Arena",
  running: "Runner active", stopped: "Runner stopped", unknown: "Status unconfirmed",
  completed: "Final complete", error: "Needs attention",
}

function tone(state: FinalRunnerStatus["state"]) {
  if (state === "error" || state === "stopped") return "error" as const
  if (state === "running" || state === "completed") return "success" as const
  return "neutral" as const
}

export function FinalRunnerBanner() {
  const { status } = useWorkbench()
  const runner = status?.final_runner
  if (!runner || runner.state === "not_started") return null
  return <div className="flex flex-wrap items-center gap-3 rounded-xl border border-orange-200 bg-orange-50/50 px-4 py-3">
    <Flag className="size-4 shrink-0 text-orange-700" />
    <div className="min-w-0 flex-1">
      <p className="text-sm font-semibold">Live final · {labels[runner.state]}</p>
      <p className="text-sm text-muted-foreground">{runner.message}</p>
    </div>
    <Button variant="outline" size="sm" onClick={() => go("final")}>View live final <ArrowUpRight /></Button>
  </div>
}

export function LiveFinal() {
  const { status, runs } = useWorkbench()
  const runner = status?.final_runner
  const finalRuns = runs.filter(run => run.mode === "final")
  const active = finalRuns.filter(run => run.status === "running").length
  const completed = finalRuns.filter(run => run.status === "completed").length
  const model = status?.models.find(model => model.id === runner?.model)?.label ?? runner?.model ?? "—"
  return <>
    <PageHeader eyebrow="Tacto Arena" title="Live final." sub="Runner status, final negotiations and conversation replays. Updates automatically every five seconds." />
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3">Final runner {runner && <StatusBadge tone={tone(runner.state)}>{labels[runner.state]}</StatusBadge>}</CardTitle>
        <CardDescription>{runner?.message ?? "Checking the final runner…"}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-sm text-muted-foreground">
        <p>{model} · Prompt: {runner?.prompt_revision ?? "—"} · {runner?.sunday_release ? "Sunday release loaded" : "Sunday release not confirmed"}</p>
        {runner?.waves && runner?.parallel ? <p>{runner.waves} waves · Up to {runner.parallel} negotiations at once</p> : null}
        {runner?.last_activity_at && <p>Last runner activity: {new Date(runner.last_activity_at).toLocaleString("en-GB", { timeZone: "Europe/Berlin" })} (Berlin)</p>}
      </CardContent>
    </Card>
    <KpiGrid>
      <Kpi label="Final sessions" value={finalRuns.length} foot="Cases appear after Arena releases them" />
      <Kpi label="Negotiating now" value={active} foot="Active final sessions" />
      <Kpi label="Completed" value={completed} foot={`${finalRuns.filter(run => run.result?.deal).length} deals reached`} />
      <Kpi label="Waves with sessions" value={new Set(finalRuns.map(run => run.wave).filter(wave => wave != null)).size} foot={`${runner?.waves ?? "—"} waves planned`} />
    </KpiGrid>
    <RunsTable rows={finalRuns} title="Final negotiations" emptyMessage={runner?.state === "waiting" ? "Arena has not opened the final yet. Sessions and their conversations will appear here automatically when each wave starts." : "No final sessions recorded yet. The runner status above shows whether it is ready."} />
  </>
}
