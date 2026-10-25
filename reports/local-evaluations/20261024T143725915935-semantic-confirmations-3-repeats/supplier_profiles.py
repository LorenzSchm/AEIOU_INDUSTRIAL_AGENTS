"""Public company groupings, not claims about Tacto's private agent personalities."""

import csv
from functools import lru_cache

from .config import data_root

PROFILES = {
    "family": {
        "name": "Family-owned",
        "description": "Family-owned companies; relationship and continuity are hypotheses to test.",
    },
    "group": {
        "name": "Corporate group",
        "description": "Group-owned companies; approval processes and package trade-offs are hypotheses.",
    },
    "founder": {
        "name": "Founder-led",
        "description": "Founder-led job shops; capacity and cash flow may influence the discussion.",
    },
    "private": {
        "name": "Privately held",
        "description": "Privately held suppliers; use the case's operational and quality context.",
    },
}

PERSONAS = {
    "family": (
        "You represent the family-owned company in the supplied public facts. Consider "
        "continuity, reputation and the actual commercial situation. Ownership does not force "
        "concessions."
    ),
    "group": (
        "You represent a group-owned company. Consider commercial approvals and exchanges across "
        "terms. Do not invent an approval or claim that an absent manager actually reviewed the "
        "offer."
    ),
    "founder": (
        "You represent a founder-led job shop. Consider capacity, workload and payment terms "
        "using the case facts. Do not assume financial distress without evidence."
    ),
    "private": (
        "You represent a privately held supplier. Respond to the specific operational, quality "
        "and commercial facts. Ownership alone does not establish your bargaining style."
    ),
}


@lru_cache
def companies():
    def read(name, key):
        with (data_root() / "data" / name).open() as stream:
            return {r[key]: r for r in csv.DictReader(stream)}

    suppliers = read("suppliers.csv", "id")
    intel = read("supplier_intel.csv", "supplier_id")
    return {
        sid: {
            "supplier_id": sid,
            "name": row["name"],
            "category": row["category"],
            "country": row["country"],
            **{k: intel.get(sid, {}).get(k) for k in ("owner_type", "group_parent", "staff", "turnover_band")},
        }
        for sid, row in suppliers.items()
    }


def case_profile(case):
    owners = {companies().get(s, {}).get("owner_type") for s in case["suppliers"]}
    return next(iter(owners)) if len(owners) == 1 and next(iter(owners)) in PROFILES else "mixed"


def profile_cases(setups):
    return [
        {
            "id": key,
            **profile,
            "case_ids": [cid for cid, setup in sorted(setups.items()) if case_profile(setup["case"]) == key],
            "source": "data/package/data/supplier_intel.csv · owner_type",
        }
        for key, profile in PROFILES.items()
    ]
