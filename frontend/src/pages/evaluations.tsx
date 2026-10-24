import { useState } from "react"
import { ArrowUpRight, Coins, Euro, FileText, Flame, Home, ScanSearch, ShieldAlert } from "lucide-react"
import { toast } from "sonner"

import { api } from "@/api"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { BenchmarkChart } from "@/components/benchmark-chart"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Empty, Kpi, KpiGrid, Notice, PageHeader, StatusBadge } from "@/components/workbench"
import { go } from "@/hooks/use-hash-route"
import { dateTime, human, num, pct } from "@/lib/format"
import { useWorkbench } from "@/store"

type Tab = "local" | "hosted" | "prompts"

const opponentIcons = [FileText, Flame, Coins, Home, ScanSearch, ShieldAlert]

function EvalTabs({ tab }: { tab: Tab }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Tabs value={tab} onValueChange={v => go(`evaluations/${v}`)}>
        <TabsList>
          <TabsTrigger value="local">Local stress suite</TabsTrigger>
          <TabsTrigger value="hosted">Hosted comparison</TabsTrigger>
          <TabsTrigger value="prompts">Prompt comparison</TabsTrigger>
        </TabsList>
      </Tabs>
      <span className="text-sm text-muted-foreground">Evidence from actual runs</span>
    </div>
  )
}

function TableCard({ title, tag, children }: { title: string; tag?: string; children: React.ReactNode }) {
  return (
    <Card className="gap-0 overflow-hidden py-0 shadow-xs">
      <CardHeader className="border-b px-4 py-3 [.border-b]:pb-3">
        <CardTitle>{title}</CardTitle>
        {tag && (
          <CardAction className="self-center">
            <Badge variant="secondary">{tag}</Badge>
          </CardAction>
        )}
      </CardHeader>
      {children}
    </Card>
  )
}

function LocalSuite() {
  const { benchmarks, status, refresh } = useWorkbench()
  const [index, setIndex] = useState(0)
  const report = benchmarks[index]
  const running = Object.values(status?.jobs ?? {}).some(j => j.kind === "benchmark" && j.status === "running")

  const start = async () => {
    try {
      await api("/api/benchmark", { method: "POST" })
      toast.success("Local benchmark started", { description: "Results will appear here when it finishes." })
      await refresh()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }
  const action = (
    <Button onClick={start} disabled={running}>
      {running ? "Benchmark running…" : "Run local suite"} <ArrowUpRight />
    </Button>
  )

  if (!report)
    return (
      <>
        <PageHeader eyebrow="Test. Learn. Improve." title="Make the advantage" muted="measurable." sub="Pair the agent against a naive baseline across supplier profiles." actions={action} />
        <EvalTabs tab="local" />
        <Empty>No benchmark reports yet.</Empty>
      </>
    )

  const casesPerOpponent = report.rows.length / report.seeds / report.opponents.length
  return (
    <>
      <PageHeader eyebrow="Test. Learn. Improve." title="An edge you can" muted="measure." sub="Paired comparisons, multiple seeds, explicit limits on what the results prove." actions={action} />
      <EvalTabs tab="local" />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Select value={String(index)} onValueChange={v => setIndex(Number(v))}>
          <SelectTrigger className="w-full max-w-md bg-card" aria-label="Evaluation report">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {benchmarks.map((r, i) => (
              <SelectItem key={i} value={String(i)}>
                {r.label} · {r.agent} · {r.matches} matches
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">{dateTime(report.created_at)}</span>
      </div>

      <KpiGrid>
        <Kpi
          highlight
          label="Mean agent score"
          value={pct(report.mean_score)}
          unit="/ 100"
          foot={
            <>
              <span className="font-semibold text-emerald-700">+{num((report.mean_score - report.baseline_score) * 100, 1)} points</span> vs. baseline
            </>
          }
        />
        <Kpi label="Opponent profiles" value={report.opponents.filter(o => o.beaten).length} unit={`/ ${report.opponents.length}`} foot="Outperformed on mean score" />
        <Kpi label="Paired matches" value={report.matches} foot={`${report.seeds} seeds · ${num(casesPerOpponent)} cases per opponent`} />
        <Kpi label="Must-have violations" value={report.must_have_violations} foot={`${report.failures} run failures · ${report.llm_calls} Claude calls`} />
      </KpiGrid>

      <Notice>
        Local results cover our synthetic supplier profiles and the upstream contract simulator. Hosted suppliers and hidden final cases require separate validation.{" "}
        {report.llm_calls === 0
          ? "This report evaluates the deterministic negotiation policy; Claude results are shown in hosted runs."
          : `Claude calls: ${report.llm_calls}; provider errors: ${report.llm_errors}; fallbacks: ${report.fallbacks}.`}
      </Notice>

      <Card className="shadow-xs">
        <CardHeader>
          <CardTitle>Score by opponent profile</CardTitle>
          <CardDescription>AEIOU against the naive baseline, mean case score per profile</CardDescription>
        </CardHeader>
        <CardContent>
          <BenchmarkChart report={report} height={240} />
        </CardContent>
      </Card>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {report.opponents.map((o, i) => {
          const Icon = opponentIcons[i] ?? Euro
          return (
            <Card key={o.id} className="gap-3 py-4 shadow-xs">
              <CardHeader className="px-4">
                <span className="grid size-8 place-items-center rounded-lg bg-orange-50 text-orange-700">
                  <Icon className="size-4" />
                </span>
                <CardAction>
                  <StatusBadge tone={o.beaten ? "success" : "error"}>{o.beaten ? "Baseline beaten" : "Needs work"}</StatusBadge>
                </CardAction>
              </CardHeader>
              <CardContent className="space-y-3 px-4">
                <div>
                  <p className="font-semibold">{o.name}</p>
                  <p className="text-sm text-muted-foreground">{o.description}</p>
                </div>
                <p className="tabular-nums">
                  <span className="text-2xl font-semibold tracking-tight">{pct(o.score)}</span>
                  <span className="ml-1 text-sm text-muted-foreground">/ 100 · baseline {pct(o.baseline_score)}</span>
                </p>
                <Progress value={o.score * 100} className="h-2" />
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>
                    {o.wins} wins · {o.ties} ties · {o.losses} losses
                  </span>
                  <span className={o.delta >= 0 ? "font-medium text-emerald-700" : "font-medium text-red-600"}>
                    {o.delta >= 0 ? "+" : ""}
                    {num(o.delta * 100, 1)} pts
                  </span>
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      <Card className="shadow-xs">
        <CardHeader>
          <CardTitle>Benchmark methodology</CardTitle>
          <CardAction>
            <Badge variant="secondary">Reproducible</Badge>
          </CardAction>
        </CardHeader>
        <CardContent className="space-y-2 text-sm leading-relaxed text-muted-foreground">
          <p>
            Both agents receive identical public case files and supplier conditions for each seed. The supplier's floor stays private. Outcomes are scored with the upstream total-cost
            function. A profile is marked beaten when the agent's mean score exceeds the naive baseline across that profile's cases and seeds.
          </p>
          <p>
            Report: {report.label} · seed range {report.seed_start}–{report.seed_start + report.seeds - 1} ·{" "}
            {report.agent === "claude" ? "Claude with economic guards" : "Deterministic policy, no LLM calls"}. Synthetic T01/T02 cases add quality and coverage constraints.
          </p>
        </CardContent>
      </Card>
    </>
  )
}

function HostedComparison() {
  const { hosted: r, caseTitle, openLaunch } = useWorkbench()
  return (
    <>
      <PageHeader
        eyebrow="Tested on the real arena"
        title="Practice makes the"
        muted="difference."
        sub="Claude-backed AEIOU against the upstream naive buyer, on hosted Tacto suppliers."
        actions={<Button onClick={() => openLaunch({ mode: "hosted" })}>New negotiation</Button>}
      />
      <EvalTabs tab="hosted" />
      <KpiGrid>
        <Kpi highlight label="AEIOU mean score" value={pct(r?.mean_score)} unit="/ 100" foot="Best observed score per case" />
        <Kpi label="Naive baseline" value={pct(r?.baseline_score)} unit="/ 100" foot="Same hosted practice cases" />
        <Kpi label="Cases ahead" value={r?.wins ?? 0} unit={`/ ${r?.paired_cases ?? 0}`} foot={`${r?.ties ?? 0} ties · ${r?.losses ?? 0} losses`} />
        <Kpi label="Cases evaluated" value={r?.cases ?? 0} foot="Includes unsuccessful attempts in the audit" />
      </KpiGrid>
      {r && (
        <Notice>
          {r.source === "snapshot" && "Showing the recorded hosted practice report (reports/hosted-practice.json); its transcripts stay on the machine that played them. "}
          {r.selection} {r.limitation} Hidden final suppliers have not been evaluated.
        </Notice>
      )}
      <TableCard title="Hosted practice · case by case" tag="Observed results">
        {!r?.rows.length ? (
          <Empty>No hosted practice runs yet. Results appear once AEIOU and the baseline have both played a hosted case.</Empty>
        ) : (
          <Table>
            <TableHeader className="bg-muted/60">
              <TableRow className="hover:bg-transparent">
                <TableHead className="h-9 pl-4">Case</TableHead>
                <TableHead className="text-right">AEIOU</TableHead>
                <TableHead className="text-right">Baseline</TableHead>
                <TableHead className="text-right">Improvement</TableHead>
                <TableHead className="text-right">Attempts A / B</TableHead>
                <TableHead className="text-right">Failed attempts</TableHead>
                <TableHead className="w-8 pr-4" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {r.rows.map(v => {
                const open = r.source === "live" && v.run_id
                return (
                <TableRow
                  key={v.case_id}
                  onClick={() => open && go(`run/${v.run_id}`)}
                  className={open ? "group h-10 cursor-pointer hover:bg-orange-50/60" : "h-10"}
                >
                  <TableCell className="pl-4">
                    <div className="max-w-[360px] truncate font-medium">{caseTitle(v.case_id)}</div>
                    <div className="font-mono text-xs text-muted-foreground">{v.case_id}</div>
                  </TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">{pct(v.score)}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">{pct(v.baseline_score)}</TableCell>
                  <TableCell className={`text-right tabular-nums ${v.delta != null && v.delta >= 0 ? "text-emerald-700" : "text-red-600"}`}>
                    {v.delta == null ? "—" : `${v.delta >= 0 ? "+" : ""}${num(v.delta * 100, 1)} pts`}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {v.claude_attempts} / {v.baseline_attempts}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">{v.claude_failures}</TableCell>
                  <TableCell className="pr-4 text-muted-foreground group-hover:text-primary">{open && <ArrowUpRight className="size-4" />}</TableCell>
                </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </TableCard>
    </>
  )
}

const auditChecks: [string, string][] = [
  ["Exact verbatim request sent (runs)", "verbatim_request_runs"],
  ["Acceptances before five repeats", "accepts_before_five_repeats"],
  ["API format repairs", "format_repairs"],
  ["Provider errors", "provider_errors"],
  ["Policy fallbacks", "fallbacks"],
  ["Decisions over 60 seconds", "decisions_over_60s"],
  ["Output tokens", "output_tokens"],
]

function PromptComparison() {
  const { promptReports, prompts } = useWorkbench()
  const [index, setIndex] = useState(0)
  const report = promptReports[index] ?? promptReports[0]
  const stats = report?.summary
  const side = (key: "original" | "adapted", label: string) => {
    const v = stats?.[key]
    return (
      <Kpi
        highlight={key === "original"}
        label={label}
        value={v ? pct(v.mean_score) : "Pending"}
        unit={v ? "/ 100" : undefined}
        foot={v ? `${v.deals}/${v.runs} deals · ${v.failures} failures` : "Matched cases, same buyer model"}
      />
    )
  }

  return (
    <>
      <PageHeader eyebrow="Local prompt lab" title="Your strategy." muted="A fair comparison." sub="The original source, the earlier rewrite, and every paired result." />
      <EvalTabs tab="prompts" />
      <Notice>
        The original source is preserved byte for byte. Both variants receive the same API transport adapter. The earlier rewrite changes tactics; it is retained as the comparison
        baseline. These tests use model API credits and zero hosted arena sessions.
      </Notice>

      {report && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Select value={String(index)} onValueChange={v => setIndex(Number(v))}>
            <SelectTrigger className="w-full max-w-lg bg-card" aria-label="Prompt comparison report">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {promptReports.map((r, i) => (
                <SelectItem key={i} value={String(i)}>
                  {r.label} · {r.conditions.supplier_kind} · {r.status}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-sm text-muted-foreground">Buyer: {report.conditions.buyer_model}</span>
        </div>
      )}

      <KpiGrid>
        {side("original", "Original prompt")}
        {side("adapted", "Earlier rewrite")}
        <Kpi
          label="Matched scenarios"
          value={report?.rows.length ?? "—"}
          foot={report ? `${report.conditions.supplier_kind} supplier · seed ${report.conditions.scenarios[0]?.seed}` : "No comparison yet"}
        />
        <Kpi label="Arena sessions used" value={report?.arena_sessions_used ?? 0} foot="All negotiations run in the local simulator" />
      </KpiGrid>

      {report && (
        <>
          <Notice>
            {report.limitations.join(" ")}{" "}
            {report.conditions.supplier_kind === "language"
              ? `Supplier model held fixed: ${report.conditions.supplier_model}.`
              : "Scripted suppliers respond to numeric packages; this does not test language effects."}
          </Notice>
          <TableCard title="Every paired outcome" tag={report.status}>
            <Table>
              <TableHeader className="bg-muted/60">
                <TableRow className="hover:bg-transparent">
                  <TableHead className="h-9 pl-4">Case / supplier</TableHead>
                  <TableHead>Original</TableHead>
                  <TableHead>Earlier rewrite</TableHead>
                  <TableHead className="pr-4 text-right">Original minus rewrite</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {report.rows.map((row, i) => {
                  const o = row.original
                  const a = row.adapted
                  const both = o?.status === "completed" && a?.status === "completed"
                  const diff = both ? ((o.result?.score ?? 0) - (a.result?.score ?? 0)) * 100 : null
                  return (
                    <TableRow key={i} className="h-11">
                      <TableCell className="pl-4">
                        <div className="font-mono text-xs font-medium">{row.case_id}</div>
                        <div className="text-xs text-muted-foreground">
                          {human(row.opponent)} · seed {row.seed}
                        </div>
                      </TableCell>
                      {[o, a].map((s, j) => (
                        <TableCell key={j}>
                          {s ? (
                            <a href={`#run/${s.run_id}`} className="font-semibold tabular-nums text-orange-700 hover:underline">
                              {s.status === "failed" ? "Failed" : pct(s.result?.score)}
                            </a>
                          ) : (
                            <span className="text-muted-foreground">Running…</span>
                          )}
                          {s && (
                            <div className="text-xs text-muted-foreground">
                              {s.result?.deal ? "Deal" : "No deal"} · {s.audit.format_repairs} repairs
                            </div>
                          )}
                        </TableCell>
                      ))}
                      <TableCell className={`pr-4 text-right tabular-nums ${diff == null ? "text-muted-foreground" : diff >= 0 ? "text-emerald-700" : "text-red-600"}`}>
                        {diff == null ? "—" : `${diff >= 0 ? "+" : ""}${num(diff, 1)} pts`}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableCard>
        </>
      )}

      {stats && (
        <TableCard title="Tactic and execution audit" tag="Observed behavior">
          <p className="border-b px-4 py-2.5 text-sm text-muted-foreground">
            Preserving the text does not guarantee model compliance. An early acceptance is recorded even when the original also instructed acceptance of the third offer.
          </p>
          <Table>
            <TableHeader className="bg-muted/60">
              <TableRow className="hover:bg-transparent">
                <TableHead className="h-9 pl-4">Check</TableHead>
                <TableHead className="text-right">Original</TableHead>
                <TableHead className="pr-4 text-right">Earlier rewrite</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {auditChecks.map(([label, key]) => (
                <TableRow key={key} className="h-9">
                  <TableCell className="pl-4">{label}</TableCell>
                  <TableCell className="text-right tabular-nums">{num(stats.original[key])}</TableCell>
                  <TableCell className="pr-4 text-right tabular-nums">{num(stats.adapted[key])}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}

      {prompts && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            {(
              [
                ["original", "Your original · SYSTEM_PROMPT.md"],
                ["adapted", "Earlier rewrite · prompts.py"],
              ] as const
            ).map(([key, label]) => (
              <Card key={key} className="min-w-0 gap-3 shadow-xs">
                <CardHeader>
                  <CardTitle>{label}</CardTitle>
                  <CardDescription className="font-mono text-xs break-all">SHA-256 {prompts.variants[key].strategy_sha256}</CardDescription>
                </CardHeader>
                <CardContent>
                  <pre className="max-h-[520px] overflow-auto rounded-md border bg-muted/50 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
                    {prompts.variants[key].strategy}
                  </pre>
                </CardContent>
              </Card>
            ))}
          </div>
          <Card className="py-2 shadow-xs">
            <CardContent>
              <Accordion type="single" collapsible>
                <AccordionItem value="transport" className="border-0">
                  <AccordionTrigger>Shared API transport adapter · appended to both prompts</AccordionTrigger>
                  <AccordionContent>
                    <pre className="max-h-[520px] overflow-auto rounded-md border bg-muted/50 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
                      {prompts.transport}
                    </pre>
                  </AccordionContent>
                </AccordionItem>
              </Accordion>
            </CardContent>
          </Card>
        </>
      )}
    </>
  )
}

export function Evaluations({ tab }: { tab: Tab }) {
  if (tab === "hosted") return <HostedComparison />
  if (tab === "prompts") return <PromptComparison />
  return <LocalSuite />
}
