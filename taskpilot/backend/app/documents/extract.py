"""Turn an uploaded file into plain text. No OCR: scanned PDFs are rejected with a clear message."""
from __future__ import annotations

import io
import re
import zipfile
from typing import Optional
from xml.etree import ElementTree as ET

from pypdf import PdfReader
from pypdf.errors import PyPdfError

ALLOWED_KINDS = {".pdf": "pdf", ".txt": "txt", ".md": "md", ".csv": "csv", ".docx": "docx"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def kind_for(filename: str) -> Optional[str]:
    name = (filename or "").lower()
    for ext, kind in ALLOWED_KINDS.items():
        if name.endswith(ext):
            return kind
    return None


def _clean(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pdf(data: bytes, max_pages: int) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractError(422, "This PDF is password-protected.")
        pages = len(reader.pages)
        parts = [(reader.pages[i].extract_text() or "") for i in range(min(pages, max_pages))]
    except ExtractError:
        raise
    except (PyPdfError, ValueError, KeyError, OSError) as exc:
        raise ExtractError(422, f"The PDF could not be read ({type(exc).__name__}).")
    return "\n\n".join(parts), pages


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if "word/document.xml" not in z.namelist():
                raise ExtractError(422, "This is not a valid .docx file.")
            if z.getinfo("word/document.xml").file_size > 20_000_000:
                raise ExtractError(413, "The document body is too large.")
            root = ET.fromstring(z.read("word/document.xml"))
    except zipfile.BadZipFile:
        raise ExtractError(422, "This is not a valid .docx file.")
    except ET.ParseError:
        raise ExtractError(422, "The .docx content could not be parsed.")
    paragraphs = []
    for p in root.iter(f"{_W}p"):
        paragraphs.append("".join(t.text or "" for t in p.iter(f"{_W}t")))
    return "\n".join(paragraphs)


def extract_text(kind: str, data: bytes, max_pages: int = 200) -> tuple[str, Optional[int]]:
    """Return (text, page_count). Raises ExtractError with an HTTP-style status."""
    pages: Optional[int] = None
    if kind == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ExtractError(415, "This file is not a real PDF.")
        text, pages = _pdf(data, max_pages)
    elif kind == "docx":
        if not data.startswith(b"PK"):
            raise ExtractError(415, "This file is not a real .docx.")
        text = _docx(data)
    else:
        if b"\x00" in data[:4096]:
            raise ExtractError(415, "This looks like a binary file, not text.")
        text = data.decode("utf-8", errors="replace")
    text = _clean(text)
    if not text:
        raise ExtractError(422, "No readable text was found (scanned/image-only files are not supported).")
    return text, pages
