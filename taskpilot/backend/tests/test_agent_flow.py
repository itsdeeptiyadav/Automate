from sqlalchemy import select, func

from app.agent.llm import build_llm
from app.config import Settings
from app.models.entities import Invoice, Verification
from tests.conftest import FILE_TASK, INVOICE_TASK, events, run_task
from tests.helpers import (EarlyFinishLLM, GarbageLLM, HallucinatedPlanLLM, TraversalLLM, UnknownToolLLM,
                           WrongAmountLLM, WrongFilesLLM, WrongInvoiceLLM)


def rows(app):
    with app.state.session_factory() as db:
        return db.scalars(select(Invoice)).all()


# ---- primary workflow ---------------------------------------------------------

def test_invoice_workflow_end_to_end(make_client):
    client, app = make_client()
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "COMPLETED", d["error"]
    inv = rows(app)
    assert len(inv) == 1
    assert (inv[0].invoice_number, inv[0].amount_cents, inv[0].due_date, inv[0].currency) == ("ACM-2026-0467", 427500, "2026-10-22", "USD")
    assert d["result"]["evidence"]["source_file"] == "invoices/invoice_003.pdf"
    assert all(c["match"] for c in d["result"]["checks"])
    kinds = {v["kind"] for v in d["verifications"]}
    assert kinds == {"tool", "final"} and all(v["passed"] for v in d["verifications"])
    types = [e["type"] for e in events(client, d["id"])]
    for needed in ("plan", "tool_call", "tool_result", "approval_requested", "approval_granted", "verification", "final", "security"):
        assert needed in types


def test_write_requires_approval_and_nothing_is_written_before_it(make_client):
    client, app = make_client()
    d = run_task(client, INVOICE_TASK, approve=False)
    assert d["status"] == "AWAITING_APPROVAL"
    assert d["pending_action"]["tool"] == "create_invoice_record"
    assert rows(app) == []
    # approve endpoint refuses when nothing is pending
    other = client.post("/api/v1/tasks", json={"text": INVOICE_TASK}).json()["id"]
    assert client.post(f"/api/v1/tasks/{other}/approve", json={"approved": True}).status_code == 409
    # denying leaves the DB untouched
    r = client.post(f"/api/v1/tasks/{d['id']}/approve", json={"approved": False}).json()
    assert r["status"] == "CANCELLED" and rows(app) == []


def test_duplicate_processing_creates_no_second_record(make_client):
    client, app = make_client()
    assert run_task(client, INVOICE_TASK)["status"] == "COMPLETED"
    second = run_task(client, INVOICE_TASK)
    assert second["status"] == "COMPLETED"
    assert second["pending_action"] is None  # no write was needed
    assert len(rows(app)) == 1


def test_missing_invoice_is_not_reported_as_success(make_client):
    client, app = make_client()
    d = run_task(client, "Find the latest invoice from Nonexistent Corp, extract its details and save it.")
    assert d["status"] == "FAILED"
    assert d["error"]["kind"] == "agent_gave_up" and d["result"] is None
    assert rows(app) == []


def test_wrong_amount_is_rejected_before_any_approval_or_write(make_client):
    client, app = make_client(llm=WrongAmountLLM())
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "FAILED" and rows(app) == []  # nothing was written
    evs = events(client, d["id"])
    assert "approval_requested" not in [e["type"] for e in evs]
    assert any("do not match" in e["title"] for e in evs if e["type"] == "tool_error")


def test_agent_that_picks_the_wrong_invoice_cannot_complete(make_client):
    client, app = make_client(llm=WrongInvoiceLLM())
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "FAILED"


def test_llm_claiming_success_without_work_is_rejected(make_client):
    client, app = make_client(llm=EarlyFinishLLM())
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "FAILED" and d["error"]["kind"] == "verification_failed"
    assert rows(app) == []


# ---- failure handling ---------------------------------------------------------

def test_unknown_tool_request_is_rejected_and_run_continues(make_client):
    client, _ = make_client(llm=UnknownToolLLM())
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "COMPLETED"
    errs = [e for e in events(client, d["id"]) if e["type"] == "tool_error"]
    assert errs and "Unknown tool" in errs[0]["title"]


def test_path_traversal_attempt_by_agent_is_blocked(make_client):
    client, _ = make_client(llm=TraversalLLM())
    d = run_task(client, INVOICE_TASK)
    errs = [e for e in events(client, d["id"]) if e["type"] == "tool_error"]
    assert errs and errs[0]["payload"]["error_type"] == "PERMISSION"
    assert d["status"] == "COMPLETED"


def test_transient_read_failure_is_retried(make_client):
    client, _ = make_client(fault_injection="read_flaky_once")
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "COMPLETED"
    assert any(e["type"] == "retry" for e in events(client, d["id"]))


def test_write_timeout_checks_database_before_retry_and_never_duplicates(make_client):
    client, app = make_client(fault_injection="write_timeout_once")
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "COMPLETED", d["error"]
    assert len(rows(app)) == 1
    rec = [e for e in events(client, d["id"]) if e["type"] == "recovery"]
    assert rec and "Inspected the database" in rec[0]["title"]


def test_llm_unavailable_is_reported_honestly(make_client):
    llm = build_llm(Settings(llm_provider="ollama", ollama_base_url="http://127.0.0.1:9", ollama_model="mistral",
                             llm_timeout_seconds=2, llm_max_repairs=0, _env_file=None))
    client, app = make_client(llm=llm)
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "FAILED"
    assert d["error"]["kind"] == "llm_unavailable" and d["error"]["error_type"] == "LLM"
    assert "Cannot reach Ollama" in d["error"]["message"]
    assert d["result"] is None and rows(app) == []


def test_invalid_llm_output_fails_instead_of_guessing(make_client):
    client, app = make_client(llm=GarbageLLM())
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "FAILED" and d["error"]["kind"] == "llm_invalid_output"


def test_plan_with_invented_company_is_refused(make_client):
    client, app = make_client(llm=HallucinatedPlanLLM())
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "FAILED" and d["error"]["kind"] == "plan_invalid"
    assert rows(app) == []


def test_step_limit_stops_the_agent(make_client):
    client, _ = make_client(max_steps=2)
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "FAILED" and d["error"]["kind"] == "step_limit"


# ---- clarification / cancel / unsupported ------------------------------------------

def test_missing_company_asks_for_input_then_resumes(make_client):
    client, app = make_client()
    d = run_task(client, "Process the latest invoice please")
    assert d["status"] == "NEEDS_INPUT" and "company" in d["result"]["question"].lower()
    r = client.post(f"/api/v1/tasks/{d['id']}/input", json={"message": "invoice from Acme Corporation"})
    assert r.status_code == 202
    d = client.get(f"/api/v1/tasks/{d['id']}").json()
    assert d["status"] == "AWAITING_APPROVAL"


def test_unsupported_task_fails_cleanly(make_client):
    client, _ = make_client()
    d = run_task(client, "Book me a flight to Paris")
    assert d["status"] == "FAILED" and d["error"]["kind"] == "unsupported_task"


def test_cancel_while_awaiting_approval(make_client):
    client, app = make_client()
    d = run_task(client, INVOICE_TASK, approve=False)
    r = client.post(f"/api/v1/tasks/{d['id']}/cancel").json()
    assert r["status"] == "CANCELLED" and rows(app) == []
    assert client.post(f"/api/v1/tasks/{d['id']}/approve", json={"approved": True}).status_code == 409
    assert client.post(f"/api/v1/tasks/{d['id']}/cancel").status_code == 409


# ---- second workflow: reuse of the same loop + registry ---------------------------------

def test_file_search_workflow(make_client):
    client, _ = make_client()
    d = run_task(client, FILE_TASK)
    assert d["status"] == "COMPLETED", d["error"]
    files = [f["path"] for f in d["result"]["evidence"]["files"]]
    assert files == [
        "workspace/legal/nda_summary.txt", "workspace/misc/vendor_call.txt",
        "workspace/notes/meeting_notes.txt", "workspace/projects/roadmap.txt",
    ]  # 'contractor' / 'Subcontracting' do not count as the word
    assert d["result"]["data"]["files"] == files
    types = {e["tool"] for e in events(client, d["id"]) if e["type"] == "tool_call"}
    assert types == {"search_file_contents", "read_text_file"}
    assert any(e["type"] == "security" for e in events(client, d["id"]))  # injected line flagged


def test_file_search_with_wrong_claimed_files_fails_verification(make_client):
    client, _ = make_client(llm=WrongFilesLLM())
    d = run_task(client, FILE_TASK)
    assert d["status"] == "FAILED" and d["error"]["kind"] == "verification_failed"


def test_injected_text_does_not_change_workspace(make_client, workspace):
    before = sorted(p.name for p in workspace.rglob("*") if p.is_file())
    client, _ = make_client()
    run_task(client, FILE_TASK)
    assert sorted(p.name for p in workspace.rglob("*") if p.is_file()) == before
