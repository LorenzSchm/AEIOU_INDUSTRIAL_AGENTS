"""Retrieve the supplier's situation, not just the references listed in a case."""

import json
import math
import threading
from datetime import date

from arena_kit.data import DataPackage

from .config import data_root
from .forecast_evidence import latest_forecasts
from .p10_evidence import enrich_p10
from .p18_evidence import enrich_p18
from .research_collection import collect_tables, reconcile_collection


class ResearchPackage(DataPackage):
    """Serialize a package connection and lazy ID index across parallel case workers."""

    def __init__(self, root, *, include_release=True):
        self._read_lock = threading.RLock()
        self._evidence_index = None
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

    def evidence_index(self):
        from .retrieval import HybridEvidenceIndex

        with self._read_lock:
            if self._evidence_index is None:
                self._evidence_index = HybridEvidenceIndex.from_package(self)
            return self._evidence_index

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


def recent_index_reversals(case, pkg, annual_indices):
    """Fixed three-month window, never a retrospectively selected price peak.

    Keep the annual rise visible. A recent market decline neither proves lower
    total supplier costs nor reprices a supplier's already-covered materials.
    """
    from .retrieval import evidence_cutoff

    if case.get("buyer", {}).get("category_goal") != "leverage" or "index_clause" not in case.get("negotiable", []):
        return []
    cutoff = evidence_cutoff(case)[:7]
    result = []
    for annual in annual_indices:
        series = annual["series"]
        points = {
            f["month"]: (ref, f["value"])
            for ref, f in pkg.facts.items()
            if ref.startswith("idx:")
            and f.get("series") == series
            and isinstance(f.get("month"), str)
            and f["month"] <= cutoff
            and type(f.get("value")) in (float, int)
            and math.isfinite(f["value"])
            and f["value"] > 0
        }
        if not points:
            continue
        end = max(points)
        number = int(end[:4]) * 12 + int(end[5:]) - 1
        cutoff_number = int(cutoff[:4]) * 12 + int(cutoff[5:]) - 1
        if cutoff_number - number > 12:
            continue  # The arena's evidence contract rejects stale end points.
        month = lambda n: f"{n // 12:04d}-{n % 12 + 1:02d}"  # noqa: E731
        start, year_start = month(number - 3), month(number - 12)
        if start not in points or year_start not in points:
            continue
        change = (points[end][1] / points[start][1] - 1) * 100
        year_change = (points[end][1] / points[year_start][1] - 1) * 100
        if change >= -0.005 or year_change <= 0:
            continue
        refs = [points[start][0], points[end][0]]
        result.append(
            {
                "series": series,
                "window": "recent_quarter",
                "weight": annual["weight"],
                "change_pct": round(change, 4),
                "annual_change_pct": round(year_change, 4),
                "refs": refs,
                "claim": (
                    f"{series} changed {change:+.2f}% from {start} to {end} ({refs[0]}, {refs[1]}). "
                    "This is the latest three-month change, while the twelve-month change remains positive. "
                    "It does not establish a fall in your total cost or reprice materials already covered."
                ),
            }
        )
    return result


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
    release_refs = (
        set(json.loads((pkg.release / "facts.json").read_text(encoding="utf-8")))
        if case.get("set") == "final" and getattr(pkg, "release", None)
        else set()
    )
    for ref, fact in pkg.facts.items():
        global_release = ref in release_refs and not fact.get("supplier")
        if ref.startswith(("doc:", "news:")) and (global_release or any(s in json.dumps(fact) for s in suppliers)):
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
    recent = recent_index_reversals(case, pkg, indices)
    for index in recent:
        for ref in index["refs"]:
            facts[ref] = pkg.lookup(ref)
    intelligence = {"facts": facts, "documents": docs, "tables": rows, "indices": indices, "estimates": estimates}
    intelligence["recent_indices"] = recent
    intelligence["latest_forecasts"] = latest_forecasts(case, rows)
    intelligence["retrieval_issues"] = issues
    intelligence["collection"] = collection
    enrich_p10(case, pkg, intelligence)
    enrich_p18(case, pkg, intelligence)
    reconcile_collection(intelligence)
    return intelligence
