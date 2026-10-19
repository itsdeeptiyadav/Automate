"""Split text into overlapping chunks that prefer paragraph / sentence boundaries."""
from __future__ import annotations

import re

_BREAK = re.compile(r"\n\n|(?<=[.!?])\s+|\n")


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> list[tuple[int, str]]:
    """Return [(start_char, chunk_text)]. start_char indexes into `text`."""
    size = max(200, size)
    overlap = max(0, min(overlap, size // 2))
    chunks: list[tuple[int, str]] = []
    n, pos = len(text), 0
    while pos < n:
        end = min(n, pos + size)
        if end < n:
            window = text[pos + size // 2 : end]
            best = None
            for m in _BREAK.finditer(window):
                best = m
            if best is not None:
                end = pos + size // 2 + best.end()
        piece = text[pos:end]
        stripped = piece.strip()
        if stripped:
            chunks.append((pos + (len(piece) - len(piece.lstrip())), stripped))
        if end >= n:
            break
        pos = max(end - overlap, pos + 1)
    return chunks
