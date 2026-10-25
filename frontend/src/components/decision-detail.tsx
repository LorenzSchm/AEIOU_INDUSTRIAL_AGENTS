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
  return (
    <>
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
