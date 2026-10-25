import { useState } from "react"
import { Play, Search } from "lucide-react"

import type { Case } from "@/api"
import { Badge } from "@/components/ui/badge"
import { Card, CardAction, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Empty } from "@/components/workbench"
import { eur, human, num } from "@/lib/format"
import { useWorkbench, type Scope } from "@/store"

const typeTone: Record<string, string> = {
  price_increase: "bg-orange-50 text-orange-700 border-orange-200",
  renewal: "bg-blue-50 text-blue-700 border-blue-200",
  rfq: "bg-violet-50 text-violet-700 border-violet-200",
  quality: "bg-red-50 text-red-700 border-red-200",
}

export function CasesTable({ cases, mode }: { cases: Case[]; mode: Scope }) {
  const { openLaunch } = useWorkbench()
  const [q, setQ] = useState("")
  const rows = cases.filter(c => `${c.case_id} ${c.title} ${c.suppliers.join(" ")}`.toLowerCase().includes(q.toLowerCase()))
  return (
    <Card className="gap-0 overflow-hidden py-0 shadow-xs">
      <CardHeader className="flex flex-wrap items-center gap-3 border-b px-4 py-3 [.border-b]:pb-3">
        <div>
          <CardTitle>Case library</CardTitle>
          <CardDescription>
            {rows.length} of {cases.length} cases · click a row to launch a negotiation
          </CardDescription>
        </div>
        <CardAction className="relative self-center">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Search cases, suppliers…" aria-label="Search cases" className="h-8 w-64 bg-card pl-8" />
        </CardAction>
      </CardHeader>
      {rows.length === 0 ? (
        <Empty>No case matches “{q}”.</Empty>
      ) : (
        <Table>
          <TableHeader className="bg-muted/60">
            <TableRow className="hover:bg-transparent">
              <TableHead className="h-9 pl-4">Case</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Supplier</TableHead>
              <TableHead className="text-right">Volume / yr</TableHead>
              <TableHead className="text-right">Current price</TableHead>
              <TableHead className="text-right">Fallback</TableHead>
              <TableHead className="text-right">Annual spend</TableHead>
              <TableHead>Goal</TableHead>
              <TableHead className="text-right">Rounds</TableHead>
              <TableHead className="w-8 pr-4" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(c => {
              const b = c.buyer
              const gap = b.current_unit_price ? b.fallback_unit_price / b.current_unit_price - 1 : null
              const launch = () => openLaunch({ mode, caseId: c.case_id })
              return (
                <TableRow key={c.case_id} tabIndex={0} onClick={launch} onKeyDown={e => e.key === "Enter" && launch()} className="group h-10 cursor-pointer hover:bg-orange-50/60">
                  <TableCell className="pl-4 font-mono text-xs text-muted-foreground">{c.case_id.replace("case:", "")}</TableCell>
                  <TableCell className="max-w-[260px] truncate font-medium" title={c.title}>
                    {c.title}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className={`rounded-full capitalize ${typeTone[c.type] ?? ""}`}>
                      {human(c.type)}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{c.suppliers.join(", ").replaceAll("sup:", "")}</TableCell>
                  <TableCell className="text-right tabular-nums">{num(b.need_per_year)}</TableCell>
                  <TableCell className="text-right tabular-nums">{eur(b.current_unit_price, 2)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {eur(b.fallback_unit_price, 2)}
                    {gap != null && <span className={`ml-1.5 text-xs ${gap > 0 ? "text-red-600" : "text-emerald-600"}`}>
                      {gap > 0 ? "+" : ""}
                      {num(gap * 100, 1)}%
                    </span>}
                  </TableCell>
                  <TableCell className="text-right font-medium tabular-nums">{eur(b.current_unit_price == null ? null : b.need_per_year * b.current_unit_price)}</TableCell>
                  <TableCell className="capitalize text-muted-foreground">{b.category_goal}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">{c.round_limit}</TableCell>
                  <TableCell className="pr-4 text-muted-foreground group-hover:text-primary">
                    <Play className="size-3.5" />
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
