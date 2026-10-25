"""Local hybrid evidence retrieval with source/date filters and optional dense vectors.

Default semantic matching uses explicit business concept expansion, not a hosted
embedding model. Callers may supply real document/query embeddings; no API is
called implicitly and embeddings must identify the exact indexed chunk IDs.
"""

import hashlib
import math
import re
from collections import Counter

DATA_CUTOFF = "2026-09-30"


def evidence_cutoff(case, pkg=None):
    if case.get("as_of"):
        return case["as_of"]
    if case.get("set") == "final" and getattr(pkg, "release", None):
        # Final cases can use the downloaded release. Historical practice and
        # sandbox cases remain anchored to the frozen base package.
        return max([DATA_CUTOFF, *(f.get("date") or DATA_CUTOFF for f in pkg.facts.values())])
    return DATA_CUTOFF


CONCEPTS = {
    "liquidity": {"cash", "liquidity", "liquidität", "payment", "paid", "financing", "working", "capital", "zahlung"},
    "continuity": {"volume", "forecast", "commitment", "planning", "framework", "duration", "planbarkeit"},
    "materials": {"copper", "hedged", "forward", "fixed", "material", "energy", "kupfer", "costs"},
    "capacity": {"capacity", "delivery", "lead", "schedule", "production", "kapazität", "lieferzeit"},
    "quality": {"quality", "defect", "ppm", "complaint", "8d", "reklamation", "qualität"},
    "authority": {"approval", "director", "committee", "authorize", "owner", "freigabe"},
}


def tokens(text):
    return re.findall(r"[\w:@#.-]+", text.casefold())


def concept_vector(text):
    words = set(tokens(text))
    return {concept: len(words & vocabulary) for concept, vocabulary in CONCEPTS.items() if words & vocabulary}


def cosine(a, b):
    if isinstance(a, dict):
        dot = sum(value * b.get(key, 0) for key, value in a.items())
        norm = math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))
    else:
        if len(a) != len(b) or not all(math.isfinite(v) for v in [*a, *b]):
            raise ValueError("Embedding dimensions and finite values must match")
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return dot / norm if norm else 0.0


class HybridEvidenceIndex:
    def __init__(self, documents, vectors=None):
        self.chunks = []
        for document in documents:
            text = document["text"]
            # Overlap preserves qualifications split at a chunk boundary.
            for offset in range(0, len(text), 700):
                excerpt = text[offset : offset + 1000]
                if not excerpt.strip():
                    continue
                chunk_id = hashlib.sha256(f"{document['id']}:{offset}:{excerpt}".encode()).hexdigest()[:20]
                self.chunks.append(
                    {
                        **document,
                        "text": excerpt,
                        "chunk_id": chunk_id,
                        "offset": offset,
                        "counts": Counter(tokens(excerpt)),
                        "concepts": concept_vector(excerpt),
                    }
                )
        self.vectors = vectors or {}
        ids = {c["chunk_id"] for c in self.chunks}
        if set(self.vectors) - ids:
            raise ValueError("Dense vectors must refer to the current chunk contents")
        self.df = Counter(word for c in self.chunks for word in c["counts"])
        self.average_length = sum(sum(c["counts"].values()) for c in self.chunks) / max(1, len(self.chunks))

    @classmethod
    def from_package(cls, pkg):
        documents = []
        for ref, fact in pkg.facts.items():
            if ref.startswith("news:"):
                row = pkg._ids.get(ref, {})
                documents.append(
                    {
                        "id": ref,
                        "text": "\n".join(filter(None, [fact.get("headline", ""), row.get("body", "")])),
                        "supplier_id": fact.get("supplier"),
                        "part_id": fact.get("part"),
                        "date": fact.get("date"),
                        "type": "news",
                    }
                )
                continue
            if not ref.startswith("doc:"):
                continue
            try:
                text = pkg.document(ref)
            except (FileNotFoundError, ValueError, TypeError):
                continue
            documents.append(
                {
                    "id": ref,
                    "text": text,
                    "supplier_id": fact.get("supplier"),
                    "part_id": fact.get("part"),
                    "date": fact.get("date"),
                    "type": fact.get("type"),
                }
            )
        return cls(documents)

    def search(self, query, suppliers, parts=(), cutoff=None, limit=6, query_vector=None):
        if not 1 <= limit <= 20 or not suppliers:
            raise ValueError("Search requires supplier scope and limit between 1 and 20")
        words, concepts = tokens(query), concept_vector(query)
        ranked = []
        for chunk in self.chunks:
            general_market = chunk.get("supplier_id") is None and chunk.get("type") in {"memo", "news"}
            if chunk.get("supplier_id") not in suppliers and not general_market:
                continue
            if parts and chunk.get("part_id") and chunk["part_id"] not in parts:
                continue
            if cutoff and chunk.get("date") and chunk["date"] > cutoff:
                continue
            length = sum(chunk["counts"].values())
            lexical = 0.0
            for word in words:
                frequency = chunk["counts"].get(word, 0)
                idf = math.log(1 + (len(self.chunks) - self.df[word] + 0.5) / (self.df[word] + 0.5))
                lexical += (
                    idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / max(1, self.average_length)))
                )
            semantic = cosine(concepts, chunk["concepts"])
            dense = (
                cosine(query_vector, self.vectors[chunk["chunk_id"]])
                if query_vector is not None and chunk["chunk_id"] in self.vectors
                else 0
            )
            score = lexical + semantic * 2 + dense * 3
            if score > 0:
                ranked.append((score, chunk))
        # Retrieval reranks exact matches and concepts before a recency tie break.
        ranked.sort(key=lambda pair: (pair[0], pair[1].get("date") or ""), reverse=True)
        selected, per_source = [], Counter()
        for score, chunk in ranked:
            if per_source[chunk["id"]] >= 2:
                continue
            per_source[chunk["id"]] += 1
            selected.append(
                {
                    k: chunk.get(k)
                    for k in ("id", "chunk_id", "text", "supplier_id", "part_id", "date", "type", "offset")
                }
                | {
                    "retrieval_score": round(score, 5),
                    "method": "bm25+concepts+dense" if query_vector is not None else "bm25+concepts",
                }
            )
            if len(selected) == limit:
                break
        return selected


def evidence_cards(hits, intelligence):
    from .grounding import safe_excerpt

    cards = []
    for hit in hits:
        # Boundary fragments remain useful for private retrieval but cannot be
        # promoted into a supplier-facing quotation.
        whole = hit["text"]
        if hit.get("offset", 0):
            whole = whole.split("\n\n", 1)[1] if "\n\n" in whole else ""
        if len(hit["text"]) == 1000:
            whole = whole.rsplit("\n\n", 1)[0] if "\n\n" in whole else ""
        approved = safe_excerpt(whole)
        text = approved or hit["text"]
        # Internal notes may inform private planning, but are never quoted to a seller.
        shareable = (
            hit.get("type") == "letter"
            and approved is not None
            and len(text) <= 450
            and not re.search(
                r"budget|walk.?away|reservation|confidential|urgent|maximum|minimum|ignore.{0,20}instructions",
                text,
                re.I,
            )
        )
        cards.append(
            {
                "id": hit["chunk_id"],
                "source_id": hit["id"],
                "text": text,
                "supplier_id": hit.get("supplier_id"),
                "date": hit.get("date"),
                "shareable": shareable,
                "kind": "verbatim_source",
                "method": hit["method"],
            }
        )
    for index in intelligence.get("indices", []) + intelligence.get("recent_indices", []):
        cards.append(
            {
                "id": "index:" + index["series"] + (":" + index["window"] if index.get("window") else ""),
                "source_ids": index["refs"],
                "text": index["claim"],
                "shareable": True,
                "kind": "computed_index",
                "change_pct": index.get("change_pct"),
                "supplier_id": None,
                **({"window": index["window"], "weight": index["weight"]} if index.get("window") else {}),
            }
        )
    return cards
