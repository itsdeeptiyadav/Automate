from datetime import datetime
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

UtcDateTime = Annotated[datetime, PlainSerializer(lambda d: d.isoformat() + "Z", return_type=str)]


class TaskCreate(BaseModel):
    text: str = Field(min_length=3, max_length=2000)


class InputIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class ApproveIn(BaseModel):
    approved: bool = True


class TaskSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    text: str
    status: str
    task_type: Optional[str]
    llm_provider: str
    llm_model: str
    step_count: int
    created_at: UtcDateTime
    updated_at: UtcDateTime


class VerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    invoice_id: Optional[int]
    kind: str
    passed: bool
    checks: list[dict[str, Any]]
    created_at: UtcDateTime


class TaskDetail(TaskSummary):
    plan: Optional[dict[str, Any]]
    result: Optional[dict[str, Any]]
    error: Optional[dict[str, Any]]
    pending_action: Optional[dict[str, Any]]
    max_steps: int
    verifications: list[VerificationOut] = []


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    seq: int
    ts: UtcDateTime
    type: str
    title: str
    step: Optional[int]
    tool: Optional[str]
    payload: dict[str, Any]


class InvoiceOut(BaseModel):
    id: int
    company: str
    invoice_number: str
    invoice_date: str
    due_date: str
    amount: str
    currency: str
    source_file: str
    created_by_task_id: Optional[str]
    created_at: UtcDateTime


class UploadOut(BaseModel):
    file: str
    company: str
    invoice_number: str
    invoice_date: str
    due_date: str
    amount: str
    currency: str
    warnings: list[str] = []
    suggested_task: str


class HealthOut(BaseModel):
    status: str
    llm_provider: str
    llm_model: str
    llm_is_demo: bool
    llm_ok: bool
    llm_detail: str
    engine: str = "langgraph"
    chat_available: bool = True
    tool_calling: str = "native"


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filename: str
    kind: str
    size_bytes: int
    page_count: Optional[int]
    char_count: int
    chunk_count: int
    created_at: UtcDateTime


class DocumentDetail(DocumentOut):
    preview: str


class SearchHitOut(BaseModel):
    document_id: int
    filename: str
    chunk: int
    start_char: int
    score: float
    snippet: str


class ConversationCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    created_at: UtcDateTime
    updated_at: UtcDateTime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    seq: int
    role: str
    content: str
    trace: list[dict[str, Any]] = []
    created_at: UtcDateTime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ChatOut(BaseModel):
    user: MessageOut
    assistant: MessageOut
    started_task_ids: list[str] = []


class GraphOut(BaseModel):
    nodes: list[str]
    mermaid: str
    waiting_at: list[str] = []
