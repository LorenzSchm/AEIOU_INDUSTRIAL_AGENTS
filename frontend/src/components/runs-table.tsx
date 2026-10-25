import { ArrowUpRight, CircleCheck, Repeat } from "lucide-react"

import type { RunSummary } from "@/api"
import { Empty, runTone, StatusBadge } from "@/components/workbench"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { go } from "@/hooks/use-hash-route"
import { eur, human, pct, time } from "@/lib/format"
import { useWorkbench } from "@/store"

const agentLabel = (a: string) => (a === "claude" ? "Claude / AEIOU" : human(a))

export function runOutcome(r: RunSummary) {
  if (r.status === "cancelled") return "Cancelled"
  if (r.status === "failed" && r.metrics?.refusals) return "Provider refusal"
  if (r.status !== "completed") return r.status === "running" ? "Running" : "Failed"
  return r.result?.deal ? "Deal reached" : "No deal"
}

export function RunsTable({ rows, title = "Negotiations", compact, emptyMessage }: { rows: RunSummary[]; title?: string; compact?: boolean; emptyMessage?: string }) {
  const { caseTitle, cases } = useWorkbench()
  return (
    <Card className="gap-0 overflow-hidden py-0 shadow-xs">
      <CardHeader className="border-b px-4 py-3 [.border-b]:pb-3">
        <CardTitle>{title}</CardTitle>
        <CardDescription>Every move, every term sheet, every outcome.</CardDescription>
        <CardAction className="self-center">
          {compact ? (
            <Button variant="link" className="h-auto p-0 text-orange-700" onClick={() => go("negotiations")}>
              View all negotiations <ArrowUpRight />
            </Button>
          ) : (
            <Badge variant="secondary">{rows.length} runs</Badge>
          )}
        </CardAction>
      </CardHeader>
      {rows.length === 0 ? (
        <Empty>{emptyMessage ?? "No negotiations in this environment yet. Launch a case to get started."}</Empty>
      ) : (
        <Table>
          <TableHeader className="bg-muted/60">
            <TableRow className="hover:bg-transparent">
              <TableHead className="h-9 pl-4">Negotiation</TableHead>
              <TableHead>Opponent</TableHead>
              <TableHead>Agent</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Case score</TableHead>
              <TableHead className="text-right">Opening cost</TableHead>
              <TableHead className="text-right">Final cost</TableHead>
              <TableHead className="text-right">Saved</TableHead>
              <TableHead className="text-right">Started</TableHead>
              <TableHead className="w-8 pr-4" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(r => {
              const final = r.result?.c_deal ?? r.result?.c_alt
              const saved = r.result?.c_open != null && final != null ? r.result.c_open - final : null
              return (
                <TableRow
                  key={r.id}
                  tabIndex={0}
                  onClick={() => go(`${r.mode === "final" ? "final/" : ""}run/${r.id}`)}
                  onKeyDown={e => e.key === "Enter" && go(`${r.mode === "final" ? "final/" : ""}run/${r.id}`)}
                  className="group h-10 cursor-pointer hover:bg-orange-50/60"
                  aria-label={`Open ${r.title ?? caseTitle(r.case_id)}`}
                >
                  <TableCell className="pl-4">
                    <div className="flex items-center gap-2.5">
                      <span className="grid size-7 shrink-0 place-items-center rounded-md border bg-muted/50 text-muted-foreground">
                        {r.result?.deal ? <CircleCheck className="size-3.5 text-emerald-600" /> : <Repeat className="size-3.5" />}
                      </span>
                      <div className="min-w-0">
                        <div className="max-w-[300px] truncate font-medium">{r.title ?? caseTitle(r.case_id)}</div>
                        <div className="font-mono text-xs text-muted-foreground">{r.case_id}</div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{r.opponent ? (cases.opponents[r.opponent.replace("language:", "")]?.name ?? human(r.opponent)) : r.mode === "final" ? "Tacto final" : "Tacto practice"}</TableCell>
                  <TableCell className="text-muted-foreground">{agentLabel(r.agent)}</TableCell>
                  <TableCell>
                    <StatusBadge tone={runTone(r.status, r.result?.deal)}>{runOutcome(r)}</StatusBadge>
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">{pct(r.result?.score)}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">{eur(r.result?.c_open)}</TableCell>
                  <TableCell className="text-right tabular-nums">{eur(final)}</TableCell>
                  <TableCell className={`text-right tabular-nums ${saved && saved > 0 ? "text-emerald-700" : "text-muted-foreground"}`}>
                    {saved == null ? "—" : eur(saved)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">{time(r.created_at)}</TableCell>
                  <TableCell className="pr-4 text-muted-foreground group-hover:text-primary">
                    <ArrowUpRight className="size-4" />
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Card>
  )
}
