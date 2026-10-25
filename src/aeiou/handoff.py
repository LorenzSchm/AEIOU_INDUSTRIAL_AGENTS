"""Draft procurement handoff assembled from recorded outcomes, never an order."""

from .store import clean


def procurement_handoff(run):
    result = run.get("result") or {}
    agreement = run.get("status") == "completed" and result.get("deal") is True
    offer_id, supplier_id = result.get("offer_id"), result.get("supplier_id")
    evidence = [
        {"supplier_id": sid, "message_index": i, "offer_id": sheet.get("term_sheet_id")}
        for sid, events in run.get("threads", {}).items()
        if sid == supplier_id
        for i, message in enumerate(events)
        if message.get("from") != "buyer"
        for sheet in message.get("term_sheets", [])
        if offer_id and sheet.get("term_sheet_id") == offer_id
    ]
    cost = result.get("c_deal") if agreement else result.get("c_alt")
    opening = result.get("c_open")
    return clean(
        {
            "status": "draft_for_review" if agreement else "no_agreement",
            "simulation_only": run.get("mode") == "local",
            "operational_commitment": False,
            "run_id": run["id"],
            "case_id": run["case_id"],
            "supplier_id": supplier_id if agreement else None,
            "offer_id": offer_id if agreement else None,
            "agreed_terms": result.get("terms", {}) if agreement else {},
            "must_haves_ok": result.get("must_haves_ok") if agreement else None,
            "economics": {
                "opening_cost": opening,
                "outcome_cost": cost,
                "alternative_cost": result.get("c_alt"),
                "improvement_vs_opening": opening - cost if opening is not None and cost is not None else None,
                "cost_breakdown": result.get("cost_breakdown", {}) if agreement else {},
            },
            "evidence": evidence[-2:] if agreement else [],
            "review_items": [
                "Review the complete accepted terms and supplier confirmation.",
                "Validate cost assumptions and contractual details with purchasing.",
                "Obtain authorized approval before issuing any purchase order.",
            ]
            if agreement
            else ["No accepted agreement is available for procurement approval."],
            "limitation": "Draft from recorded evidence. No purchase order, legal review or ERP write has occurred.",
        }
    )
