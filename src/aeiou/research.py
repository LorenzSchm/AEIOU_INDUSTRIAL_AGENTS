"""Retrieve the supplier's situation, not just the references listed in a case."""

import json
import threading
from datetime import date

from arena_kit.data import DataPackage

from .config import data_root


class ResearchPackage(DataPackage):
    """Serialize a package connection and lazy ID index across parallel case workers."""

    def __init__(self, root):
        self._read_lock = threading.RLock()
        self._evidence_index = None
        super().__init__(root)

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


def research(case, pkg):
    suppliers, parts = case["suppliers"], case["parts"]
    facts = dict(case.get("facts", {}))
    docs, rows, indices, estimates = [], {}, [], {}
    for ref in [*suppliers, *parts, *case.get("refs", [])]:
        if value := pkg.lookup(ref):
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
        if ref.startswith(("doc:", "news:")) and any(s in json.dumps(fact) for s in suppliers):
            facts[ref] = fact
            if ref.startswith("doc:"):
                doc_refs.append(ref)
    for ref in dict.fromkeys(r for r in doc_refs if r):
        try:
            docs.append({"id": ref, "text": pkg.document(ref)[:4500]})
            if fact := pkg.lookup(ref):
                facts[ref] = fact
        except (FileNotFoundError, ValueError, TypeError):
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
        if "cost_drivers" not in pkg.tables():
            continue
        for driver in pkg.query("SELECT * FROM cost_drivers WHERE part_id=?", [part]):
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
    return {"facts": facts, "documents": docs, "tables": rows, "indices": indices, "estimates": estimates}
