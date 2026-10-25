import { useEffect, useRef, useState } from "react"
import { ArrowUpRight, ChevronRight, CircleCheck, CircleX, Flag, Inbox, Play, Plus, Repeat } from "lucide-react"

import { api, type Deal, type Run, type Stage } from "@/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Empty, Loading, PageHeader, ScopeTabs } from "@/components/workbench"
import { go } from "@/hooks/use-hash-route"
import { eur, human, num, pct, time } from "@/lib/format"
import { cn } from "@/lib/utils"
import { Mail } from "@/pages/replay"
import { useWorkbench } from "@/store"

type Column = { key: Stage; label: string; hint: string; icon: typeof Inbox; tone: string }

// Briefing runs share the first column: the deal exists, the supplier has not quoted yet.
const columns: Column[] = [
  { key: "opening", label: "Opening quote", hint: "Where we started", icon: Inbox, tone: "text-slate-600 bg-slate-100" },
  { key: "negotiating", label: "Negotiating", hint: "Counter-offers in flight", icon: Repeat, tone: "text-orange-700 bg-orange-50" },
  { key: "final_offer", label: "Final offer", hint: "Supplier's last move", icon: Flag, tone: "text-amber-700 bg-amber-50" },
  { key: "closed", label: "Deal closed", hint: "Where we wanted to go", icon: CircleCheck, tone: "text-emerald-700 bg-emerald-50" },
  { key: "no_deal", label: "No deal", hint: "Walked away or stopped", icon: CircleX, tone: "text-red-700 bg-red-50" },
]
const columnOf = (s: Stage): Stage => (s === "briefing" ? "opening" : s)

// Whole euros for large unit prices keep the card's price line on one row.
const price = (v: number | null | undefined) => eur(v, v != null && v >= 100 ? 0 : 2)

// Share of the gap between the supplier's opening quote and our target that is already closed.
function gapClosed(d: Deal) {
  if (d.start == null || d.now == null || d.target == null || d.start <= d.target) return null
  return Math.max(0, Math.min(1, (d.start - d.now) / (d.start - d.target)))
}

function Sparkline({ asks, bids, target }: { asks: number[]; bids: number[]; target?: number }) {
  const all = [...asks, ...bids, ...(target != null ? [target] : [])]
  if (asks.length < 2 && bids.length < 2) return null
  const lo = Math.min(...all)
  const hi = Math.max(...all)
  const n = Math.max(asks.length, bids.length, 2)
  const x = (i: number) => (i / (n - 1)) * 100
  const y = (v: number) => (hi === lo ? 16 : 30 - ((v - lo) / (hi - lo)) * 28)
  const path = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")
  return (
    <svg viewBox="0 0 100 32" preserveAspectRatio="none" className="h-9 w-full overflow-visible" aria-hidden>
      {target != null && <line x1="0" x2="100" y1={y(target)} y2={y(target)} className="stroke-emerald-500" strokeDasharray="2 2" strokeWidth="1" vectorEffect="non-scaling-stroke" />}
      {bids.length > 1 && <path d={path(bids)} fill="none" className="stroke-slate-400" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />}
      {asks.length > 1 && <path d={path(asks)} fill="none" className="stroke-primary" strokeWidth="2" vectorEffect="non-scaling-stroke" />}
    </svg>
  )
}

function PriceTrack({ d, large }: { d: Deal; large?: boolean }) {
  const points = [d.start, d.now, d.bid, d.target, d.walk_away].filter((v): v is number => v != null)
  if (d.start == null || points.length < 2) return null
  const lo = Math.min(...points)
  const hi = Math.max(...points)
  const at = (v: number) => (hi === lo ? 50 : ((v - lo) / (hi - lo)) * 100)
  const marker = (v: number | null | undefined, cls: string, label: string) =>
    v == null ? null : <span title={`${label} ${eur(v, 2)}`} className={cn("absolute top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-card", cls)} style={{ left: `${at(v)}%` }} />
  return (
    <div className="space-y-1.5">
      <div className={cn("relative mx-1.5 rounded-full bg-muted", large ? "h-2" : "h-1.5")}>
        {d.now != null && <div className="absolute inset-y-0 rounded-full bg-orange-200" style={{ left: `${at(d.now)}%`, right: `${100 - at(d.start)}%` }} />}
        {d.walk_away != null && <span title={`Alternative cost ${eur(d.walk_away, 2)}`} className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-red-400" style={{ left: `${at(d.walk_away)}%` }} />}
        {marker(d.target, "size-2.5 bg-emerald-500", "Target")}
        {marker(d.bid, "size-2 bg-slate-500", "Our bid")}
        {marker(d.start, "size-2.5 bg-slate-300", "Opening quote")}
        {marker(d.now, cn("bg-primary", large ? "size-3.5" : "size-3"), d.stage === "closed" ? "Agreed" : "Current ask")}
      </div>
      <div className="flex flex-wrap justify-between gap-x-2 text-[11px] text-muted-foreground tabular-nums">
        <span className="whitespace-nowrap text-emerald-700">Target {price(d.target)}</span>
        <span className="whitespace-nowrap">
          {price(d.start)} → <span className="font-semibold text-foreground">{price(d.now)}</span>
        </span>
      </div>
    </div>
  )
}

function DealCard({ d, moved, onOpen }: { d: Deal; moved: boolean; onOpen: () => void }) {
  const live = d.status === "running"
  const closed = gapClosed(d)
  return (
    <button
      onClick={onOpen}
      className={cn(
        "w-full space-y-2.5 rounded-lg border bg-card p-3 text-left shadow-xs transition hover:border-orange-300 hover:shadow-sm focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none",
        "animate-in fade-in-0 slide-in-from-left-3 duration-500",
        moved && "border-orange-300 ring-3 ring-orange-200",
      )}
    >
      <div className="flex items-center gap-1.5 text-xs">
        <span className="font-mono text-muted-foreground">{d.case_id.replace("case:", "")}</span>
        {live && (
          <Badge variant="outline" className="h-5 gap-1 rounded-full border-orange-200 bg-orange-50 px-1.5 text-[11px] text-orange-700">
            <span className="size-1.5 animate-pulse rounded-full bg-primary" /> {d.stage === "briefing" ? "Preparing" : "Live"}
          </Badge>
        )}
        <span className="ml-auto text-muted-foreground">{time(d.created_at)}</span>
      </div>
      <div>
        <p className="line-clamp-2 text-sm leading-snug font-medium">{d.title}</p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground">
          {d.supplier_id?.replace("sup:", "Supplier ") ?? "Awaiting supplier"}
          {d.suppliers > 1 && ` +${d.suppliers - 1}`} · {d.agent === "claude" ? "Claude" : human(d.agent)}
        </p>
      </div>
      <Sparkline asks={d.asks} bids={d.bids} target={d.target} />
      <PriceTrack d={d} />
      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
        {d.round_limit ? (
          <>
            <span className="tabular-nums">
              Round {d.rounds}/{d.round_limit}
            </span>
            <Progress value={(d.rounds / d.round_limit) * 100} className="h-1 flex-1" />
          </>
        ) : (
          <span className="flex-1" />
        )}
        {d.score != null ? (
          <span className="font-semibold text-foreground tabular-nums">{pct(d.score)}</span>
        ) : closed != null ? (
          <span className="tabular-nums">{num(closed * 100)}% of gap</span>
        ) : null}
      </div>
    </button>
  )
}

function Stepper({ deals, active, onSelect }: { deals: Deal[]; active?: Stage; onSelect: (s?: Stage) => void }) {
  return (
    <div className="flex items-stretch gap-1 overflow-x-auto rounded-xl border bg-card p-1.5 shadow-xs">
      {columns.map((c, i) => {
        const list = deals.filter(d => columnOf(d.stage) === c.key)
        const live = list.filter(d => d.status === "running").length
        return (
          <div key={c.key} className="flex min-w-[140px] flex-1 items-center gap-1">
            <button
              onClick={() => onSelect(active === c.key ? undefined : c.key)}
              className={cn("flex flex-1 items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition hover:bg-muted/60", active === c.key && "bg-muted")}
            >
              <span className={cn("grid size-8 shrink-0 place-items-center rounded-md", c.tone)}>
                <c.icon className="size-4" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs text-muted-foreground" title={c.hint}>
                  {c.label}
                </span>
                <span className="flex items-baseline gap-1.5">
                  <span className="text-lg font-semibold tabular-nums">{list.length}</span>
                  {live > 0 && <span className="text-[11px] font-medium text-orange-700">{live} live</span>}
                </span>
              </span>
            </button>
            {i < columns.length - 1 && <ChevronRight className="size-4 shrink-0 text-muted-foreground/50" />}
          </div>
        )
      })}
    </div>
  )
}

function Correspondence({ deal, onClose }: { deal?: Deal; onClose: () => void }) {
  const [run, setRun] = useState<Run>()
  const [thread, setThread] = useState<string>()
  useEffect(() => {
    if (!deal) return
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    setRun(undefined)
    setThread(undefined)
    const load = async () => {
      try {
        const r = await api<Run>(`/api/runs/${deal.id}`)
        if (!alive) return
        setRun(r)
        if (r.status === "running") timer = setTimeout(load, 1500)
      } catch {
        // The card stays visible; the next pipeline poll will show the latest state.
      }
    }
    load()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [deal?.id])

  const threads = Object.keys(run?.threads ?? {})
  const active = thread ?? (deal?.supplier_id && threads.includes(deal.supplier_id) ? deal.supplier_id : threads[0])
  const messages = run?.threads?.[active ?? ""] ?? []
  return (
    <Sheet open={!!deal} onOpenChange={o => !o && onClose()}>
      <SheetContent className="gap-0 p-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl data-[side=right]:xl:max-w-3xl">
        {deal && (
          <>
            <SheetHeader className="space-y-3 border-b p-5">
              <div className="space-y-1">
                <p className="font-mono text-xs font-semibold text-orange-700">
                  {deal.case_id} · {deal.mode.toUpperCase()}
                </p>
                <SheetTitle className="text-lg leading-snug">{deal.title}</SheetTitle>
                <SheetDescription>
                  {columns.find(c => c.key === columnOf(deal.stage))?.label} · round {deal.rounds}
                  {deal.round_limit ? ` of ${deal.round_limit}` : ""} · {deal.agent === "claude" ? "Claude / AEIOU" : human(deal.agent)}
                </SheetDescription>
              </div>
              <PriceTrack d={deal} large />
              <div className="grid grid-cols-3 gap-2 text-center text-xs">
                {[
                  ["Started at", eur(deal.start, 2)],
                  [deal.stage === "closed" ? "Agreed" : "Now", eur(deal.now, 2)],
                  ["Target", eur(deal.target, 2)],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-md border bg-muted/40 px-2 py-1.5">
                    <div className="text-muted-foreground">{k}</div>
                    <div className="font-semibold tabular-nums">{v}</div>
                  </div>
                ))}
              </div>
              <Button variant="outline" size="sm" className="w-fit" onClick={() => go(`run/${deal.id}`)}>
                Open full replay <ArrowUpRight />
              </Button>
            </SheetHeader>
            <div className="flex-1 space-y-3 overflow-y-auto bg-muted/30 p-5">
              {threads.length > 1 && (
                <Tabs value={active} onValueChange={setThread}>
                  <TabsList>
                    {threads.map(s => (
                      <TabsTrigger key={s} value={s}>
                        {s}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                </Tabs>
              )}
              {!run ? <Loading>Loading correspondence…</Loading> : messages.length ? messages.map((m, i) => <Mail key={i} m={m} index={i} />) : <Empty>No messages yet. The agent is preparing.</Empty>}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}

export function Pipeline() {
  const { scope, openLaunch } = useWorkbench()
  const [deals, setDeals] = useState<Deal[]>()
  const [updated, setUpdated] = useState<Date>()
  const [focus, setFocus] = useState<Stage>()
  const [openId, setOpenId] = useState<string>()
  const [moved, setMoved] = useState<Set<string>>(new Set())
  const stages = useRef(new Map<string, Stage>())

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      try {
        const r = await api<{ deals: Deal[] }>("/api/pipeline")
        if (!alive) return
        // Highlight cards that changed column since the previous poll.
        const changed = r.deals.filter(d => stages.current.has(d.id) && columnOf(stages.current.get(d.id)!) !== columnOf(d.stage)).map(d => d.id)
        r.deals.forEach(d => stages.current.set(d.id, d.stage))
        if (changed.length) {
          setMoved(new Set(changed))
          setTimeout(() => alive && setMoved(new Set()), 1800)
        }
        setDeals(r.deals)
        setUpdated(new Date())
      } finally {
        // Poll faster while any deal is live.
        if (alive) timer = setTimeout(load, document.hidden ? 5000 : 1500)
      }
    }
    load()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [])

  const scoped = (deals ?? []).filter(d => d.mode === scope && d.agent !== "baseline")
  const live = scoped.filter(d => d.status === "running").length
  const shown = focus ? columns.filter(c => c.key === focus) : columns
  const openDeal = deals?.find(d => d.id === openId)

  return (
    <>
      <PageHeader
        eyebrow="Deal pipeline"
        title="Every deal, where it stands."
        muted="Live."
        sub="Where each negotiation started, where it is now and where we want it to land. Click a deal to read the correspondence."
        actions={
          <>
            <Button variant="outline" onClick={() => openLaunch({ mode: "local", live: true, stay: true })}>
              <Play /> Live demo
            </Button>
            <Button onClick={() => openLaunch({ mode: "local", stay: true })}>
              New negotiation <Plus />
            </Button>
          </>
        }
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ScopeTabs />
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span className={cn("size-2 rounded-full", live ? "animate-pulse bg-primary" : "bg-emerald-500")} />
          {live ? `${live} negotiation${live > 1 ? "s" : ""} live` : "Up to date"}
          {updated && ` · updated ${updated.toLocaleTimeString("en-GB")}`}
        </span>
        <span className="inline-flex items-center gap-3">
          <span className="inline-flex items-center gap-1">
            <span className="size-2 rounded-full bg-slate-300" /> Opening quote
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="size-2 rounded-full bg-primary" /> Current ask
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="size-2 rounded-full bg-slate-500" /> Our bid
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="size-2 rounded-full bg-emerald-500" /> Target
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="h-2.5 w-0.5 bg-red-400" /> Walk-away
          </span>
        </span>
      </div>

      {!deals ? (
        <Loading>Loading the pipeline…</Loading>
      ) : (
        <>
          <Stepper deals={scoped} active={focus} onSelect={setFocus} />
          {scoped.length === 0 ? (
            <div className="rounded-xl border border-dashed bg-card">
              <Empty>
                No {scope === "hosted" ? "hosted" : "local"} negotiations yet.{" "}
                <Button variant="link" className="h-auto p-0 text-orange-700" onClick={() => openLaunch({ mode: "local", live: true, stay: true })}>
                  Start a live demo
                </Button>{" "}
                to watch a deal move through the stages.
              </Empty>
            </div>
          ) : (
            <div className="-mx-1 overflow-x-auto px-1 pb-2">
              <div className={cn("grid gap-3", focus ? "grid-cols-1" : "min-w-[900px] grid-cols-5")}>
                {shown.map(c => {
                  const list = scoped.filter(d => columnOf(d.stage) === c.key)
                  return (
                    <section key={c.key} className="flex min-w-0 flex-col gap-2 rounded-xl bg-muted/50 p-2">
                      <header className="flex items-center gap-2 px-1 pt-1" title={c.hint}>
                        <c.icon className={cn("size-4 shrink-0", c.tone.split(" ")[0])} />
                        <h2 className="truncate text-sm font-semibold">{c.label}</h2>
                        <Badge variant="secondary" className="ml-auto h-5 rounded-full px-1.5 tabular-nums">
                          {list.length}
                        </Badge>
                      </header>
                      <div className={cn("grid gap-2", focus && "sm:grid-cols-2 xl:grid-cols-4")}>
                        {list.length ? (
                          list.slice(0, focus ? 60 : 12).map(d => <DealCard key={`${d.id}-${columnOf(d.stage)}`} d={d} moved={moved.has(d.id)} onOpen={() => setOpenId(d.id)} />)
                        ) : (
                          <p className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">No deals here</p>
                        )}
                        {!focus && list.length > 12 && (
                          <Button variant="ghost" size="sm" onClick={() => setFocus(c.key)}>
                            +{list.length - 12} more
                          </Button>
                        )}
                      </div>
                    </section>
                  )
                })}
              </div>
            </div>
          )}
        </>
      )}
      <Correspondence deal={openDeal} onClose={() => setOpenId(undefined)} />
    </>
  )
}
