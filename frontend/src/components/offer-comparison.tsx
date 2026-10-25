import type { Case, Message, Result } from "@/api"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { eur, num } from "@/lib/format"
import { lowestQuote, threadQuotes, type Quote } from "@/lib/offer-comparison"

export function CostPosition({ opening, current, target, targetLabel, currentLabel }: {
  opening: number | null | undefined
  current: number | null | undefined
  target: number | null | undefined
  targetLabel: string
  currentLabel: string
}) {
  if ([opening, current, target].some(v => v == null || !Number.isFinite(v)) || opening! <= target!) return null
  const position = Math.max(0, Math.min(100, (opening! - current!) / (opening! - target!) * 100))
  return <div className="space-y-2">
    <div className="flex justify-between gap-3 text-xs text-muted-foreground">
      <span>Opening <strong className="block font-medium text-foreground tabular-nums">{eur(opening)}</strong></span>
      <span className="text-right">{targetLabel}<strong className="block font-medium text-foreground tabular-nums">{eur(target)}</strong></span>
    </div>
    <div className="relative h-2 rounded-full bg-muted" role="img" aria-label={`Total cost: opening ${eur(opening)}, ${currentLabel} ${eur(current)}, ${targetLabel} ${eur(target)}`}>
      <div className="h-full rounded-full bg-primary" style={{ width: `${position}%` }} />
      <span className="absolute -top-1 h-4 w-0.5 -translate-x-1/2 bg-foreground" style={{ left: `${position}%` }} />
    </div>
    <p className="text-xs text-muted-foreground">{currentLabel} <strong className="font-semibold text-foreground tabular-nums">{eur(current)}</strong></p>
  </div>
}

function QuoteTerms({ quote }: { quote: Quote }) {
  const t = quote.terms
  const details = [
    t.payment_days != null && `${t.payment_days} days payment`,
    t.lead_time_days != null && `${t.lead_time_days} days delivery`,
    t.incoterm,
    t.contract_months != null && `${t.contract_months} months`,
    t.volume_per_year != null && `${num(Number(t.volume_per_year))} / year`,
    t.effective_date && t.effective_date !== "none" && `from ${t.effective_date}`,
    t.ppm_commitment != null && `${num(Number(t.ppm_commitment))} PPM`,
    Number(t.tooling_eur) > 0 && `${eur(Number(t.tooling_eur))} tooling`,
    Number(t.chargeback_eur_per_complaint) > 0 && `${eur(Number(t.chargeback_eur_per_complaint))} chargeback`,
    t.index_clause && `Index: ${JSON.stringify(t.index_clause)}`,
  ].filter(Boolean).join(" · ")
  return <>
    <span className="font-semibold text-foreground tabular-nums">{eur(Number(t.unit_price), 2)} / unit</span>
    <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{details}</p>
    {!!quote.violations.length && <p className="mt-1 text-xs text-amber-700">Outside requirements: {quote.violations.join("; ")}.</p>}
  </>
}

export function OfferComparison({ negotiationCase, events, supplier, result, running }: {
  negotiationCase: Case; events: Message[]; supplier: string; result?: Result; running: boolean
}) {
  const c = negotiationCase
  const quotes = threadQuotes(c, events)
  if (!quotes.opening.length) return null
  const opening = lowestQuote(quotes.opening), current = lowestQuote(quotes.current), proposal = lowestQuote(quotes.proposal)
  const validComparison = opening && current && proposal && ![opening, current, proposal].some(q => q.violations.length)
  const savings = opening?.cost != null && current?.cost != null ? opening.cost - current.cost : null
  const awarded = result?.deal && result.supplier_id === supplier
  const matchesDeal = (quote: Quote) => awarded && Object.entries(quote.terms).every(([key, value]) =>
    JSON.stringify(value ?? null) === JSON.stringify(result.terms?.[key] ?? null))
  const rows = [
    ...quotes.opening.map((quote, i) => ({ label: `Opening quote${quotes.opening.length > 1 ? ` ${i + 1}` : ""}`, quote, proposal: false })),
    ...quotes.current.map((quote, i) => ({ label: awarded && result.offer_id === quote.id ? "Accepted deal" : `Latest supplier quote${quotes.current.length > 1 ? ` ${i + 1}` : ""}`, quote, proposal: false })),
    ...quotes.proposal.map((quote, i) => ({ label: `Your latest proposal${quotes.proposal.length > 1 ? ` ${i + 1}` : ""}`, quote, proposal: true })),
  ]
  return <Card className="gap-4 shadow-xs">
    <CardHeader className="gap-1">
      <CardTitle className="text-base">Price & terms · {supplier.replace(/^sup:/, "")}</CardTitle>
      <p className="text-xs text-muted-foreground">Need {num(c.buyer.need_per_year)} units / year{c.buyer.horizon_years != null && ` for ${c.buyer.horizon_years} years`} · lower total cost is better</p>
    </CardHeader>
    <CardContent className="space-y-4">
      {running && validComparison && <CostPosition opening={opening.cost} current={current.cost} target={proposal.cost} targetLabel="Your proposal · unaccepted" currentLabel="Latest supplier quote" />}
      <Table>
        <TableHeader><TableRow>
          <TableHead className="w-36 pl-0">Offer</TableHead>
          <TableHead>Terms</TableHead>
          <TableHead className="pr-0 text-right">Est. total cost</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {rows.map(({ label, quote, proposal: isProposal }) => <TableRow key={`${label}-${quote.message}`}>
            <TableCell className="py-3 pl-0 align-top whitespace-normal">
              <p className="text-xs font-medium">{label}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">{isProposal ? matchesDeal(quote) ? "Matched agreed terms" : "Buyer’s requested terms" : `Message ${quote.message}`}</p>
            </TableCell>
            <TableCell className="min-w-44 py-3 whitespace-normal"><QuoteTerms quote={quote} /></TableCell>
            <TableCell className="py-3 pr-0 text-right align-top font-medium tabular-nums">{quote.cost == null ? "Unavailable" : eur(quote.cost, 2)}</TableCell>
          </TableRow>)}
        </TableBody>
      </Table>
      {!quotes.proposal.length && <p className="text-xs text-muted-foreground">{running ? "Your target will appear when the buyer sends a priced proposal." : "No priced buyer proposal was sent in this thread."}</p>}
      {savings != null && !opening?.violations.length && !current?.violations.length && <p className="text-sm">
        <span className="font-semibold tabular-nums">{eur(Math.abs(savings), 2)}</span> {savings >= 0 ? "below" : "above"} this supplier’s opening cost.
      </p>}
      <p className="text-xs leading-relaxed text-muted-foreground">Estimates use the quoted terms, supplier standard terms, and the kit’s cost formula over the full buying period.{running && " The buyer’s proposal is an ask, not a confirmed attainable price."}
        {["stabilise", "secure_supply"].includes(c.buyer.category_goal) && " Hidden supplier relationship costs are excluded; the final hosted result may differ."}
      </p>
    </CardContent>
  </Card>
}
