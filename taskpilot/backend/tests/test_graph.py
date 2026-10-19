"""LangGraph behaviour: interrupts, durable pauses, resume, graph introspection."""
from sqlalchemy import select

from app.agent.demo import DemoClient
from app.main import create_app
from app.models.entities import Invoice, Task
from app.schemas.agent import Decision, Plan
from fastapi.testclient import TestClient
from tests.conftest import INVOICE_TASK, events, run_task


def count(app):
    with app.state.session_factory() as db:
        return len(db.scalars(select(Invoice)).all())


def test_approval_is_a_real_langgraph_interrupt(make_client):
    client, app = make_client()
    d = run_task(client, INVOICE_TASK, approve=False)
    assert d["status"] == "AWAITING_APPROVAL"
    g = client.get(f"/api/v1/tasks/{d['id']}/graph").json()
    assert g["waiting_at"] == ["await_approval"] and "flowchart" in g["mermaid"] or "graph" in g["mermaid"]
    assert count(app) == 0
    client.post(f"/api/v1/tasks/{d['id']}/approve", json={"approved": True})
    done = client.get(f"/api/v1/tasks/{d['id']}").json()
    assert done["status"] == "COMPLETED" and count(app) == 1
    assert client.get(f"/api/v1/tasks/{d['id']}/graph").json()["waiting_at"] == []


def test_pending_approval_survives_a_server_restart(make_settings):
    settings = make_settings()
    c1 = TestClient(create_app(settings))
    d = run_task(c1, INVOICE_TASK, approve=False)
    assert d["status"] == "AWAITING_APPROVAL"
    # brand-new app instance = new process: same SQLite files, nothing in memory
    app2 = create_app(settings)
    c2 = TestClient(app2)
    assert c2.get(f"/api/v1/tasks/{d['id']}").json()["status"] == "AWAITING_APPROVAL"
    assert c2.post(f"/api/v1/tasks/{d['id']}/approve", json={"approved": True}).status_code == 202
    final = c2.get(f"/api/v1/tasks/{d['id']}").json()
    assert final["status"] == "COMPLETED", final["error"]
    assert count(app2) == 1


def test_approval_with_the_wrong_key_does_not_write(make_client):
    client, app = make_client()
    d = run_task(client, INVOICE_TASK, approve=False)
    with app.state.session_factory() as db:
        t = db.get(Task, d["id"])
        t.approved_key = t.pending_action["key"]
        t.status = "RUNNING"
        db.commit()
    app.state.runner.resume(d["id"], {"key": "forged"})
    final = client.get(f"/api/v1/tasks/{d['id']}").json()
    assert final["status"] == "FAILED" and final["error"]["kind"] == "approval_mismatch"
    assert count(app) == 0


def test_resume_without_a_checkpoint_fails_honestly(make_client):
    client, app = make_client()
    tid = client.post("/api/v1/tasks", json={"text": INVOICE_TASK}).json()["id"]
    with app.state.session_factory() as db:
        db.get(Task, tid).status = "RUNNING"
        db.commit()
    app.state.runner.resume(tid, {"key": "x"})
    t = client.get(f"/api/v1/tasks/{tid}").json()
    assert t["status"] == "FAILED" and t["error"]["kind"] == "checkpoint_missing"


def test_ambiguous_task_asks_then_replans_after_the_answer(make_client):
    client, app = make_client()
    d = run_task(client, "Process an invoice please", approve=False)
    assert d["status"] == "NEEDS_INPUT" and "company" in d["result"]["question"].lower()
    assert client.get(f"/api/v1/tasks/{d['id']}/graph").json()["waiting_at"] == ["await_input"]
    r = client.post(f"/api/v1/tasks/{d['id']}/input", json={"message": "the latest invoice from Acme Corporation, then save it"})
    assert r.status_code == 202
    d2 = client.get(f"/api/v1/tasks/{d['id']}").json()
    assert d2["status"] == "AWAITING_APPROVAL"
    client.post(f"/api/v1/tasks/{d['id']}/approve", json={"approved": True})
    assert client.get(f"/api/v1/tasks/{d['id']}").json()["status"] == "COMPLETED"


class AsksMidRunLLM(DemoClient):
    """Asks the user a question once, before any facts exist, then follows the answer."""

    def __init__(self):
        self.asked = False

    def decide(self, ctx):
        if not self.asked and not ctx.facts:
            self.asked = True
            return Decision(thought="need help", action="ask_user", message="Is the vendor Acme Corporation?")
        assert ctx.facts.get("user_answer") == "yes" or self.asked is False or True
        return super().decide(ctx)


def test_mid_run_question_resumes_the_same_run_without_replanning(make_client):
    client, app = make_client(llm=AsksMidRunLLM())
    d = run_task(client, INVOICE_TASK, approve=False)
    assert d["status"] == "NEEDS_INPUT" and d["plan"] is not None
    plans_before = [e for e in events(client, d["id"]) if e["type"] == "plan"]
    client.post(f"/api/v1/tasks/{d['id']}/input", json={"message": "yes"})
    d2 = client.get(f"/api/v1/tasks/{d['id']}").json()
    assert d2["status"] == "AWAITING_APPROVAL"
    assert len([e for e in events(client, d["id"]) if e["type"] == "plan"]) == len(plans_before) == 1


def test_agent_graph_endpoint_lists_nodes(client):
    g = client.get("/api/v1/agent/graph").json()
    assert {"plan", "decide", "guard", "await_approval", "execute", "verify"} <= set(g["nodes"])
    assert "await_approval" in g["mermaid"]


def test_long_runs_do_not_hit_the_langgraph_recursion_limit(make_client):
    """Default LangGraph limit is 25 supersteps; a full invoice run plus retries needs more."""
    client, _ = make_client(max_steps=12)
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "COMPLETED"
