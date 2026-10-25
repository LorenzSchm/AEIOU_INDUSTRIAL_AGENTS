"""Retrieve the supplier's situation, not just the references listed in a case."""

import json
import math
import threading
from datetime import date

from arena_kit.data import DataPackage

from .config import data_root


class ResearchPackage(DataPackage):
    """Serialize a package connection and lazy ID index across parallel case workers."""

    def __init__(self, root, release=None):
        self._read_lock = threading.RLock()
        self._evidence_index = None
        super().__init__(root, release=release)
        self.release_ids = set()
        self.release_as_of = None
        if self.release:
            released = json.loads((self.release / "facts.json").read_text(encoding="utf-8"))
            self.release_ids = set(released)
            self.release_as_of = max((f.get("date", "") for f in released.values()), default="") or None

    def source_record(self, ref):
        """Merge the citable fact with its full CSV/SQLite source, without rewriting either."""
        with self._read_lock:
            return {**self._ids.get(ref, {}), **self.facts.get(ref, {})}

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

    if case["buyer"].get("category_goal") != "leverage" or "index_clause" not in case["negotiable"]:
        return []
    cutoff = evidence_cutoff(case, pkg)[:7]
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
    from .retrieval import evidence_cutoff

    cutoff = evidence_cutoff(case, pkg)

    def visible(fact):
        return not fact.get("date") or fact["date"] <= cutoff

    suppliers, parts = case["suppliers"], case["parts"]
    facts = {ref: fact for ref, fact in case.get("facts", {}).items() if visible(fact)}
    docs, rows, indices, estimates = [], {}, [], {}
    for ref in [*suppliers, *parts, *case.get("refs", [])]:
        if value := pkg.lookup(ref):
            if visible(value):
                facts[ref] = value
    # Bound retrieval to relevant supplier / part rows. SQL is parameterized and read-only.
    for table, column, values in [
        ("supplier_intel", "supplier_id", suppliers),
        ("negotiation_history", "supplier_id", suppliers),
        ("should_cost", "part_id", parts),
        ("quotes", "part_id", parts),
        ("news", "supplier_id", suppliers),
        ("scorecards", "supplier_id", suppliers),
    ]:
        if values and table in pkg.tables():
            marks = ",".join("?" for _ in values)
            rows[table] = pkg.query(f'SELECT * FROM "{table}" WHERE "{column}" IN ({marks})', values, limit=40)
            rows[table] = [r for r in rows[table] if visible(r)]
            for row in rows[table]:
                if row.get("id") and (fact := pkg.lookup(row["id"])):
                    facts[row["id"]] = fact
    doc_refs = [case.get("trigger_doc")]
    if "documents" in pkg.tables():
        for sid in suppliers:
            doc_refs.extend(
                r["id"]
                for r in pkg.query("SELECT id FROM documents WHERE supplier_id=? ORDER BY date DESC LIMIT 8", [sid])
            )
    # Sunday facts are not in the original SQLite database; retrieve them too.
    for ref, fact in pkg.facts.items():
        if (
            ref.startswith(("doc:", "news:"))
            and visible(fact)
            and fact.get("supplier", fact.get("supplier_id")) in suppliers
        ):
            facts[ref] = fact
            if ref.startswith("doc:"):
                doc_refs.append(ref)
    for ref in dict.fromkeys(r for r in doc_refs if r):
        try:
            if not visible(pkg.lookup(ref) or {}):
                continue
            docs.append({"id": ref, "text": pkg.document(ref)[:4500]})
            if fact := pkg.lookup(ref):
                facts[ref] = fact
        except (FileNotFoundError, ValueError, TypeError):
            continue
    release_context = []
    for ref in sorted(getattr(pkg, "release_ids", ())):
        source = pkg.source_record(ref)
        sid = source.get("supplier") or source.get("supplier_id") or None
        if not visible(source) or sid not in {*suppliers, None} or source.get("topic") == "noise":
            continue
        text = pkg.document(ref)
        facts[ref] = pkg.lookup(ref)
        if ref.startswith("news:"):
            rows.setdefault("news", []).append(source)
        release_context.append(
            {
                "source_id": ref,
                "supplier_id": sid,
                "date": source.get("date"),
                "type": source.get("type") or "news",
                "text": text[:4500],
                "private_planning_only": True,
            }
        )
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
        if "cost_drivers" not in pkg.tables():
            continue
        for driver in pkg.query("SELECT * FROM cost_drivers WHERE part_id=?", [part]):
            series = driver["series_id"]
            points = sorted(
                [
                    (ref, f)
                    for ref, f in pkg.facts.items()
                    if ref.startswith("idx:")
                    and f.get("series") == series
                    and f.get("value")
                    and f.get("month", "") <= cutoff[:7]
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
    result = {
        "facts": facts,
        "documents": docs,
        "tables": rows,
        "indices": indices,
        "recent_indices": recent,
        "estimates": estimates,
    }
    if release_context:
        result["release_context"] = release_context
    return result
