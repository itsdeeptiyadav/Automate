import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import init_db, make_engine, make_session_factory
from app.main import create_app
from app.models.entities import Task
from app.tools.base import ToolContext
from app.tools.registry import build_registry

REPO = Path(__file__).resolve().parents[2]
INVOICE_TASK = (
    "Find the latest invoice from Acme Corporation, extract its invoice number, amount, and due date, "
    "save it in the internal invoice system, verify that it was saved correctly, and tell me the result."
)
FILE_TASK = "Find the text files in the sample workspace that contain the word 'contract' and return their filenames with evidence."


@pytest.fixture
def workspace(tmp_path) -> Path:
    dest = tmp_path / "sample_data"
    shutil.copytree(REPO / "sample_data", dest)
    return dest


@pytest.fixture
def make_settings(tmp_path, workspace):
    def _make(**overrides):
        base = dict(
            llm_provider="demo",
            database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
            workspace_root=workspace,
            documents_dir=tmp_path / "documents",
            retry_backoff_seconds=0,
            _env_file=None,
        )
        base.update(overrides)
        return Settings(**base)

    return _make


@pytest.fixture
def make_client(make_settings):
    def _make(llm=None, **overrides):
        settings = make_settings(**overrides)
        app = create_app(settings, llm)
        return TestClient(app), app

    return _make


@pytest.fixture
def client(make_client):
    c, _ = make_client()
    return c


@pytest.fixture
def db_session(make_settings):
    settings = make_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    sf = make_session_factory(engine)
    with sf() as db:
        db.add(Task(id="t-1", text="x", status="RUNNING"))
        db.commit()
        yield db, settings


@pytest.fixture
def tool_ctx(db_session):
    db, settings = db_session
    return ToolContext(settings, db, "t-1")


@pytest.fixture
def registry():
    return build_registry()


def run_task(client, text, approve=True, expect_pause=True):
    """Create + start a task, optionally approving the write. Returns final detail."""
    t = client.post("/api/v1/tasks", json={"text": text})
    assert t.status_code == 201
    tid = t.json()["id"]
    assert client.post(f"/api/v1/tasks/{tid}/start").status_code == 202
    d = client.get(f"/api/v1/tasks/{tid}").json()
    if d["status"] == "AWAITING_APPROVAL" and approve:
        assert client.post(f"/api/v1/tasks/{tid}/approve", json={"approved": True}).status_code == 202
        d = client.get(f"/api/v1/tasks/{tid}").json()
    return d


def events(client, tid):
    return client.get(f"/api/v1/tasks/{tid}/events").json()
