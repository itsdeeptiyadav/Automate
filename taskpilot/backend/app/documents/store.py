"""Persist uploaded documents: validate, extract text, chunk, store on disk and in SQLite."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import BinaryIO, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.documents.chunking import chunk_text
from app.documents.extract import ExtractError, extract_text, kind_for
from app.documents.search import bm25_search
from app.models.entities import Document, DocumentChunk


class DocumentError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _safe_stem(filename: str) -> str:
    stem = Path(filename.replace("\\", "/")).stem
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:60].strip("._") or "document"


def save_document(db: Session, settings: Settings, filename: str, fileobj: BinaryIO) -> Document:
    kind = kind_for(filename)
    if kind is None:
        raise DocumentError(415, "Supported types: .pdf, .docx, .txt, .md, .csv.")
    data = fileobj.read(settings.max_document_bytes + 1)
    if len(data) > settings.max_document_bytes:
        raise DocumentError(413, f"File is too large (limit {settings.max_document_bytes // 1_000_000} MB).")
    if not data:
        raise DocumentError(422, "The file is empty.")
    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalars(select(Document).where(Document.sha256 == digest)).first()
    if existing:
        raise DocumentError(409, f"This file is already in the library as '{existing.filename}' (#{existing.id}).")
    try:
        text, pages = extract_text(kind, data, settings.max_document_pages)
    except ExtractError as exc:
        raise DocumentError(exc.status, exc.message)

    display = Path(filename.replace("\\", "/")).name[:255] or "document"
    doc = Document(filename=display, stored_name="", kind=kind, size_bytes=len(data), sha256=digest,
                   page_count=pages, char_count=len(text), text=text)
    db.add(doc)
    db.flush()  # assigns doc.id
    pieces = chunk_text(text, settings.chunk_chars, settings.chunk_overlap)
    db.add_all(DocumentChunk(document_id=doc.id, idx=i, start_char=start, text=piece) for i, (start, piece) in enumerate(pieces))
    doc.chunk_count = len(pieces)

    settings.documents_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{doc.id}_{_safe_stem(display)}.{kind}"  # never uses the client's path
    try:
        (settings.documents_dir / stored).write_bytes(data)
        doc.stored_name = stored
        db.commit()
    except Exception:
        db.rollback()
        (settings.documents_dir / stored).unlink(missing_ok=True)
        raise
    return doc


def delete_document(db: Session, settings: Settings, doc: Document) -> None:
    path = settings.documents_dir / doc.stored_name
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    db.delete(doc)
    db.commit()
    if doc.stored_name:
        path.unlink(missing_ok=True)


def search_chunks(db: Session, query: str, document_id: Optional[int] = None, top_k: int = 5):
    stmt = select(DocumentChunk.id, DocumentChunk.document_id, DocumentChunk.idx, DocumentChunk.text)
    if document_id is not None:
        stmt = stmt.where(DocumentChunk.document_id == document_id)
    rows = [tuple(r) for r in db.execute(stmt).all()]
    return bm25_search(rows, query, top_k)
