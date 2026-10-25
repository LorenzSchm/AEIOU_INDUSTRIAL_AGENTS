import type { Decision, OfferChange } from "@/api"
import { eur, human } from "@/lib/format"

const kindLabels = { public_record: "Public record", supplier_claim: "Supplier claim", inference: "Inference" }

function valueText(value: unknown) {
  if (value == null) return "None"
  return typeof value === "object" ? JSON.stringify(value) : String(value)
}

function CostChange({ label, change }: { label: string; change: OfferChange | null }) {
  return <div className="min-w-0 rounded-md border bg-background p-3">
    <p className="font-medium">{label}</p>
    {change ? <>
      <p className="mt-1 text-base font-semibold tabular-nums">
        {change.cost_delta > 0 ? "+" : ""}{eur(change.cost_delta, 2)}
        <span className="ml-2 text-xs font-normal text-muted-foreground">
          {change.cost_delta > 0 ? "More cost to us" : change.cost_delta < 0 ? "Less cost to us" : "Unchanged cost"}
        </span>
      </p>
      {change.term_changes.length ? <ul className="mt-2 space-y-1">
        {change.term_changes.map(item => <li key={item.term} className="break-words text-xs text-muted-foreground">
          {human(item.term)}: {valueText(item.before)} → {valueText(item.after)}
        </li>)}
      </ul> : <p className="mt-1 text-xs text-muted-foreground">Terms unchanged.</p>}
    </> : <p className="mt-1 text-xs text-muted-foreground">Two valid offer menus are needed to compare.</p>}
  </div>
}

export function SupplierAssessment({ decision }: { decision: Decision }) {
  const assessment = decision.assessment
  if (!assessment) return null
  return <div className="rounded-lg border border-sky-200 bg-sky-50/50 p-4 text-sm dark:border-sky-900 dark:bg-sky-950/20">
    <p className="text-xs font-semibold text-sky-800 dark:text-sky-300">Buyer decision summary · internal</p>
    <p className="mt-1 leading-relaxed">{assessment.next_move}</p>
    <details className="mt-2">
      <summary className="cursor-pointer text-xs font-medium text-muted-foreground focus-visible:outline-ring">Supplier assessment & offer changes</summary>
      <div className="mt-4 space-y-4">
        <div><p className="font-medium">What changed</p>
          <ul className="mt-1 list-disc space-y-1 pl-4">{assessment.changes.map((change, i) => <li key={i}>{change}</li>)}</ul>
        </div>
        <div><p className="font-medium">Response to our last move</p><p className="mt-1">{assessment.response_to_last_move}</p></div>
        <div><p className="font-medium">How we adapt</p><p className="mt-1">{assessment.adaptation}</p></div>
        {decision.economics && <div>
          <div className="grid gap-2 sm:grid-cols-2">
            <CostChange label="Supplier’s latest offer change" change={decision.economics.supplier_change} />
            <CostChange label="Our latest offer change" change={decision.economics.buyer_change} />
          </div>
          <p className="mt-2 text-xs text-muted-foreground">Lowest valid package in each menu. {decision.economics.cost_basis} These are cost comparisons, not negotiation scores.</p>
        </div>}
        <div><p className="font-medium">Evidence & uncertainty</p>
          <ul className="mt-2 space-y-3">{assessment.evidence.map((item, i) => <li key={i}>
            <p className="text-xs text-muted-foreground">{kindLabels[item.kind]} · {item.confidence} confidence</p>
            <p className="mt-0.5">{item.claim}</p>
            <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">Sources: {item.source_refs.join(", ")}</p>
          </li>)}</ul>
        </div>
        <div><p className="font-medium">Still unresolved</p>
          {assessment.open_questions.length ? <ul className="mt-1 list-disc space-y-1 pl-4">
            {assessment.open_questions.map((question, i) => <li key={i}>{question}</li>)}
          </ul> : <p className="mt-1 text-muted-foreground">No open questions recorded.</p>}
        </div>
        {!!decision.numeric_checks?.length && <div><p className="font-medium">Checked calculations</p>
          <ul className="mt-1 space-y-1">{decision.numeric_checks.map((check, i) => <li key={i}>
            “{check.quote}” <span className="text-xs text-muted-foreground">· {check.description}</span>
          </li>)}</ul>
        </div>}
        <p className="text-xs text-muted-foreground">A model-generated summary. The runner checks source references and declared calculations; it cannot verify every statement.</p>
      </div>
    </details>
  </div>
}
