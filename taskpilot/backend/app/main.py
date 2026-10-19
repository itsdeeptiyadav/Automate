import logging
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.llm import LLMClient, build_llm
from app.agent.chat import ChatService
from app.agent.runner import AgentRunner, build_checkpointer
from app.api.routes import router
from app.config import Settings, get_settings
from app.enums import EventType
from app.database import init_db, make_engine, make_session_factory
from app.tools.registry import build_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def fail_interrupted_tasks(session_factory) -> int:
    """A restart (or uvicorn --reload) kills background runs, leaving tasks stuck in PLANNING/RUNNING forever."""
    from sqlalchemy import select

    from app.agent.events import add_event
    from app.models.entities import Task

    n = 0
    with session_factory() as db:
        for t in db.scalars(select(Task).where(Task.status.in_(["PLANNING", "RUNNING", "VERIFYING"]))).all():
            t.status, t.pending_action = "FAILED", None
            t.error = {"kind": "interrupted", "error_type": "INTERNAL", "message": "The server restarted while this task was running."}
            db.commit()
            add_event(db, t.id, EventType.ERROR.value, "Task interrupted by a server restart", t.error)
            add_event(db, t.id, EventType.STATE.value, "Status: FAILED", {"status": "FAILED"})
            n += 1
    return n


def create_app(settings: Optional[Settings] = None, llm: Optional[LLMClient] = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    fail_interrupted_tasks(session_factory)
    llm = llm or build_llm(settings)
    registry = build_registry()

    app = FastAPI(title="TaskPilot - Autonomous AI Task Worker", version="2.0.0")
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.llm = llm
    app.state.registry = registry
    app.state.checkpointer = build_checkpointer(settings)
    app.state.runner = AgentRunner(session_factory, settings, llm, registry, app.state.checkpointer)
    app.state.chat = ChatService(session_factory, settings, llm, registry)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
