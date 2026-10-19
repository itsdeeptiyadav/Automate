"""Invoice PDF upload: validate, save into the workspace invoices folder, confirm it is readable."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.config import Settings
from app.tools.base import ToolFailure
from app.tools.pdf import extract_pdf_text, parse_invoice_text
from app.tools.safety import rel_posix


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_uploaded_invoice(settings: Settings, filename: str, fileobj: BinaryIO) -> dict:
    if not filename or not filename.lower().endswith(".pdf"):
        raise UploadError(415, "Only .pdf files can be uploaded.")
    data = fileobj.read(settings.max_file_bytes + 1)
    if len(data) > settings.max_file_bytes:
        raise UploadError(413, f"File is too large (limit {settings.max_file_bytes // 1000} KB).")
    if not data.startswith(b"%PDF-"):
        raise UploadError(415, "This file is not a real PDF.")

    inv_dir = settings.invoices_dir
    inv_dir.mkdir(parents=True, exist_ok=True)
    digest = _sha256(data)
    for existing in inv_dir.glob("*.pdf"):
        if _sha256(existing.read_bytes()) == digest:
            raise UploadError(409, f"This PDF is already in the workspace as {existing.name}.")

    # Never use the client's path: keep only a sanitised name, always inside the invoices folder.
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename.replace("\\", "/")).stem)[:60].strip("._") or "invoice"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = inv_dir / f"upload_{stamp}_{uuid4().hex[:4]}_{stem}.pdf"
    dest.write_bytes(data)
    try:
        parsed = parse_invoice_text(extract_pdf_text(dest), rel_posix(settings.workspace_root, dest))
    except ToolFailure as exc:
        dest.unlink()
        raise UploadError(422, f"{exc.message} (expected text lines like 'Vendor:', 'Invoice Number:', 'Invoice Date:', 'Due Date:', 'Total Due:', 'Currency:')")
    except Exception:  # noqa: BLE001 - corrupt PDFs can raise many things
        dest.unlink()
        raise UploadError(422, "The PDF could not be read as an invoice.")

    fields = parsed.to_fields()
    return {
        "file": fields["source_file"],
        **{k: fields[k] for k in ("company", "invoice_number", "invoice_date", "due_date", "amount", "currency")},
        "warnings": parsed.warnings,
        "suggested_task": (
            f"Find the latest invoice from {parsed.company}, extract its invoice number, amount, and due date, "
            "save it in the internal invoice system, verify that it was saved correctly, and tell me the result."
        ),
    }
