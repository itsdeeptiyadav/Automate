"""Real PDF text extraction + strict invoice field parsing/validation."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.enums import ErrorType
from app.tools.base import ToolFailure

_DATE_FORMATS = ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y")
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_NUMBER_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-/]{2,31}$")

# Text that looks like an attempt to instruct the agent (prompt injection).
_INJECTION_RE = re.compile(
    r"(ignore (all |any )?(previous|prior|above)|disregard (the )?(previous|above|instructions)|"
    r"system\s*(prompt|override|:)|you (must|should) now|delete (all|every)|drop table|"
    r"approve (this|the) (write|action)|do not (ask|request) (for )?approval)",
    re.I,
)


class ParsedInvoice(BaseModel):
    company: str
    invoice_number: str
    invoice_date: date
    due_date: date
    amount: Decimal
    currency: str
    source_file: str
    warnings: list[str] = []

    @property
    def amount_cents(self) -> int:
        return int((self.amount * 100).to_integral_value())

    def to_fields(self) -> dict:
        return {
            "company": self.company,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "amount": f"{self.amount:.2f}",
            "currency": self.currency,
            "source_file": self.source_file,
        }


def normalize_company(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def extract_pdf_text(path: Path, max_pages: int = 5) -> str:
    try:
        reader = PdfReader(str(path))
        pages = reader.pages[:max_pages]
        return "\n".join((p.extract_text() or "") for p in pages)
    except (PyPdfError, ValueError, KeyError, OSError) as exc:
        raise ToolFailure(ErrorType.VALIDATION, f"{path.name}: not a readable PDF ({type(exc).__name__})")


def _field(text: str, label: str) -> Optional[str]:
    m = re.search(rf"^\s*{label}\s*:\s*(.+?)\s*$", text, re.I | re.M)
    return m.group(1).strip() if m else None


def extract_vendor(text: str) -> Optional[str]:
    return _field(text, r"(?:Vendor|Supplier|From)")


def _parse_date(value: str) -> date:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{value}'")


def parse_invoice_text(text: str, source_file: str) -> ParsedInvoice:
    problems: list[str] = []
    company = extract_vendor(text)
    number = _field(text, r"(?:Invoice\s*Number|Invoice\s*No\.?|Invoice\s*#)")
    inv_raw = _field(text, "Invoice Date")
    due_raw = _field(text, r"(?:Due\s*Date|Payment\s*Due)")
    amount_raw = _field(text, r"(?:Total\s*Due|Amount\s*Due|Balance\s*Due|Total)")
    currency = _field(text, "Currency")
    if not currency and amount_raw:  # e.g. "Total: EUR 1,250.50" or "Total: €1,250.50"
        m = re.search(r"\b([A-Z]{3})\b", amount_raw)
        currency = m.group(1) if m else next((c for s, c in _SYMBOLS.items() if s in amount_raw), None)

    for label, val in (
        ("Vendor", company),
        ("Invoice Number", number),
        ("Invoice Date", inv_raw),
        ("Due Date", due_raw),
        ("Total Due", amount_raw),
        ("Currency", currency),
    ):
        if not val:
            problems.append(f"missing field: {label}")

    inv_date = due_date = None
    amount: Optional[Decimal] = None
    if inv_raw:
        try:
            inv_date = _parse_date(inv_raw)
        except ValueError as e:
            problems.append(f"Invoice Date: {e}")
    if due_raw:
        try:
            due_date = _parse_date(due_raw)
        except ValueError as e:
            problems.append(f"Due Date: {e}")
    if inv_date and due_date and due_date < inv_date:
        problems.append("Due Date is before Invoice Date")
    if amount_raw:
        try:
            amount = Decimal(re.sub(r"[,$€£₹\s]|\b[A-Za-z]{3}\b", "", amount_raw))
            if amount <= 0 or amount > Decimal("10000000"):
                problems.append("Total Due out of range")
            if amount.as_tuple().exponent < -2:  # type: ignore[operator]
                problems.append("Total Due has more than 2 decimal places")
        except InvalidOperation:
            problems.append(f"Total Due: not a number '{amount_raw}'")
    if number and not _NUMBER_RE.match(number):
        problems.append(f"Invoice Number has unexpected format '{number}'")
    if currency and not re.fullmatch(r"[A-Z]{3}", currency):
        problems.append(f"Currency must be a 3-letter code, got '{currency}'")

    if problems:
        raise ToolFailure(
            ErrorType.VALIDATION,
            f"{source_file}: invalid invoice data - " + "; ".join(problems),
            {"problems": problems},
        )

    warnings = []
    if _INJECTION_RE.search(text):
        warnings.append(
            f"{source_file}: contains instruction-like text. Treated as untrusted data and ignored."
        )
    return ParsedInvoice(
        company=company,  # type: ignore[arg-type]
        invoice_number=number,  # type: ignore[arg-type]
        invoice_date=inv_date,  # type: ignore[arg-type]
        due_date=due_date,  # type: ignore[arg-type]
        amount=amount,  # type: ignore[arg-type]
        currency=currency,  # type: ignore[arg-type]
        source_file=source_file,
        warnings=warnings,
    )
