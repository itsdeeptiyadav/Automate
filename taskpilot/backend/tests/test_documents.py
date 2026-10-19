import io
import zipfile

import pytest
from reportlab.pdfgen import canvas

from app.documents.chunking import chunk_text
from app.documents.search import bm25_search, quote_in_text

POLICY = (
    "Refund policy\n\nCustomers may return unused products within 30 days of delivery for a full refund. "
    "Refunds are issued to the original payment method within 5 business days.\n\n"
    "Shipping\n\nStandard shipping takes 3 to 5 business days. Express shipping is available for an extra fee.\n\n"
    "Warranty\n\nAll devices carry a two year limited warranty covering manufacturing defects."
)


def pdf_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in text.split("\n"):
        c.drawString(40, y, line[:100])
        y -= 16
    c.save()
    return buf.getvalue()


def docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def upload(client, name, data, ctype="application/octet-stream"):
    return client.post("/api/v1/documents", files={"file": (name, data, ctype)})


@pytest.mark.parametrize("name,data", [
    ("policy.txt", POLICY.encode()),
    ("policy.md", ("# Policy\n\n" + POLICY).encode()),
    ("policy.csv", b"topic,rule\nrefund,30 days\nshipping,3 to 5 business days\n"),
    ("policy.pdf", pdf_bytes(POLICY)),
    ("policy.docx", docx_bytes(POLICY.split("\n\n"))),
])
def test_every_supported_type_is_extracted_and_listed(client, name, data):
    r = upload(client, name, data)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["kind"] == name.rsplit(".", 1)[1] and d["char_count"] > 20 and d["chunk_count"] >= 1
    assert client.get("/api/v1/documents").json()[0]["id"] == d["id"]
    assert len(client.get(f"/api/v1/documents/{d['id']}").json()["preview"]) > 10


def test_rejections_are_clear_and_save_nothing(client):
    assert upload(client, "x.exe", b"MZ").status_code == 415
    assert upload(client, "empty.txt", b"").status_code == 422
    assert upload(client, "fake.pdf", b"not a pdf").status_code == 415
    assert upload(client, "fake.docx", b"not a zip").status_code == 415
    assert upload(client, "bin.txt", b"abc\x00def" * 10).status_code == 415
    assert upload(client, "blank.pdf", pdf_bytes("")).status_code == 422  # no extractable text
    assert client.get("/api/v1/documents").json() == []


def test_duplicate_upload_is_rejected(client):
    assert upload(client, "a.txt", POLICY.encode()).status_code == 201
    r = upload(client, "copy-of-a.txt", POLICY.encode())
    assert r.status_code == 409 and "already in the library" in r.json()["detail"]


def test_size_limit(make_client):
    client, _ = make_client(max_document_bytes=1000)
    assert upload(client, "big.txt", b"a" * 2000).status_code == 413


def test_search_finds_the_right_passage_and_delete_removes_it(client):
    a = upload(client, "policy.txt", POLICY.encode()).json()
    upload(client, "other.txt", b"Quarterly roadmap: launch the mobile app in March and hire two engineers.")
    hits = client.get("/api/v1/documents/search", params={"q": "refund within 30 days"}).json()
    assert hits and hits[0]["document_id"] == a["id"] and "30 days" in hits[0]["snippet"]
    assert client.get("/api/v1/documents/search", params={"q": "mobile app"}).json()[0]["filename"] == "other.txt"
    assert client.get("/api/v1/documents/search", params={"q": "zebra"}).json() == []
    assert client.delete(f"/api/v1/documents/{a['id']}").status_code == 204
    assert client.get(f"/api/v1/documents/{a['id']}").status_code == 404
    assert client.get("/api/v1/documents/search", params={"q": "refund"}).json() == []


def test_stored_file_name_never_uses_client_path(make_client):
    client, app = make_client()
    d = upload(client, "../../etc/evil name.txt", POLICY.encode()).json()
    files = [p.name for p in app.state.settings.documents_dir.iterdir()]
    assert files == [f"{d['id']}_evil_name.txt"]


def test_chunks_cover_the_text_and_quotes_are_checked_leniently():
    chunks = chunk_text(POLICY * 5, 300, 50)
    assert all((POLICY * 5)[s:s + len(t)] == t for s, t in chunks)
    assert quote_in_text("within 30  DAYS of delivery", POLICY)
    assert not quote_in_text("within 60 days of delivery", POLICY)
    assert bm25_search([], "x") == []


def test_plurals_and_verb_forms_match(client):
    upload(client, "policy.txt", POLICY.encode())
    for q in ("refunds", "returning products", "warranties covering defect", "shipped"):
        assert client.get("/api/v1/documents/search", params={"q": q}).json(), q
    hit = client.get("/api/v1/documents/search", params={"q": "refunds"}).json()[0]
    assert "refund" in hit["snippet"].lower()
