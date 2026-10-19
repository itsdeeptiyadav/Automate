from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.events import add_event
from app.agent.llm import LLMError
from app.documents.store import DocumentError, delete_document, save_document, search_chunks
from app.documents.search import search_terms, snippet
from app.enums import TERMINAL_STATUSES, EventType, TaskStatus
from app.models.entities import Conversation, Document, DocumentChunk, Invoice, Message, Task, TaskEvent, Verification
from app.schemas.api import (
    ApproveIn,
    ChatIn,
    ChatOut,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    DocumentDetail,
    DocumentOut,
    EventOut,
    GraphOut,
    HealthOut,
    InputIn,
    InvoiceOut,
    MessageOut,
    SearchHitOut,
    TaskCreate,
    TaskDetail,
    TaskSummary,
    UploadOut,
    VerificationOut,
)
from app.task_service import claim_for_start, create_task as new_task
from app.uploads import UploadError, save_uploaded_invoice

router = APIRouter()


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


def _get_task(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


def _detail(request: Request, db: Session, task: Task) -> TaskDetail:
    db.refresh(task)
    vers = db.scalars(select(Verification).where(Verification.task_id == task.id).order_by(Verification.id)).all()
    data = TaskSummary.model_validate(task).model_dump()
    return TaskDetail(
        **data,
        plan=task.plan,
        result=task.result,
        error=task.error,
        pending_action=task.pending_action,
        max_steps=request.app.state.settings.max_steps,
        verifications=[VerificationOut.model_validate(v) for v in vers],
    )


def _invoice_out(i: Invoice) -> InvoiceOut:
    return InvoiceOut(
        id=i.id, company=i.company, invoice_number=i.invoice_number, invoice_date=i.invoice_date,
        due_date=i.due_date, amount=f"{i.amount:.2f}", currency=i.currency, source_file=i.source_file,
        created_by_task_id=i.created_by_task_id, created_at=i.created_at,
    )


# ====================================================================== health / graph
@router.get("/health", response_model=HealthOut)
def health(request: Request):
    llm = request.app.state.llm
    h = llm.health()
    return HealthOut(status="ok", llm_provider=llm.provider, llm_model=llm.model, llm_is_demo=llm.is_demo,
                     llm_ok=bool(h["ok"]), llm_detail=h["detail"], chat_available=llm.chat_model is not None,
                     tool_calling=request.app.state.settings.tool_calling)


@router.get("/agent/graph", response_model=GraphOut)
def agent_graph(request: Request):
    """The task agent's LangGraph (as Mermaid text)."""
    return GraphOut(**request.app.state.runner.graph_info())


@router.get("/tasks/{task_id}/graph", response_model=GraphOut)
def task_graph(task_id: str, request: Request, db: Session = Depends(get_db)):
    """Same graph, plus the node(s) this task's saved run is currently waiting at."""
    _get_task(db, task_id)
    return GraphOut(**request.app.state.runner.graph_info(task_id))


# ====================================================================== tasks
@router.post("/tasks", response_model=TaskDetail, status_code=201)
def create_task(body: TaskCreate, request: Request, db: Session = Depends(get_db)):
    task = new_task(db, request.app.state.llm, body.text)
    return _detail(request, db, task)


@router.get("/tasks", response_model=list[TaskSummary])
def list_tasks(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    return db.scalars(select(Task).order_by(Task.created_at.desc()).limit(limit)).all()


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    return _detail(request, db, _get_task(db, task_id))


@router.post("/tasks/{task_id}/start", response_model=TaskDetail, status_code=202)
def start_task(task_id: str, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if task.status != TaskStatus.PENDING.value:
        raise HTTPException(409, f"Task is {task.status}; only PENDING tasks can be started")
    claim_for_start(db, task)  # claim it so a double-click cannot start two runs
    background.add_task(request.app.state.runner.run, task.id)
    return _detail(request, db, task)


@router.post("/tasks/{task_id}/approve", response_model=TaskDetail, status_code=202)
def approve_task(task_id: str, body: ApproveIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if task.status != TaskStatus.AWAITING_APPROVAL.value or not task.pending_action:
        raise HTTPException(409, f"Task is {task.status}; nothing is waiting for approval")
    pending = task.pending_action
    if not body.approved:
        task.status = TaskStatus.CANCELLED.value
        task.pending_action = None
        db.commit()
        add_event(db, task.id, EventType.APPROVAL_DENIED.value, f"Write to {pending['tool']} denied. Nothing was written.", {"tool": pending["tool"]})
        add_event(db, task.id, EventType.STATE.value, "Status: CANCELLED", {"status": "CANCELLED"})
        return _detail(request, db, task)
    task.approved_key = pending["key"]
    task.status = TaskStatus.RUNNING.value
    db.commit()
    add_event(db, task.id, EventType.APPROVAL_GRANTED.value, f"Human approved {pending['tool']}", {"tool": pending["tool"], "args": pending["args"]}, pending.get("step"), pending["tool"])
    add_event(db, task.id, EventType.STATE.value, "Status: RUNNING", {"status": "RUNNING"})
    # Resumes the paused LangGraph run; the key binds the approval to this exact call.
    background.add_task(request.app.state.runner.resume, task.id, {"key": pending["key"]})
    return _detail(request, db, task)


@router.post("/tasks/{task_id}/input", response_model=TaskDetail, status_code=202)
def provide_input(task_id: str, body: InputIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if task.status != TaskStatus.NEEDS_INPUT.value:
        raise HTTPException(409, f"Task is {task.status}; it is not waiting for input")
    # No plan yet -> the task itself was ambiguous and will be re-planned; otherwise the agent asked mid-run.
    replanning = task.plan is None
    task.status = TaskStatus.PLANNING.value if replanning else TaskStatus.RUNNING.value
    db.commit()
    add_event(db, task.id, EventType.STATE.value, "Clarification received. " + ("Re-planning." if replanning else "Continuing."), {"message": body.message})
    background.add_task(request.app.state.runner.resume, task.id, {"message": body.message.strip()})
    return _detail(request, db, task)


@router.post("/tasks/{task_id}/cancel", response_model=TaskDetail)
def cancel_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    task = _get_task(db, task_id)
    if TaskStatus(task.status) in TERMINAL_STATUSES:
        raise HTTPException(409, f"Task already {task.status}")
    if task.status in {TaskStatus.PENDING.value, TaskStatus.AWAITING_APPROVAL.value, TaskStatus.NEEDS_INPUT.value}:
        task.status, task.pending_action = TaskStatus.CANCELLED.value, None
        db.commit()
        add_event(db, task.id, EventType.CANCEL.value, "Task cancelled by user before any further action.")
        add_event(db, task.id, EventType.STATE.value, "Status: CANCELLED", {"status": "CANCELLED"})
    elif task.cancel_requested:  # second click: the runner is stuck in a slow LLM call or is gone -> force it
        task.status, task.pending_action = TaskStatus.CANCELLED.value, None
        db.commit()
        add_event(db, task.id, EventType.CANCEL.value, "Task force-cancelled by user.")
        add_event(db, task.id, EventType.STATE.value, "Status: CANCELLED", {"status": "CANCELLED"})
    else:  # running: the loop checks this flag after every LLM call and before every step
        task.cancel_requested = True
        db.commit()
        add_event(db, task.id, EventType.CANCEL.value, "Cancel requested. The agent will stop at the next safe point. Click Cancel again to force it.")
    return _detail(request, db, task)


@router.get("/tasks/{task_id}/events", response_model=list[EventOut])
def task_events(task_id: str, after: int = Query(0, ge=0), db: Session = Depends(get_db)):
    _get_task(db, task_id)
    return db.scalars(
        select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.seq > after).order_by(TaskEvent.seq)
    ).all()


# ====================================================================== invoices
@router.get("/invoices", response_model=list[InvoiceOut])
def list_invoices(db: Session = Depends(get_db)):
    return [_invoice_out(i) for i in db.scalars(select(Invoice).order_by(Invoice.id.desc())).all()]


@router.post("/invoices/upload", response_model=UploadOut, status_code=201)
def upload_invoice(request: Request, file: UploadFile = File(...)):
    """Add an invoice PDF to the workspace so the worker can find it."""
    try:
        info = save_uploaded_invoice(request.app.state.settings, file.filename or "", file.file)
    except UploadError as exc:
        raise HTTPException(exc.status, exc.message)
    return UploadOut(**info)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(404, "Invoice not found")
    return _invoice_out(inv)


# ====================================================================== documents (general library)
@router.post("/documents", response_model=DocumentOut, status_code=201)
def upload_document(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Add any supported document (.pdf .docx .txt .md .csv) to the library the chat and Q&A tasks read from."""
    try:
        doc = save_document(db, request.app.state.settings, file.filename or "", file.file)
    except DocumentError as exc:
        raise HTTPException(exc.status, exc.message)
    return doc


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    return db.scalars(select(Document).order_by(Document.id.desc())).all()


@router.get("/documents/search", response_model=list[SearchHitOut])
def search_documents(q: str = Query(min_length=2, max_length=200), document_id: Optional[int] = None,
                     limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db)):
    hits = search_chunks(db, q, document_id, limit)
    names = {d.id: d.filename for d in db.scalars(select(Document)).all()}
    starts = {cid: st for cid, st in db.execute(
        select(DocumentChunk.id, DocumentChunk.start_char).where(DocumentChunk.id.in_([h.chunk_id for h in hits]))).all()}
    terms = search_terms(q)
    return [SearchHitOut(document_id=h.document_id, filename=names.get(h.document_id, "?"), chunk=h.idx,
                         start_char=starts.get(h.chunk_id, 0), score=round(h.score, 2), snippet=snippet(h.text, terms)) for h in hits]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return DocumentDetail(**DocumentOut.model_validate(doc).model_dump(), preview=doc.text[:3000])


@router.delete("/documents/{document_id}", status_code=204)
def remove_document(document_id: int, request: Request, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    delete_document(db, request.app.state.settings, doc)


# ====================================================================== chat
def _get_conversation(db: Session, conversation_id: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationCreate, db: Session = Depends(get_db)):
    conv = Conversation(title=(body.title or "New chat").strip()[:120] or "New chat")
    db.add(conv)
    db.commit()
    return conv


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db), limit: int = Query(50, ge=1, le=200)):
    return db.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(limit)).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    conv = _get_conversation(db, conversation_id)
    msgs = db.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.seq)).all()
    return ConversationDetail(**ConversationOut.model_validate(conv).model_dump(), messages=[MessageOut.model_validate(m) for m in msgs])


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)):
    conv = _get_conversation(db, conversation_id)
    for m in db.scalars(select(Message).where(Message.conversation_id == conv.id)).all():
        db.delete(m)
    db.delete(conv)
    db.commit()


@router.post("/conversations/{conversation_id}/messages", response_model=ChatOut)
def send_message(conversation_id: str, body: ChatIn, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    _get_conversation(db, conversation_id)
    try:
        out = request.app.state.chat.reply(conversation_id, body.text.strip())
    except LLMError as exc:
        raise HTTPException(503, str(exc))
    except LookupError:
        raise HTTPException(404, "Conversation not found")
    for tid in out["started_task_ids"]:  # tasks the assistant started run after the reply is sent
        background.add_task(request.app.state.runner.run, tid)
    return ChatOut(user=MessageOut.model_validate(out["user"]), assistant=MessageOut.model_validate(out["assistant"]),
                   started_task_ids=out["started_task_ids"])
