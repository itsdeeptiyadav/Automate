"""Small dependency-free BM25 ranking over document chunks.

Deterministic and good enough for a personal library; no embeddings or external service needed.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional

_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "is", "it", "its",
    "of", "on", "or", "that", "the", "to", "was", "were", "what", "when", "which", "who", "will", "with",
    "this", "these", "those", "do", "does", "did", "how", "about", "into", "than", "then", "there", "their",
}


def _stem(t: str) -> str:
    """Very light suffix stripping so 'refunds', 'refunded' and 'refund' match. Applied to queries and documents alike."""
    if t.isdigit() or len(t) <= 3:
        return t
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    for suffix in ("ing", "ed"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 4:
            return t[: -len(suffix)]
    if t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in _TOKEN.findall(text.casefold()) if t not in _STOP and (len(t) > 1 or t.isdigit())]


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    idx: int
    score: float
    text: str


def snippet(text: str, terms: Iterable[str], width: int = 240) -> str:
    low = text.casefold()
    pos = min((p for p in (low.find(t[: max(3, len(t) - 1)]) for t in terms) if p >= 0), default=0)  # stems may be shorter than the word
    start = max(0, pos - width // 3)
    out = text[start : start + width].strip().replace("\n", " ")
    return ("..." if start > 0 else "") + out + ("..." if start + width < len(text) else "")


def bm25_search(
    chunks: list[tuple[int, int, int, str]],  # (chunk_id, document_id, idx, text)
    query: str,
    top_k: int = 5,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[Hit]:
    q_terms = list(dict.fromkeys(tokenize(query)))
    if not q_terms or not chunks:
        return []
    docs = [(cid, did, idx, text, Counter(tokenize(text))) for cid, did, idx, text in chunks]
    n = len(docs)
    avg_len = sum(sum(tf.values()) for *_, tf in docs) / n or 1.0
    df = {t: sum(1 for *_, tf in docs if t in tf) for t in q_terms}
    hits: list[Hit] = []
    for cid, did, idx, text, tf in docs:
        length = sum(tf.values()) or 1
        score = 0.0
        for t in q_terms:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * length / avg_len))
        if score > 0:
            hits.append(Hit(cid, did, idx, score, text))
    hits.sort(key=lambda h: (-h.score, h.document_id, h.idx))
    return hits[:top_k]


def search_terms(query: str) -> list[str]:
    return list(dict.fromkeys(tokenize(query)))


def normalise_for_match(s: str) -> str:
    """Whitespace- and case-insensitive form used when checking that a quote really occurs in a document."""
    return re.sub(r"\s+", " ", s).strip().casefold()


def quote_in_text(quote: str, text: str) -> bool:
    q = normalise_for_match(quote).strip("\"'“”‘’ .…")
    return bool(q) and q in normalise_for_match(text)


def first_match_offset(text: str, query: Optional[str]) -> int:
    if not query:
        return 0
    low = text.casefold()
    return min((p for p in (low.find(t[: max(3, len(t) - 1)]) for t in search_terms(query)) if p >= 0), default=0)
