from tests.conftest import INVOICE_TASK, run_task


def test_health_reports_provider_and_demo_label(client):
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["llm_is_demo"] is True and h["llm_provider"] == "demo"


def test_task_crud_and_status_codes(client):
    assert client.post("/api/v1/tasks", json={"text": "x"}).status_code == 422
    t = client.post("/api/v1/tasks", json={"text": INVOICE_TASK})
    assert t.status_code == 201 and t.json()["status"] == "PENDING"
    tid = t.json()["id"]
    assert client.get("/api/v1/tasks/does-not-exist").status_code == 404
    assert client.get(f"/api/v1/tasks/{tid}/events").json()[0]["title"] == "Task created"
    assert any(x["id"] == tid for x in client.get("/api/v1/tasks").json())
    assert client.post(f"/api/v1/tasks/{tid}/start").status_code == 202
    assert client.post(f"/api/v1/tasks/{tid}/start").status_code == 409  # cannot start twice


def test_invoice_endpoints_show_real_rows(client):
    assert client.get("/api/v1/invoices").json() == []
    run_task(client, INVOICE_TASK)
    items = client.get("/api/v1/invoices").json()
    assert len(items) == 1 and items[0]["amount"] == "4275.00"
    assert client.get(f"/api/v1/invoices/{items[0]['id']}").json()["invoice_number"] == "ACM-2026-0467"
    assert client.get("/api/v1/invoices/999").status_code == 404


def test_events_after_cursor(client):
    d = run_task(client, INVOICE_TASK)
    evs = client.get(f"/api/v1/tasks/{d['id']}/events").json()
    later = client.get(f"/api/v1/tasks/{d['id']}/events", params={"after": evs[5]["seq"]}).json()
    assert len(later) == len(evs) - 6
