"""Creating and claiming tasks. Shared by the REST API and the chat agent's `start_task` tool."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.events import add_event
from app.agent.llm import LLMClient
from app.enums import EventType, TaskStatus
from app.models.entities import Task


def create_task(db: Session, llm: LLMClient, text: str, origin: str = "") -> Task:
    task = Task(text=text.strip(), status=TaskStatus.PENDING.value, llm_provider=llm.provider, llm_model=llm.model,
                memory={"facts": {}, "history": []})
    db.add(task)
    db.commit()
    add_event(db, task.id, EventType.STATE.value, "Task created" + (f" ({origin})" if origin else ""), {"status": "PENDING"})
    return task


def claim_for_start(db: Session, task: Task) -> None:
    """PENDING -> PLANNING so a double start cannot launch two runs. The caller schedules runner.run."""
    task.status = TaskStatus.PLANNING.value
    db.commit()
    add_event(db, task.id, EventType.STATE.value, "Status: PLANNING", {"status": "PLANNING"})
