import type { Case, Message } from "../api.ts"

type Terms = Record<string, unknown>
export type Quote = {
  id?: string
  terms: Terms
  cost: number | null
  violations: string[]
  message: number
}

const labels: Record<string, string> = {
  "Unit price (EUR)": "unit_price",
  "Payment terms (days net)": "payment_days",
  "Lead time (days)": "lead_time_days",
  "Volume per year (pcs)": "volume_per_year",
  "Contract term (months)": "contract_months",
  "Price valid from": "effective_date",
  "Index clause": "index_clause",
  Incoterm: "incoterm",
  "Tooling (EUR)": "tooling_eur",
  "Quality commitment (ppm)": "ppm_commitment",
  "Chargeback per complaint (EUR)": "chargeback_eur_per_complaint",
}

// The kit's quote PDFs supply terms omitted from the structured term sheet.
export function quoteStandard(text: string): Terms {
  const standard: Terms = {}
  let section = false
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (line.startsWith("Our standard terms")) section = true
    else if (/^(Offered terms|Terms fixed by your enquiry|Agreed terms)/.test(line)) section = false
    if (!section) continue
    for (const [label, key] of Object.entries(labels)) {
      if (!line.startsWith(`${label} `)) continue
      const value = line.slice(label.length).trim()
      if (value === "none") standard[key] = null
      else if (key === "index_clause") {
        const match = value.match(/^(\S+), (\d+)% material, deadband ([\d.]+) %, base (\S+)/)
        if (match) standard[key] = { series: match[1], material_share: Number(match[2]) / 100, deadband_pct: Number(match[3]), base_month: match[4] }
      } else if (["effective_date", "incoterm"].includes(key)) standard[key] = value
      else if (Number.isFinite(Number(value))) standard[key] = Number(value)
    }
  }
  return standard
}

export function closingTerms(c: Case, terms: Terms, standard: Terms = {}): Terms {
  const b = c.buyer
  const freight = Object.entries(b.freight_eur_per_unit ?? {}).sort((a, z) => z[1] - a[1])
  const plan: Terms = {
    volume_per_year: b.need_per_year,
    contract_months: b.horizon_years == null ? undefined : Math.round(12 * b.horizon_years),
    payment_days: b.reference_payment_days,
    lead_time_days: b.need_lead_time_days,
    incoterm: freight[0]?.[0],
  }
  const complete = { ...plan }
  for (const layer of [standard, c.fixed_terms ?? {}, terms]) {
    for (const [key, value] of Object.entries(layer)) {
      if (value != null || !(key in plan)) complete[key] = value
    }
  }
  return complete
}

// Public cost model from pinned arena_kit/cost.py. Private supplier economics
// (relationship cost) are unavailable live; only the hosted result is official.
export function publicCost(c: Case, t: Terms): number | null {
  const b = c.buyer
  const required = [t.unit_price, t.volume_per_year, t.contract_months, t.payment_days, t.lead_time_days,
    b.horizon_years, b.need_per_year, b.fallback_unit_price, b.interest_rate, b.reference_payment_days,
    b.need_lead_time_days, b.freight_eur_per_unit?.[String(t.incoterm)]]
  if (required.some(v => typeof v !== "number" || !Number.isFinite(v))) return null
  const price = Number(t.unit_price), volume = Number(t.volume_per_year)
  const years = Math.min(Number(t.contract_months) ? Number(t.contract_months) / 12 : 1, b.horizon_years!)
  const committed = volume * years
  const covered = Math.min(volume, b.need_per_year) * years
  const dates = (c.term_options?.effective_date ?? []).filter((v): v is string => typeof v === "string" && !!v).sort()
  const start = c.fixed_terms?.effective_date || dates[0]
  let pre = 0
  if (start && t.effective_date && t.effective_date !== "none") {
    const lag = (Date.parse(String(t.effective_date)) - Date.parse(String(start))) / (86400000 * 365)
    pre = volume * Math.min(Math.max(lag, 0), years)
  }
  const atNewPrice = (committed - pre) * price
  const spend = pre * (b.current_unit_price || price) + atNewPrice
  const uncovered = (b.need_per_year * b.horizon_years! - covered) * b.fallback_unit_price
  const defect = (b.cost_per_defect ?? 0) - Math.min(Number(t.chargeback_eur_per_complaint) || 0, b.cost_per_defect ?? 0)
  let index = 0, indexRisk = 0
  if (t.index_clause && typeof t.index_clause === "object") {
    const clause = t.index_clause as { series: string; material_share: number; deadband_pct: number }
    const path = b.index_paths?.[clause.series] ?? 0
    const moved = Math.max(0, Math.abs(path) - clause.deadband_pct / 100)
    index = atNewPrice * clause.material_share * moved * (path < 0 ? -1 : 1)
    indexRisk = atNewPrice * clause.material_share * (b.index_risk_pct ?? 0) / 100
  }
  const total = spend + uncovered + uncovered * (b.fallback_risk_pct ?? 0) / 100
    + spend * b.interest_rate! * (b.reference_payment_days! - Number(t.payment_days)) / 360
    + committed * b.freight_eur_per_unit![String(t.incoterm)]
    + (b.late_cost_per_day ?? 0) * Math.max(0, Number(t.lead_time_days) - b.need_lead_time_days!)
    + committed * (Number(t.ppm_commitment) || 0) / 1e6 * defect
    + (Number(t.tooling_eur) || 0) + index + indexRisk
  return Number.isFinite(total) ? Math.round((total + Number.EPSILON) * 100) / 100 : null
}

export function violations(c: Case, terms: Terms): string[] {
  return (c.buyer.must_haves ?? []).flatMap(m => {
    if (typeof m === "string") return [`Check: ${m}`]
    const value = terms[m.field], limit = m.value
    const pass = m.op === "==" ? value === limit : m.op === "!=" ? value !== limit
      : typeof value === "number" && typeof limit === "number" && (
        m.op === "<=" ? value <= limit : m.op === "<" ? value < limit
          : m.op === ">=" ? value >= limit : m.op === ">" ? value > limit : false)
    return pass ? [] : [`${m.field.replaceAll("_", " ")} must be ${m.op} ${limit}`]
  })
}

export function threadQuotes(c: Case, events: Message[]) {
  let standard: Terms = {}
  let opening: Quote[] = [], current: Quote[] = [], proposal: Quote[] = []
  for (const [index, message] of events.entries()) {
    const attachment = message.from !== "buyer" && message.attachments?.find(a => a.name.startsWith("quote") && a.text)
    if (attachment) standard = quoteStandard(attachment.text!)
    const quotes = (message.term_sheets ?? []).map(sheet => {
      const terms = closingTerms(c, sheet.terms, standard)
      return { id: sheet.term_sheet_id, terms, cost: publicCost(c, terms), violations: violations(c, terms), message: index + 1 }
    }).filter(q => typeof q.terms.unit_price === "number")
    if (!quotes.length) continue
    if (message.from === "buyer") proposal = quotes
    else {
      if (!opening.length) opening = quotes
      current = quotes
    }
  }
  return { opening, current, proposal }
}

export function lowestQuote(quotes: Quote[]): Quote | undefined {
  return [...quotes].sort((a, b) => Number(!!a.violations.length) - Number(!!b.violations.length)
    || (a.cost ?? Infinity) - (b.cost ?? Infinity))[0]
}

export function openingCost(c: Case, threads: Record<string, Message[]>) {
  const quotes = Object.values(threads).flatMap(events => threadQuotes(c, events).opening)
    .filter(q => !q.violations.length && q.cost != null)
  return lowestQuote(quotes)?.cost ?? null
}

export function alternativeCost(c: Case) {
  const alt = c.buyer.alternative
  if (!alt || alt.kind === "none" || !alt.terms) return null
  const terms = closingTerms(c, alt.terms)
  return violations(c, terms).length ? null : publicCost(c, terms)
}
