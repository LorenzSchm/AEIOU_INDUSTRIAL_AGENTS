import { useEffect, useState } from "react"

import { api, type Research } from "@/api"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Loading, Notice, PageHeader } from "@/components/workbench"
import { eur, num } from "@/lib/format"
import { useWorkbench } from "@/store"

function Section({ title, tag, children }: { title: string; tag?: string; children: React.ReactNode }) {
  return (
    <Card className="gap-2 shadow-xs">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {tag && (
          <CardAction>
            <Badge variant="secondary">{tag}</Badge>
          </CardAction>
        )}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export function Evidence() {
  const { cases, status } = useWorkbench()
  const all = [...cases.hosted, ...cases.local]
  const [caseId, setCaseId] = useState("case:P01")
  const [research, setResearch] = useState<Research>()
  const [error, setError] = useState("")

  useEffect(() => {
    let alive = true
    setResearch(undefined)
    setError("")
    api<{ research: Research }>(`/api/research/${encodeURIComponent(caseId)}`)
      .then(d => alive && setResearch(d.research))
      .catch(e => alive && setError((e as Error).message))
    return () => {
      alive = false
    }
  }, [caseId])

  const facts = Object.entries(research?.facts ?? {})
  return (
    <>
      <PageHeader eyebrow="Research that moves the conversation" title="Context is your" muted="leverage." sub="Supplier notes, market indices and cost estimates, tied to citable data IDs." />

      <div className="flex flex-wrap items-center gap-3">
        <Select value={caseId} onValueChange={setCaseId}>
          <SelectTrigger className="w-full max-w-lg bg-card [&>span]:truncate" aria-label="Research case">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="max-h-80">
            {all.map(c => (
              <SelectItem key={c.case_id} value={c.case_id}>
                <span className="font-mono text-xs text-muted-foreground">{c.case_id}</span> {c.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-sm text-muted-foreground">{num(facts.length)} relevant facts</span>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>}
      {!research && !error && <Loading>Retrieving the supplier dossier…</Loading>}
      {research && (
        <>
          <Notice>
            Should-cost estimates carry uncertainty. They are not a revealed supplier floor. The buyer data world is frozen at 30 September 2026. Sunday release:{" "}
            {status?.sunday_release ? "loaded" : "not loaded"}.
          </Notice>

          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Supplier correspondence" tag={`${research.documents.length} documents`}>
              {research.documents.length ? (
                <Accordion type="multiple" defaultValue={[research.documents[0].id]}>
                  {research.documents.map(d => (
                    <AccordionItem key={d.id} value={d.id}>
                      <AccordionTrigger className="font-mono text-xs">{d.id}</AccordionTrigger>
                      <AccordionContent>
                        <pre className="max-h-[440px] overflow-auto font-sans text-sm leading-relaxed whitespace-pre-wrap text-muted-foreground [overflow-wrap:anywhere]">{d.text}</pre>
                      </AccordionContent>
                    </AccordionItem>
                  ))}
                </Accordion>
              ) : (
                <p className="text-sm text-muted-foreground">No supplier correspondence for this case.</p>
              )}
            </Section>

            <div className="space-y-4">
              <Section title="Computed market evidence" tag="Code, not guesswork">
                {research.indices.length ? (
                  <div className="divide-y">
                    {research.indices.map(i => (
                      <div key={i.series} className="space-y-1.5 py-3 first:pt-0 last:pb-0">
                        <p className="text-xs font-semibold text-orange-700">
                          {i.series} · weight {num(i.weight * 100)}%
                        </p>
                        <p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">{i.claim}</p>
                        <Badge variant="secondary" className="font-mono">
                          Weighted contribution: {num(i.weighted_change_pct, 2)} pp
                        </Badge>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No comparable index series available.</p>
                )}
              </Section>

              <Section title="Estimated supplier economics">
                {Object.keys(research.estimates).length ? (
                  <div className="divide-y">
                    {Object.entries(research.estimates).map(([sid, v]) => (
                      <div key={sid} className="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
                        <div>
                          <p className="font-mono text-sm font-medium">{sid}</p>
                          <p className="text-xs text-muted-foreground">Source {v.source}</p>
                        </div>
                        <div className="text-right">
                          <p className="font-semibold tabular-nums">{eur(v.full_cost, 2)} / unit</p>
                          <p className="text-xs text-muted-foreground">± {v.confidence_pct}% uncertainty</p>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No supplier-specific cost estimate. Sustainability cost remains uncertain.</p>
                )}
              </Section>
            </div>
          </div>

          <Section title="Facts behind the argument" tag={`${facts.length} sources`}>
            <Accordion type="multiple" className="grid gap-x-6 md:grid-cols-2">
              {facts.slice(0, 60).map(([id, v]) => (
                <AccordionItem key={id} value={id}>
                  <AccordionTrigger className="font-mono text-xs">{id}</AccordionTrigger>
                  <AccordionContent>
                    <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">{JSON.stringify(v, null, 2)}</pre>
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </Section>
        </>
      )}
    </>
  )
}
