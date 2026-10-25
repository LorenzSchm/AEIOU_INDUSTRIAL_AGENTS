"""Export all buyer knowledge with provenance; documents are not response labels."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from aeiou.research import package
from aeiou.research_collection import read_all
from aeiou.training_corpus import dump, write_jsonl


def export(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    pkg = package()
    rows = []
    counts = {}
    for name in pkg.tables():
        values = read_all(pkg, f'SELECT * FROM "{name}" ORDER BY rowid')
        counts[name] = len(values)
        rows.extend({"table": name, "release": "base", "record": row} for row in values)
    if pkg.release:
        for path in sorted((pkg.release / "data").glob("*.csv")):
            with path.open(encoding="utf-8", newline="") as handle:
                values = list(csv.DictReader(handle))
            counts["sunday:" + path.stem] = len(values)
            rows.extend({"table": path.stem, "release": "sunday", "record": row} for row in values)
    release_refs = set(json.loads((pkg.release / "facts.json").read_text())) if pkg.release else set()
    documents, errors = [], []
    for ref in pkg.ids("doc:") + pkg.ids("ctr:") + pkg.ids("news:"):
        if "#" in ref:
            continue
        try:
            row = pkg._ids.get(ref, {})
            text = (
                "\n".join(filter(None, [row.get("headline"), row.get("body")]))
                if ref.startswith("news:")
                else pkg.document(ref)
            )
            documents.append(
                {
                    "id": ref,
                    "release": "sunday" if ref in release_refs else "base",
                    "fact": pkg.lookup(ref),
                    "text": text,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
            )
        except (OSError, ValueError, TypeError) as exc:
            errors.append({"ref": ref, "error": type(exc).__name__})
    write_jsonl(output / "business-records.jsonl", rows)
    write_jsonl(output / "documents.jsonl", documents)
    write_jsonl(output / "facts.jsonl", ({"id": ref, "fact": fact} for ref, fact in sorted(pkg.facts.items())))
    files = []
    for root in [pkg.root, *([pkg.release] if pkg.release else [])]:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                files.append(
                    {
                        "path": str(path),
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
    manifest = {
        "tables": counts,
        "business_records": len(rows),
        "facts": len(pkg.facts),
        "documents": len(documents),
        "sunday_refs": sorted(release_refs),
        "errors": errors,
        "files": files,
        "policy": "Knowledge/retrieval corpus, not invented conversation targets. "
        "Dated context in response training stays at the original case cutoff. Sunday evidence is final-only.",
    }
    dump(output / "manifest.json", manifest)
    return {k: v for k, v in manifest.items() if k != "files"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    print(json.dumps(export(parser.parse_args().output), indent=2))
