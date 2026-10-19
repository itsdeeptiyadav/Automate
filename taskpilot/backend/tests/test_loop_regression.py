"""Regression tests for the 'approval asked again and again' / stuck-task bugs."""
from sqlalchemy import select

from app.agent.demo import DemoClient
from app.database import init_db, make_engine, make_session_factory
from app.main import create_app
from app.models.entities import Invoice, Task
from app.schemas.agent import Decision
from tests.conftest import INVOICE_TASK, events, run_task


class RepeatCreateLLM(DemoClient):
    """After the save, a weak model asks to save AGAIN (amount typed '4275' instead of '4275.00')."""

    def decide(self, ctx):
        d = super().decide(ctx)
        if d.action == "tool" and d.tool == "verify_invoice_record":
            inv = dict(ctx.facts["latest_invoice"])
            inv["amount"] = str(inv["amount"]).split(".")[0]
            return Decision(thought="save again", action="tool", tool="create_invoice_record", args=inv)
        return d


def test_repeated_write_is_not_re_approved_and_loop_is_stopped(make_client):
    client, app = make_client(RepeatCreateLLM())
    d = run_task(client, INVOICE_TASK)  # approves once
    types = [e["type"] for e in events(client, d["id"])]
    assert types.count("approval_requested") == 1
    assert d["status"] == "FAILED" and d["error"]["kind"] == "loop_detected"
    with app.state.session_factory() as db:
        assert len(db.scalars(select(Invoice)).all()) == 1


def test_runner_keeps_no_per_task_state(make_client):
    client, app = make_client()
    run_task(client, INVOICE_TASK)
    assert not hasattr(app.state.runner, "db") and not hasattr(app.state.runner, "task")


def test_interrupted_tasks_are_failed_on_startup(make_settings):
    settings = make_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    with make_session_factory(engine)() as db:
        db.add(Task(id="stuck", text="x", status="RUNNING"))
        db.commit()
    app = create_app(settings)
    with app.state.session_factory() as db:
        assert db.get(Task, "stuck").status == "FAILED"


def test_second_cancel_force_cancels_a_stuck_task(make_client):
    client, app = make_client()
    tid = client.post("/api/v1/tasks", json={"text": INVOICE_TASK}).json()["id"]
    with app.state.session_factory() as db:  # simulate a RUNNING task whose runner is gone
        db.get(Task, tid).status = "RUNNING"
        db.commit()
    assert client.post(f"/api/v1/tasks/{tid}/cancel").json()["status"] == "RUNNING"  # flag only
    assert client.post(f"/api/v1/tasks/{tid}/cancel").json()["status"] == "CANCELLED"  # forced


def test_prompt_only_lists_workflow_tools(make_client):
    client, app = make_client()
    from app.agent.runner import _TaskRun
    from app.schemas.agent import Plan
    run = _TaskRun(app.state.session_factory, app.state.settings, app.state.llm, app.state.registry)
    run.task = type("T", (), {"text": "x", "step_count": 0})()
    ctx = run._context(Plan(task_type="file_search", goal="g", params={"keyword": "contract"}), {"facts": {}, "history": []})
    assert {t["name"] for t in ctx.tools} == {"search_file_contents", "read_text_file"}


class AskForPathLLM(DemoClient):
    """Weak model that asks the user for a path it already has (the real-world report)."""

    def __init__(self):
        self.asked = 0

    def decide(self, ctx):
        d = super().decide(ctx)
        if ctx.facts.get("latest_invoice") and not self.asked and d.action == "tool":
            self.asked = 1
            return Decision(thought="need path", action="ask_user", message="Please provide the path to the latest invoice")
        return d


def test_agent_does_not_ask_user_for_data_it_already_has(make_client):
    client, app = make_client(AskForPathLLM())
    d = run_task(client, INVOICE_TASK)
    types = [e["type"] for e in events(client, d["id"])]
    assert "needs_input" not in types and d["status"] == "COMPLETED"
    assert any(e["tool"] == "ask_user" for e in events(client, d["id"]))
