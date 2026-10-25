"""Observable business signals and complete-package features for offline learning.

No case/supplier identity, final score, private cost or future message enters the
input. Signals are literal evidence with negation handling, not an LLM's guesses.
"""

import copy
import json
import math
import re
from pathlib import Path

from arena_kit.cost import closing_terms, cost_breakdown, total_cost
from arena_kit.docs import standard_terms

from .response_learning import _date_delta, _sheets, extract_run, features
from .response_refinement import enrich_history

PATTERNS = {
    "quick_payment": r"quick payment|faster payment|early payment|cash.flow|liquidity|liquidit\w*|schnell\w* zahlung",
    "free_capacity": r"free capacity|spare capacity|idle capacity|freie\w* kapazit\w*",
    "capacity_pressure": r"fully booked|at capacity|capacity constraint|ausgelastet|overtime",
    "internal_approval": r"clarify.*internally|internal approval|management approval|intern\w* kl[aä]r\w*",
    "competition": r"rival quote|competing offer|another supplier|competitive quote|wettbewerb\w*",
    "evidence_accepted": r"data.*(?:holds|valid)|checked.*took it into account|fair point",
    "price_resistance": r"hold firm|limit of what|no further|cannot reduce|can't reduce|price.*frozen",
    "closing_warning": r"final offer|last offer|withdraw|letztes angebot",
    "duration_interest": r"longer.*(?:contract|agreement)|long.term commitment|planning security",
    "delivery_tradeoff": r"more time.*delivery|delivery flexibility|shorter lead|expedit\w*",
}
PACKAGE_FIELDS = (
    "present",
    "price_gap",
    "payment_delta",
    "duration_delta",
    "date_delta",
    "lead_delta",
    "volume_delta",
    "ppm_delta",
    "chargeback_delta",
    "tooling_delta",
    "freight_delta",
    "index_cost_delta",
    "index_risk_delta",
    "direct_cost_gap",
)
CONTEXT_FIELDS = ("supplier_count", "relationship_exposure", "uncovered_exposure", "quality_exposure")
EXTRA_FEATURES = (
    tuple(PATTERNS) + CONTEXT_FIELDS + tuple(f"package_{i}_{field}" for i in range(3) for field in PACKAGE_FIELDS)
)


def business_signals(text):
    """Return matched clauses and their literal positive/negative polarity.

    Negated mentions are retained as evidence but never become positive flags.
    This is intentionally a small auditable extractor, not semantic certainty.
    """
    evidence = []
    for sentence in re.split(r"[.!?;\n]+|\bbut\b", text, flags=re.I):
        for name, pattern in PATTERNS.items():
            match = re.search(pattern, sentence, re.I)
            if not match:
                continue
            before = sentence[max(0, match.start() - 50) : match.start()]
            after = sentence[match.end() : match.end() + 35]
            negated = bool(re.search(r"\b(?:no|not|never|without|kein\w*|nicht|don't|do not)\b", before, re.I))
            negated |= bool(re.match(r"\s+(?:is|are|does)\s+not\b", after, re.I))
            evidence.append({"signal": name, "text": sentence.strip(), "polarity": -1 if negated else 1})
    return evidence


def complete_features(case, prefix, proposal):
    quotes = [m for m in prefix if m.get("from") not in {"buyer", "arena"} and _sheets(m)]
    if not quotes:
        raise ValueError("A visible supplier quote is required")
    std = standard_terms(prefix)
    reference = closing_terms(case, _sheets(quotes[-1])[0], std)
    cost = total_cost(case, reference, std)
    price, need = reference["unit_price"], case["buyer"]["need_per_year"]
    breakdown = cost_breakdown(case, reference, std)
    evidence = business_signals(quotes[-1].get("body", ""))
    vector = [float(any(e["signal"] == name and e["polarity"] == 1 for e in evidence)) for name in PATTERNS]
    vector += [
        len(case.get("suppliers", [])) / 5,
        case["buyer"].get("relationship_eur_per_point", 0) / max(cost, 1),
        (breakdown["uncovered_need"] + breakdown["fallback_risk"]) / max(cost, 1),
        breakdown["quality"] / max(cost, 1),
    ]
    # Canonical ordering keeps full term associations without depending on menu order.
    packages = sorted(
        (closing_terms(case, p, std) for p in _sheets(proposal)),
        key=lambda p: (p["unit_price"], json.dumps(p, sort_keys=True)),
    )
    if not 1 <= len(packages) <= 3:
        raise ValueError("Model supports menus with one to three complete packages")
    for p in packages:
        b = cost_breakdown(case, p, std)
        vector += [
            1,
            (p["unit_price"] - price) / price,
            (p["payment_days"] - reference["payment_days"]) / 90,
            (p["contract_months"] - reference["contract_months"]) / 36,
            _date_delta(p.get("effective_date"), reference.get("effective_date")),
            (p["lead_time_days"] - reference["lead_time_days"]) / max(reference["lead_time_days"], 1),
            (p["volume_per_year"] - reference["volume_per_year"]) / max(need, 1),
            ((p.get("ppm_commitment") or 0) - (reference.get("ppm_commitment") or 0)) / 10000,
            ((p.get("chargeback_eur_per_complaint") or 0) - (reference.get("chargeback_eur_per_complaint") or 0))
            / price,
            ((p.get("tooling_eur") or 0) - (reference.get("tooling_eur") or 0)) / max(cost, 1),
            (b["freight"] - breakdown["freight"]) / max(cost, 1),
            (b["index_clause"] - breakdown["index_clause"]) / max(cost, 1),
            (b["index_risk"] - breakdown["index_risk"]) / max(cost, 1),
            (total_cost(case, p, std) - cost) / max(cost, 1),
        ]
    vector += [0.0] * ((3 - len(packages)) * len(PACKAGE_FIELDS))
    assert len(vector) == len(EXTRA_FEATURES) and all(math.isfinite(x) for x in vector)
    return [max(-3, min(3, float(x))) for x in vector], evidence


def outcome(case, prefix, response):
    """Primary next quote only; text alternatives are not presumed executable.

    Signed improvement includes worsening. Exits without a live offer have a risk
    label and a missing gain, never a fabricated euro reward. Private relationship
    cost is unknown; this is explicitly direct cost, not true final Arena utility.
    """
    exited = response.get("status") in {"walked_away", "no_bid"}
    gain = None
    if not exited and _sheets(response):
        previous = next(m for m in reversed(prefix) if m.get("from") not in {"buyer", "arena"} and _sheets(m))
        std = standard_terms(prefix)
        old = total_cost(case, _sheets(previous)[0], std)
        new = total_cost(case, _sheets(response)[0], std)
        gain = (old - new) / max(old, 1)
    return {"quoted_cost_gain": gain, "supplier_exit": int(exited)}


def enrich_rows(raw, runs):
    rows = enrich_history(raw)
    for row in rows:
        run = runs[row["session_id"]]
        events, turn = run["threads"][row["supplier_id"]], row["turn"]
        extra, evidence = complete_features(run["case"], events[:turn], events[turn])
        row["v"] = row["z"] + extra
        row["signal_evidence"] = evidence
        row["y"].update(outcome(run["case"], events[:turn], events[turn + 1]))
    return rows


def read_runs(folder):
    runs = {}
    for path in Path(folder).glob("*.json"):
        run = json.loads(path.read_text(encoding="utf-8"))
        sid = run.get("session_id")
        if sid and run.get("case"):
            if sid not in runs or sum(map(len, run.get("threads", {}).values())) > sum(
                map(len, runs[sid].get("threads", {}).values())
            ):
                runs[sid] = copy.deepcopy(run)
    return runs


def live_row(case, supplier_id, prefix, proposal):
    """Build the identical input schema before sending a proposed message.

    Only supplied history is available. Outcome labels for the candidate are
    explicitly missing; previous observed responses can supply online feedback.
    """
    from .response_value import TARGETS, add_target_history

    run = {"mode": "hosted", "case": case, "session_id": "live", "threads": {supplier_id: prefix}}
    raw, _ = extract_run(run)
    prior = enrich_rows(raw, {"live": run})
    current = {
        "session_id": "live",
        "supplier_id": supplier_id,
        "case_id": case["case_id"],
        "turn": len(prefix),
        "x": features(case, prefix, proposal),
        "y": {"package_matched": None, **{target: None for target in TARGETS}},
    }
    current = enrich_history(raw + [current])[-1]
    extra, evidence = complete_features(case, prefix, proposal)
    current.update(v=current["z"] + extra, signal_evidence=evidence)
    return add_target_history(prior + [current])[-1]
