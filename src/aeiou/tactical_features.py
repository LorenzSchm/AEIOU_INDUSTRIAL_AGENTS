"""Observable outgoing evidence and commitment features, available before sending."""

import re

from arena_kit.agents.llm import ID_RE
from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms

TACTIC_SCHEMA = (
    "cited_po",
    "cited_index",
    "cited_cost",
    "cited_scorecard",
    "cited_contract",
    "cited_forecast",
    "cited_letter",
    "new_source_fraction",
    "reused_source_fraction",
    "body_length",
    "asks_final",
    "firm_commitment",
    "conditional_exchange",
    "explicit_delivery_trade",
    "has_source_attachment",
    "question_without_package",
)

EXPERIENCE_STATE_SCHEMA = (
    "round_fraction",
    "price_vs_opening",
    "payment_days",
    "contract_months",
    "lead_time_days",
    "coverage",
    "index_share",
    "index_deadband",
    "terminal",
)


def experience_state(case, prefix):
    quotes = [m for m in prefix if m.get("from") not in {"buyer", "arena"} and m.get("term_sheets")]
    current = closing_terms(case, quotes[-1]["term_sheets"][0]["terms"], standard_terms(prefix))
    first = quotes[0]["term_sheets"][0]["terms"]["unit_price"]
    clause = current.get("index_clause") or {}
    return [
        sum(m.get("from") == "buyer" for m in prefix) / max(case.get("round_limit", 10), 1),
        current["unit_price"] / first,
        current["payment_days"] / 90,
        current["contract_months"] / 36,
        current["lead_time_days"] / 56,
        current["volume_per_year"] / case["buyer"]["need_per_year"],
        clause.get("material_share", 0),
        clause.get("deadband_pct", 0) / 5,
        float(quotes[-1].get("status") in {"final", "final_offer", "agreed"}),
    ]


def cited_sources(message):
    return set(ID_RE.findall(message.get("body", ""))) | set(message.get("refs", []))


def tactic_features(prefix, proposal):
    refs = cited_sources(proposal)
    prior = set().union(*(cited_sources(m) for m in prefix if m.get("from") == "buyer"))
    body = proposal.get("body", "").lower()
    values = [
        float(any(ref.startswith(kind + ":") for ref in refs))
        for kind in ("po", "idx", "cost", "sc", "ctr", "fc", "doc")
    ]
    values += [
        len(refs - prior) / max(len(refs), 1),
        len(refs & prior) / max(len(refs), 1),
        min(len(body), 5000) / 5000,
        float(bool(re.search(r"best and final|final (?:offer|quote)|letztes angebot", body))),
        float(bool(re.search(r"binding.*(?:year|volume)|firm.*commit|committed.*year", body))),
        float(bool(re.search(r"in return|in exchange|conditional on|reciprocal", body))),
        float(bool(re.search(r"(?:accept|agree).{0,70}\d+.{0,30}days.{0,60}(?:instead|rather)|scheduling room", body))),
        float(bool(proposal.get("attachments"))),
        float(not proposal.get("term_sheets")),
    ]
    assert len(values) == len(TACTIC_SCHEMA)
    return values
