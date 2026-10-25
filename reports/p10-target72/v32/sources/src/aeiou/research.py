"""Retrieve the supplier's situation, not just the references listed in a case."""

import json
import threading
from datetime import date

from arena_kit.data import DataPackage

from .config import data_root
from .p10_evidence import enrich_p10
from .p18_evidence import enrich_p18
from .research_collection import collect_tables, reconcile_collection


class ResearchPackage(DataPackage):
    """Serialize a package connection and lazy ID index across parallel case workers."""

    def __init__(self, root, *, include_release=True):
        self._read_lock = threading.RLock()
        self._base_view = None
        super().__init__(root, include_release=include_release)

    def for_case(self, case):
        # Official practice/sandbox cases stay on package v1 after Sunday's release.
        if self.release and case.get("set") in {"practice", "sandbox"}:
            with self._read_lock:
                if self._base_view is None:
                    self._base_view = ResearchPackage(self.root, include_release=False)
                return self._base_view
        return self

    def tables(self):
        with self._read_lock:
            return super().tables()

    def query(self, sql, params=(), limit=200):
        with self._read_lock:
            return super().query(sql, params, limit)

    def lookup(self, ref):
        with self._read_lock:
            return super().lookup(ref)

    def document(self, ref):
        with self._read_lock:
            return super().document(ref)


def package():
    return ResearchPackage(data_root())


def research(case, pkg):
    if isinstance(pkg, ResearchPackage):
        pkg = pkg.for_case(case)
    suppliers, parts = case["suppliers"], case["parts"]
    facts = dict(case.get("facts", {}))
    docs, indices, estimates, issues = [], [], {}, []
    rows, collection = collect_tables(case, pkg)
    for ref in [*suppliers, *parts, *case.get("refs", [])]:
        if value := pkg.lookup(ref):
            facts[ref] = value
        elif ref not in facts:
            issues.append({"source_ref": ref, "status": "unavailable", "error": "MissingReference"})
    for table in ("should_cost", "quotes", "news", "scorecards", "contracts", "contract_clauses"):
        for row in rows.get(table, []):
            if row.get("id") and (fact := pkg.lookup(row["id"])):
                facts[row["id"]] = fact
                if table == "contract_clauses":
                    facts[row["id"]] = {**fact, "text": row["text"]}
    doc_refs = [case.get("trigger_doc")]
    doc_refs.extend(r["id"] for r in rows.get("documents", []))
    doc_refs.extend(r["id"] for r in rows.get("contracts", []))
    doc_refs.extend(ref for ref in case.get("refs", []) if ref.startswith(("doc:", "ctr:")) and "#" not in ref)
    # Sunday facts are not in the original SQLite database; retrieve them too.
    for ref, fact in pkg.facts.items():
        if ref.startswith(("doc:", "news:")) and any(s in json.dumps(fact) for s in suppliers):
            facts[ref] = fact
            if ref.startswith("doc:"):
                doc_refs.append(ref)
    for ref in dict.fromkeys(r for r in doc_refs if r):
        try:
            docs.append({"id": ref, "text": pkg.document(ref)})
            if fact := pkg.lookup(ref):
                facts[ref] = fact
        except (FileNotFoundError, ValueError, TypeError) as exc:
            issues.append({"source_ref": ref, "error": type(exc).__name__, "status": "unavailable"})
            continue
    for part in parts:
        costs = [r for r in rows.get("should_cost", []) if r["part_id"] == part]
        for sid in suppliers:
            match = next((r for r in costs if r.get("supplier_id") == sid), None)
            if match:
                estimates[sid] = {
                    "full_cost": match["unit_cost"] / (match.get("price_unit") or 1),
                    "confidence_pct": match.get("confidence_pct", 15),
                    "source": match["id"],
                }
        for driver in (r for r in rows.get("cost_drivers", []) if r["part_id"] == part):
            series = driver["series_id"]
            points = sorted(
                [
                    (ref, f)
                    for ref, f in pkg.facts.items()
                    if ref.startswith("idx:") and f.get("series") == series and f.get("value")
                ],
                key=lambda r: r[1]["month"],
            )
            if len(points) < 2:
                continue
            end_ref, end = points[-1]
            end_month = date.fromisoformat(end["month"] + "-01")
            eligible = [
                (r, f)
                for r, f in points[:-1]
                if 0 < (end_month.year - int(f["month"][:4])) * 12 + end_month.month - int(f["month"][5:7]) <= 12
            ]
            if not eligible:
                continue
            start_ref, start = eligible[0]
            change = (end["value"] / start["value"] - 1) * 100
            claim = f"{series} changed {change:+.2f}% from {start['month']} to {end['month']} ({start_ref}, {end_ref})."
            indices.append(
                {
                    "series": series,
                    "weight": driver["weight"],
                    "change_pct": round(change, 4),
                    "weighted_change_pct": round(change * driver["weight"], 4),
                    "claim": claim,
                    "refs": [start_ref, end_ref],
                }
            )
            facts[start_ref], facts[end_ref] = start, end
    intelligence = {"facts": facts, "documents": docs, "tables": rows, "indices": indices, "estimates": estimates}
    intelligence["retrieval_issues"] = issues
    intelligence["collection"] = collection
    enrich_p10(case, pkg, intelligence)
    enrich_p18(case, pkg, intelligence)
    reconcile_collection(intelligence)
    return intelligence
