import threading
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import TaskEvent

_seq_lock = threading.Lock()


def add_event(
    db: Session,
    task_id: str,
    type_: str,
    title: str,
    payload: Optional[dict[str, Any]] = None,
    step: Optional[int] = None,
    tool: Optional[str] = None,
) -> TaskEvent:
    with _seq_lock:  # API threads and the agent thread both write events; keep seq unique per task
        seq = (db.scalar(select(func.max(TaskEvent.seq)).where(TaskEvent.task_id == task_id)) or 0) + 1
        ev = TaskEvent(task_id=task_id, seq=seq, type=type_, title=title[:300], step=step, tool=tool, payload=payload or {})
        db.add(ev)
        db.commit()
    return ev
