"""Invoice tools: real PDF reading, deterministic latest-invoice selection,
SQLite writes through SQLAlchemy and independent read-back verification."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.enums import ErrorType
from app.models.entities import Invoice, Verification
from app.tools.base import Tool, ToolContext, ToolFailure, ToolResult
from app.tools.faults import maybe_fail
from app.tools.pdf import (
    ParsedInvoice,
    extract_pdf_text,
    extract_vendor,
    normalize_company,
    parse_invoice_text,
)
from app.tools.safety import rel_posix, resolve_safe

# --------------------------------------------------------------------------- helpers


@dataclass
class ScanResult:
    invoices: list[ParsedInvoice] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    vendors: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def scan_company_invoices(ctx: ToolContext, company: str) -> ScanResult:
    """Read every PDF in the invoices folder; keep valid invoices whose Vendor
    line equals `company` exactly (so 'Acme Industries Ltd' != 'Acme Corporation')."""
    inv_dir = ctx.settings.invoices_dir
    if not inv_dir.is_dir():
        raise ToolFailure(ErrorType.NOT_FOUND, "invoices directory does not exist in the workspace")
    target = normalize_company(company)
    out = ScanResult()
    for path in sorted(inv_dir.rglob("*.pdf")):
        rel = rel_posix(ctx.root, path)
        if path.stat().st_size > ctx.settings.max_file_bytes:
            out.skipped.append({"file": rel, "reason": "file too large"})
            continue
        try:
            text = extract_pdf_text(path)
        except ToolFailure as exc:
            out.skipped.append({"file": rel, "reason": exc.message})
            continue
        vendor = extract_vendor(text)
        if not vendor:
            out.skipped.append({"file": rel, "reason": "no Vendor line found"})
            continue
        out.vendors.add(vendor)
        if normalize_company(vendor) != target:
            continue
        try:
            parsed = parse_invoice_text(text, rel)
        except ToolFailure as exc:
            out.skipped.append({"file": rel, "reason": exc.message})
            continue
        out.invoices.append(parsed)
        out.warnings.extend(parsed.warnings)
    return out


def pick_latest(invoices: list[ParsedInvoice]) -> ParsedInvoice:
    """Deterministic: greatest invoice_date; ties broken by invoice_number."""
    return max(invoices, key=lambda i: (i.invoice_date, i.invoice_number))


def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_invoice(row: Invoice, parsed: ParsedInvoice) -> list[dict[str, Any]]:
    """Field-by-field comparison of the stored row (actual) vs the PDF (expected)."""
    pairs = [
        ("company", parsed.company, row.company),
        ("invoice_number", parsed.invoice_number, row.invoice_number),
        ("invoice_date", parsed.invoice_date.isoformat(), row.invoice_date),
        ("due_date", parsed.due_date.isoformat(), row.due_date),
        ("amount", f"{parsed.amount:.2f}", f"{row.amount:.2f}"),
        ("currency", parsed.currency, row.currency),
        ("source_file", parsed.source_file, row.source_file),
    ]
    return [{"field": f, "expected": e, "actual": a, "match": e == a} for f, e, a in pairs]


def store_verification(
    ctx: ToolContext, invoice_id: Optional[int], kind: str, checks: list[dict[str, Any]]
) -> bool:
    passed = bool(checks) and all(c["match"] for c in checks)
    ctx.db.add(
        Verification(task_id=ctx.task_id, invoice_id=invoice_id, kind=kind, passed=passed, checks=checks)
    )
    ctx.db.commit()
    return passed


# --------------------------------------------------------------------------- args


class CompanyArgs(BaseModel):
    company: str = Field(min_length=2, max_length=200, description="Exact vendor/company name, e.g. 'Acme Corporation'")


class ReadInvoiceArgs(BaseModel):
    path: str = Field(description="Workspace-relative path of an invoice PDF, e.g. 'invoices/invoice_003.pdf'")


class SearchInternalArgs(BaseModel):
    company: Optional[str] = Field(default=None, max_length=200, description="Company name filter")
    invoice_number: Optional[str] = Field(default=None, max_length=64, description="Invoice number filter")

    @model_validator(mode="after")
    def _need_one(self):
        if not (self.company or self.invoice_number):
            raise ValueError("provide company and/or invoice_number")
        return self


class CreateInvoiceArgs(BaseModel):
    company: str = Field(min_length=2, max_length=200)
    invoice_number: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9\-/]{2,31}$")
    invoice_date: date = Field(description="ISO date yyyy-mm-dd")
    due_date: date = Field(description="ISO date yyyy-mm-dd")
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description="Total amount due, e.g. 4275.00")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source_file: str = Field(description="Workspace-relative path of the source PDF")

    @field_validator("amount", mode="before")
    @classmethod
    def _clean_amount(cls, v):
        if isinstance(v, str):
            return v.replace(",", "").replace("$", "").strip()
        return v

    @field_serializer("amount")
    def _ser_amount(self, v: Decimal) -> str:
        # "4275" and "4275.00" are the same call: one canonical form keeps duplicate-write
        # detection and the approval hash stable.
        return f"{v:.2f}"

    @model_validator(mode="after")
    def _dates(self):
        if self.due_date < self.invoice_date:
            raise ValueError("due_date is before invoice_date")
        return self


class GetInvoiceRecordArgs(BaseModel):
    invoice_id: Optional[int] = Field(default=None, ge=1)
    invoice_number: Optional[str] = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _need_one(self):
        if self.invoice_id is None and not self.invoice_number:
            raise ValueError("provide invoice_id or invoice_number")
        return self


class VerifyInvoiceArgs(BaseModel):
    invoice_number: str = Field(min_length=3, max_length=64)
    source_file: str = Field(description="Workspace-relative path of the source PDF to compare against")
    company: Optional[str] = Field(default=None, max_length=200)


# --------------------------------------------------------------------------- tools


def search_invoice_files(ctx: ToolContext, args: CompanyArgs) -> ToolResult:
    scan = scan_company_invoices(ctx, args.company)
    files = [
        {"path": i.source_file, "invoice_number": i.invoice_number, "invoice_date": i.invoice_date.isoformat()}
        for i in sorted(scan.invoices, key=lambda i: i.source_file)
    ]
    summary = (
        f"Found {len(files)} invoice PDF(s) from '{args.company}'."
        if files
        else f"No invoice PDFs found for vendor '{args.company}'."
    )
    return ToolResult(
        summary=summary,
        data={"count": len(files), "files": files, "skipped": scan.skipped},
        warnings=scan.warnings,
    )


def read_invoice(ctx: ToolContext, args: ReadInvoiceArgs) -> ToolResult:
    full = resolve_safe(ctx.root, args.path)
    if full.suffix.lower() != ".pdf":
        raise ToolFailure(ErrorType.VALIDATION, "only .pdf files can be read as invoices")
    if full.stat().st_size > ctx.settings.max_file_bytes:
        raise ToolFailure(ErrorType.VALIDATION, "file too large")
    parsed = parse_invoice_text(extract_pdf_text(full), rel_posix(ctx.root, full))
    return ToolResult(
        summary=f"Extracted invoice {parsed.invoice_number} ({parsed.currency} {parsed.amount:.2f}) from {parsed.source_file}.",
        data={"invoice": parsed.to_fields()},
        warnings=parsed.warnings,
    )


def get_latest_invoice(ctx: ToolContext, args: CompanyArgs) -> ToolResult:
    maybe_fail(ctx, "read_flaky_once")
    scan = scan_company_invoices(ctx, args.company)
    if not scan.invoices:
        raise ToolFailure(
            ErrorType.NOT_FOUND,
            f"No valid invoice PDFs found for vendor '{args.company}'.",
            {"vendors_in_workspace": sorted(scan.vendors), "skipped": scan.skipped},
        )
    latest = pick_latest(scan.invoices)
    candidates = [
        {"file": i.source_file, "invoice_number": i.invoice_number, "invoice_date": i.invoice_date.isoformat()}
        for i in sorted(scan.invoices, key=lambda i: (i.invoice_date, i.invoice_number), reverse=True)
    ]
    return ToolResult(
        summary=(
            f"Latest of {len(candidates)} invoice(s) from {args.company}: {latest.invoice_number} "
            f"dated {latest.invoice_date.isoformat()} ({latest.source_file})."
        ),
        data={"latest": latest.to_fields(), "candidates_by_date": candidates, "skipped": scan.skipped},
        memory={"latest_invoice": latest.to_fields()},
        warnings=scan.warnings,
    )


def search_internal_invoices(ctx: ToolContext, args: SearchInternalArgs) -> ToolResult:
    stmt = select(Invoice).order_by(Invoice.invoice_date.desc(), Invoice.id)
    if args.company:
        stmt = stmt.where(Invoice.company == args.company)
    if args.invoice_number:
        stmt = stmt.where(Invoice.invoice_number == args.invoice_number)
    rows = ctx.db.scalars(stmt.limit(25)).all()
    matches = [r.as_dict() for r in rows]
    return ToolResult(
        summary=f"{len(matches)} matching record(s) in the internal invoice system.",
        data={"count": len(matches), "matches": matches},
        memory={"db_matches": matches},
    )


def create_invoice_record(ctx: ToolContext, args: CreateInvoiceArgs) -> ToolResult:
    src = resolve_safe(ctx.root, args.source_file)
    if src.suffix.lower() != ".pdf":
        raise ToolFailure(ErrorType.VALIDATION, "source_file must be a PDF")
    row = Invoice(
        company=args.company,
        invoice_number=args.invoice_number,
        invoice_date=args.invoice_date.isoformat(),
        due_date=args.due_date.isoformat(),
        amount_cents=int((args.amount * 100).to_integral_value()),
        currency=args.currency,
        source_file=rel_posix(ctx.root, src),
        source_sha256=sha256_of(src),
        created_by_task_id=ctx.task_id,
    )
    ctx.db.add(row)
    try:
        ctx.db.commit()
    except IntegrityError:
        ctx.db.rollback()
        existing = ctx.db.scalars(
            select(Invoice).where(
                Invoice.company == args.company, Invoice.invoice_number == args.invoice_number
            )
        ).first()
        raise ToolFailure(
            ErrorType.CONFLICT,
            f"Invoice {args.invoice_number} for {args.company} already exists; no duplicate created.",
            {"existing": existing.as_dict() if existing else None},
        )
    # Test/demo hook: the write above is already committed when this "times out".
    maybe_fail(ctx, "write_timeout_once")
    return ToolResult(
        summary=f"Saved invoice {row.invoice_number} as record #{row.id}.",
        data={"record": row.as_dict()},
        memory={"db_record": row.as_dict()},
    )


def preflight_create_invoice_record(ctx: ToolContext, args: CreateInvoiceArgs) -> Optional[str]:
    """Reject values that differ from the source PDF so a wrong record is never written or approved."""
    try:
        src = resolve_safe(ctx.root, args.source_file)
        if src.suffix.lower() != ".pdf":
            return "source_file must be a PDF"
        parsed = parse_invoice_text(extract_pdf_text(src), rel_posix(ctx.root, src))
    except ToolFailure as exc:
        return exc.message
    pairs = [("company", args.company, parsed.company), ("invoice_number", args.invoice_number, parsed.invoice_number),
             ("invoice_date", args.invoice_date, parsed.invoice_date), ("due_date", args.due_date, parsed.due_date),
             ("amount", args.amount, parsed.amount), ("currency", args.currency, parsed.currency)]
    bad = [f"{n}: you sent {a}, the PDF says {b}" for n, a, b in pairs if a != b]
    if bad:
        return f"Values do not match {parsed.source_file}: " + "; ".join(bad) + ". Copy them exactly from facts.latest_invoice."
    return None


def recover_create_invoice_record(ctx: ToolContext, args: CreateInvoiceArgs) -> Optional[ToolResult]:
    """After a write timeout: look in the DB before retrying the insert."""
    ctx.db.rollback()
    row = ctx.db.scalars(
        select(Invoice).where(Invoice.company == args.company, Invoice.invoice_number == args.invoice_number)
    ).first()
    if row is None:
        return None  # write did not land -> safe to retry
    same = (
        row.invoice_date == args.invoice_date.isoformat()
        and row.due_date == args.due_date.isoformat()
        and row.amount_cents == int((args.amount * 100).to_integral_value())
        and row.currency == args.currency
    )
    if not same:
        return ToolResult.failure(
            ErrorType.CONFLICT,
            f"A different record for {args.invoice_number} already exists; refusing to overwrite.",
            {"existing": row.as_dict()},
        )
    return ToolResult(
        summary=f"Write had timed out, but record #{row.id} already exists with identical values. Not inserting again.",
        data={"record": row.as_dict(), "recovered": True},
        memory={"db_record": row.as_dict()},
    )


def get_invoice_record(ctx: ToolContext, args: GetInvoiceRecordArgs) -> ToolResult:
    stmt = select(Invoice)
    if args.invoice_id is not None:
        stmt = stmt.where(Invoice.id == args.invoice_id)
    if args.invoice_number:
        stmt = stmt.where(Invoice.invoice_number == args.invoice_number)
    rows = ctx.db.scalars(stmt.limit(5)).all()
    if not rows:
        raise ToolFailure(ErrorType.NOT_FOUND, "No such invoice record.")
    return ToolResult(
        summary=f"Read {len(rows)} record(s).",
        data={"records": [r.as_dict() for r in rows]},
        memory={"db_record": rows[0].as_dict()},
    )


def verify_invoice_record(ctx: ToolContext, args: VerifyInvoiceArgs) -> ToolResult:
    """Independent check: re-read the PDF from disk and the row from SQLite, compare."""
    src = resolve_safe(ctx.root, args.source_file)
    parsed = parse_invoice_text(extract_pdf_text(src), rel_posix(ctx.root, src))
    stmt = select(Invoice).where(Invoice.invoice_number == args.invoice_number)
    if args.company:
        stmt = stmt.where(Invoice.company == args.company)
    rows = ctx.db.scalars(stmt).all()
    if len(rows) > 1:
        raise ToolFailure(ErrorType.VALIDATION, "invoice_number is ambiguous; pass company as well")
    if not rows:
        checks = [{"field": "record", "expected": "exists", "actual": "missing", "match": False}]
        store_verification(ctx, None, "tool", checks)
        return ToolResult(
            summary="Verification FAILED: no saved record found.",
            data={"passed": False, "checks": checks},
            memory={"verification": {"passed": False}},
        )
    row = rows[0]
    checks = compare_invoice(row, parsed)
    passed = store_verification(ctx, row.id, "tool", checks)
    bad = [c["field"] for c in checks if not c["match"]]
    return ToolResult(
        summary="Verification PASSED: saved record matches the source PDF."
        if passed
        else f"Verification FAILED: mismatch in {', '.join(bad)}.",
        data={"passed": passed, "invoice_id": row.id, "checks": checks},
        memory={"verification": {"passed": passed, "invoice_id": row.id}},
        warnings=parsed.warnings,
    )


def build_invoice_tools() -> list[Tool]:
    return [
        Tool("search_invoice_files",
             "List invoice PDFs in the workspace whose Vendor line exactly matches the company.",
             CompanyArgs, "read", search_invoice_files),
        Tool("read_invoice",
             "Read ONE invoice PDF and extract company, invoice number, dates, amount and currency.",
             ReadInvoiceArgs, "read", read_invoice),
        Tool("get_latest_invoice",
             "Read every PDF from the company and deterministically select the invoice with the latest invoice date.",
             CompanyArgs, "read", get_latest_invoice),
        Tool("search_internal_invoices",
             "Search the internal invoice system (SQLite) by company and/or invoice number.",
             SearchInternalArgs, "read", search_internal_invoices),
        Tool("create_invoice_record",
             "WRITE: insert an invoice into the internal invoice system. Requires human approval. Duplicates are rejected.",
             CreateInvoiceArgs, "write", create_invoice_record, recover=recover_create_invoice_record,
             preflight=preflight_create_invoice_record),
        Tool("get_invoice_record",
             "Read a saved invoice record back from the internal invoice system.",
             GetInvoiceRecordArgs, "read", get_invoice_record),
        Tool("verify_invoice_record",
             "Independently compare the saved record with the source PDF and store the verification result.",
             VerifyInvoiceArgs, "read", verify_invoice_record),
    ]
