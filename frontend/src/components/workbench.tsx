import { Loader2 } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import { useWorkbench, type Scope } from "@/store"

export function PageHeader({ eyebrow, title, muted, sub, actions }: { eyebrow: string; title: string; muted?: string; sub: string; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <p className="text-xs font-semibold text-orange-700">{eyebrow}</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          {title} {muted && <span className="text-muted-foreground">{muted}</span>}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">{sub}</p>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

export function Kpi({ label, value, unit, foot, highlight }: { label: string; value: React.ReactNode; unit?: string; foot: React.ReactNode; highlight?: boolean }) {
  return (
    <Card className={cn("gap-2 py-4 shadow-xs", highlight && "border-orange-200 bg-gradient-to-b from-orange-50 to-card")}>
      <CardHeader className="px-4">
        <CardDescription className="font-medium">{label}</CardDescription>
      </CardHeader>
      <CardContent className="px-4">
        <div className="text-3xl font-semibold tracking-tight tabular-nums">
          {value}
          {unit && <span className="ml-1 text-sm font-medium text-muted-foreground">{unit}</span>}
        </div>
        <div className="mt-1 text-xs text-muted-foreground">{foot}</div>
      </CardContent>
    </Card>
  )
}

export function KpiGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">{children}</div>
}

const tones = {
  success: "border-emerald-200 bg-emerald-50 text-emerald-700",
  warning: "border-amber-200 bg-amber-50 text-amber-700",
  error: "border-red-200 bg-red-50 text-red-700",
  neutral: "border-border bg-muted text-muted-foreground",
} as const

export function StatusBadge({ tone, children }: { tone: keyof typeof tones; children: React.ReactNode }) {
  return (
    <Badge variant="outline" className={cn("gap-1 rounded-full font-medium", tones[tone])}>
      {tone === "warning" && children !== "Walked away" ? <Loader2 className="size-3 animate-spin" /> : <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </Badge>
  )
}

export function runTone(status: string, deal?: boolean): keyof typeof tones {
  if (status === "running") return "warning"
  if (status === "failed") return "error"
  return deal ? "success" : "neutral"
}

export function ScopeTabs() {
  const { scope, setScope } = useWorkbench()
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Tabs value={scope} onValueChange={v => setScope(v as Scope)}>
        <TabsList>
          <TabsTrigger value="hosted">Hosted practice</TabsTrigger>
          <TabsTrigger value="local">Local laboratory</TabsTrigger>
        </TabsList>
      </Tabs>
      <span className="text-sm text-muted-foreground">
        {scope === "hosted" ? "Real Tacto suppliers · practice results" : "Synthetic opponents · isolated from hosted results"}
      </span>
    </div>
  )
}

export function Notice({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-l-[3px] border-l-primary bg-card px-4 py-3 text-sm leading-relaxed text-muted-foreground shadow-xs">{children}</div>
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="px-6 py-10 text-center text-sm text-muted-foreground">{children}</div>
}

export function Loading({ children = "Loading…" }: { children?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 px-1 py-10 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" />
      {children}
    </div>
  )
}
