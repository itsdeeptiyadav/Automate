from app.tools.pdf import parse_invoice_text
from tests.conftest import run_task


def _take_pdf(workspace, name="invoice_006.pdf"):
    """Remove a sample invoice from the workspace and return its bytes (to upload it instead)."""
    path = workspace / "invoices" / name
    data = path.read_bytes()
    path.unlink()
    return data


def _post(client, data, name="My Invoice.pdf"):
    return client.post("/api/v1/invoices/upload", files={"file": (name, data, "application/pdf")})


def test_uploaded_pdf_is_saved_and_the_agent_can_process_it(make_client, workspace):
    data = _take_pdf(workspace)
    client, _ = make_client()
    r = _post(client, data)
    assert r.status_code == 201
    body = r.json()
    assert (body["company"], body["invoice_number"], body["amount"]) == ("Initech LLC", "INI-0042", "5500.00")
    assert body["file"].startswith("invoices/upload_") and body["file"].endswith("_My_Invoice.pdf")
    assert (workspace / body["file"]).is_file()
    d = run_task(client, body["suggested_task"])
    assert d["status"] == "COMPLETED", d["error"]
    assert d["result"]["evidence"]["source_file"] == body["file"]


def test_upload_rejects_wrong_types(make_client):
    client, _ = make_client()
    assert _post(client, b"hello", "notes.txt").status_code == 415
    assert _post(client, b"hello", "fake.pdf").status_code == 415  # no %PDF header


def test_upload_rejects_unreadable_pdf_and_cleans_up(make_client, workspace):
    client, _ = make_client()
    before = sorted(p.name for p in (workspace / "invoices").iterdir())
    assert _post(client, b"%PDF-1.4 this is not a real pdf").status_code == 422
    assert sorted(p.name for p in (workspace / "invoices").iterdir()) == before


def test_upload_size_limit(make_client):
    client, _ = make_client(max_file_bytes=100)
    assert _post(client, b"%PDF-1.4" + b"x" * 500).status_code == 413


def test_upload_duplicate_is_rejected(make_client, workspace):
    data = _take_pdf(workspace)
    client, _ = make_client()
    assert _post(client, data).status_code == 201
    assert _post(client, data, "again.pdf").status_code == 409


def test_upload_filename_cannot_escape_the_invoices_folder(make_client, workspace):
    data = _take_pdf(workspace)
    client, _ = make_client()
    r = _post(client, data, "../../evil.pdf")
    assert r.status_code == 201
    assert (workspace / r.json()["file"]).resolve().is_relative_to((workspace / "invoices").resolve())


def test_parser_accepts_common_label_variants_and_currency_symbols():
    text = "Supplier: Delta Works\nInvoice No: DW-100\nInvoice Date: 2026-10-01\nPayment Due: 2026-10-31\nTotal: \u20ac1,250.50\n"
    p = parse_invoice_text(text, "invoices/x.pdf")
    assert (p.company, p.invoice_number, p.currency, str(p.amount)) == ("Delta Works", "DW-100", "EUR", "1250.50")
