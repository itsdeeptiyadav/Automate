import sqlalchemy.exc
import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import select

from app.enums import ErrorType
from app.models.entities import Invoice, Verification
from app.tools.base import ToolFailure
from app.tools.pdf import extract_pdf_text, parse_invoice_text


def _call(registry, ctx, name, args):
    tool, parsed = registry.prepare(name, args)
    return registry.invoke(tool, parsed, ctx)


def test_extracts_actual_pdf_content(workspace):
    text = extract_pdf_text(workspace / "invoices" / "invoice_003.pdf")
    inv = parse_invoice_text(text, "invoices/invoice_003.pdf")
    assert (inv.invoice_number, str(inv.amount), inv.due_date.isoformat(), inv.currency) == (
        "ACM-2026-0467", "4275.00", "2026-10-22", "USD")


def test_extraction_is_not_hardcoded(workspace, tool_ctx, registry):
    """A brand-new PDF with values that exist nowhere in the code must be read correctly."""
    c = canvas.Canvas(str(workspace / "invoices" / "new.pdf"))
    for i, line in enumerate([
        "Vendor: Zephyr Works", "Invoice Number: ZW-777", "Invoice Date: 2027-01-05",
        "Due Date: 2027-02-04", "Currency: GBP", "Total Due: 7,777.77"]):
        c.drawString(50, 780 - 20 * i, line)
    c.save()
    r = _call(registry, tool_ctx, "read_invoice", {"path": "invoices/new.pdf"})
    assert r.ok and r.data["invoice"]["amount"] == "7777.77" and r.data["invoice"]["invoice_number"] == "ZW-777"


def test_latest_invoice_is_selected_by_date_not_filename_or_lookalike(registry, tool_ctx):
    r = _call(registry, tool_ctx, "get_latest_invoice", {"company": "Acme Corporation"})
    assert r.ok
    assert r.data["latest"]["invoice_number"] == "ACM-2026-0467"  # invoice_003.pdf, not first/last file
    assert [c["invoice_number"] for c in r.data["candidates_by_date"]] == ["ACM-2026-0467", "ACM-2026-0412", "ACM-2026-0388"]
    assert all(c["invoice_number"] != "AI-2026-0099" for c in r.data["candidates_by_date"])  # Acme Industries Ltd excluded


def test_injected_instructions_in_pdf_are_flagged_not_obeyed(registry, tool_ctx):
    r = _call(registry, tool_ctx, "get_latest_invoice", {"company": "Acme Corporation"})
    assert any("invoice_004.pdf" in w for w in r.warnings)
    assert r.data["latest"]["invoice_number"] == "ACM-2026-0467"


def test_missing_invoice_returns_not_found_without_fabrication(registry, tool_ctx):
    r = _call(registry, tool_ctx, "get_latest_invoice", {"company": "Nonexistent Corp"})
    assert not r.ok and r.error_type == ErrorType.NOT_FOUND
    assert "latest" not in r.data


def test_save_then_read_back_and_verify(registry, tool_ctx):
    db = tool_ctx.db
    latest = _call(registry, tool_ctx, "get_latest_invoice", {"company": "Acme Corporation"}).data["latest"]
    saved = _call(registry, tool_ctx, "create_invoice_record", latest)
    assert saved.ok
    row = db.scalars(select(Invoice)).one()
    assert (row.invoice_number, row.amount_cents, row.due_date) == ("ACM-2026-0467", 427500, "2026-10-22")
    v = _call(registry, tool_ctx, "verify_invoice_record", {"invoice_number": row.invoice_number, "source_file": row.source_file})
    assert v.ok and v.data["passed"] is True
    assert db.scalars(select(Verification)).one().passed is True


def test_incorrect_saved_amount_fails_verification(registry, tool_ctx):
    db = tool_ctx.db
    latest = _call(registry, tool_ctx, "get_latest_invoice", {"company": "Acme Corporation"}).data["latest"]
    _call(registry, tool_ctx, "create_invoice_record", latest)
    row = db.scalars(select(Invoice)).one()
    row.amount_cents = 100  # corrupt the stored value
    db.commit()
    v = _call(registry, tool_ctx, "verify_invoice_record", {"invoice_number": row.invoice_number, "source_file": row.source_file})
    assert v.ok and v.data["passed"] is False
    assert [c["field"] for c in v.data["checks"] if not c["match"]] == ["amount"]


def test_duplicate_insert_rejected_by_tool_and_by_database(registry, tool_ctx):
    db = tool_ctx.db
    latest = _call(registry, tool_ctx, "get_latest_invoice", {"company": "Acme Corporation"}).data["latest"]
    assert _call(registry, tool_ctx, "create_invoice_record", latest).ok
    second = _call(registry, tool_ctx, "create_invoice_record", latest)
    assert not second.ok and second.error_type == ErrorType.CONFLICT
    assert len(db.scalars(select(Invoice)).all()) == 1
    # the constraint itself, bypassing the tool
    db.add(Invoice(company=latest["company"], invoice_number=latest["invoice_number"], invoice_date="2026-01-01",
                   due_date="2026-02-01", amount_cents=1, currency="USD", source_file="x", source_sha256="y"))
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize("path", ["../../backend/.env", "/etc/passwd", "C:\\Windows\\win.ini", "invoices/../../README.md"])
def test_path_traversal_blocked(registry, tool_ctx, path):
    r = _call(registry, tool_ctx, "read_text_file", {"path": path})
    assert not r.ok and r.error_type in (ErrorType.PERMISSION, ErrorType.NOT_FOUND)
    assert r.error_type == ErrorType.PERMISSION or "not found" in r.error


def test_tool_argument_validation(registry):
    with pytest.raises(ToolFailure) as e:
        registry.prepare("create_invoice_record", {"company": "A", "amount": "-5"})
    assert e.value.error_type == ErrorType.VALIDATION
    with pytest.raises(ToolFailure):
        registry.prepare("run_shell", {"cmd": "ls"})  # unregistered tool


def test_registry_has_no_shell_or_arbitrary_execution(registry):
    names = set(registry.names())
    assert {"get_latest_invoice", "create_invoice_record", "verify_invoice_record", "search_file_contents"} <= names
    assert not any(n in names for n in ("run_shell", "exec", "python", "bash"))
    assert [t["name"] for t in registry.describe() if t["requires_approval"]] == ["create_invoice_record"]
