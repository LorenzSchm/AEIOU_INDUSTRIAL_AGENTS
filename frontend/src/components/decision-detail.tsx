import type { Decision } from "@/api"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { eur, human } from "@/lib/format"

export function DecisionDetail({ decision }: { decision: Decision }) {
  const diagnostic = decision.walk_away
  const support = decision.learned_response_support
  const supplierSupport = support?.suppliers[decision.supplier_id]
  const usableMenus = supplierSupport?.menus.filter(menu =>
    menu.status === "advisory_only" && Object.keys(menu.predictions ?? {}).length > 0,
  ).length ?? 0
  return (
    <>
      {support && (
        <div className="mt-2 rounded-md border bg-muted/40 p-2 text-foreground">
          <p className="font-medium">
            {support.mode === "shadow" ? "Learned forecasts · audit only" : "Learned decision support · experimental"}
          </p>
          {support.mode === "shadow" && (
            <p>Recorded for evaluation. These forecasts were not sent to the buyer model or used to select its action.</p>
          )}
          <p>
            {support.status === "unavailable"
              ? "Unavailable for this decision; the buyer used its other decision checks."
              : supplierSupport?.status === "skipped"
                ? "Skipped: execution rules or package limits took priority."
                : usableMenus > 0
                  ? `${usableMenus} proposed menus received ${support.mode === "shadow" ? "evaluation" : "advisory"} forecasts.`
                  : "No supported forecast for these proposals; evidence and decision rules remain in control."}
          </p>
          {supplierSupport?.preferred_menu && (
            <p>
              {support.mode === "shadow" ? "Recorded" : "Advisory"} preference: {supplierSupport.preferred_menu.join(" + ")}.
              {support.mode !== "shadow" && " Supplier approval is still required."}
            </p>
          )}
          {supplierSupport?.business_records && (
            <p>
              Business context available: {supplierSupport.business_records.source_count} sourced records
              through {supplierSupport.business_records.cutoff}.
            </p>
          )}
          <Accordion type="single" collapsible>
            <AccordionItem value="learned-support">
              <AccordionTrigger className="py-1 text-xs">Support details</AccordionTrigger>
              <AccordionContent>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap">{JSON.stringify(support, null, 2)}</pre>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </div>
      )}
      {decision.action === "walk_away" && (
        <div className="mt-2 space-y-2 rounded-md border bg-muted/40 p-2 text-foreground">
          <p className="font-medium">
            {diagnostic
              ? human(diagnostic.reason_code)
              : "This older run has no recorded walk-away diagnosis."}
          </p>
          {diagnostic?.model_reason && <p>{diagnostic.model_reason}</p>}
          {diagnostic?.rounds_left && (
            <p>
              Messages remaining:{" "}
              {Object.entries(diagnostic.rounds_left)
                .map(([sid, rounds]) => `${sid}: ${rounds}`)
                .join(" · ")}
            </p>
          )}
          {Object.entries(diagnostic?.offers ?? {}).flatMap(([sid, offers]) =>
            offers.map((offer) => (
              <p key={`${sid}-${offer.term_sheet_id}`}>
                {offer.term_sheet_id}: {offer.repeat_count} repeats ·{" "}
                {eur(offer.cost)} · must-haves{" "}
                {offer.must_haves_ok ? "met" : "not met"}
              </p>
            )),
          )}
          {diagnostic && (
            <Accordion type="single" collapsible>
              <AccordionItem value="walk">
                <AccordionTrigger className="py-1 text-xs">
                  Full decision audit
                </AccordionTrigger>
                <AccordionContent>
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap">
                    {JSON.stringify(diagnostic, null, 2)}
                  </pre>
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          )}
        </div>
      )}
    </>
  )
}
