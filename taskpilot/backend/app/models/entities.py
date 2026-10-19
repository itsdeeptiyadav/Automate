from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no tz support)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    task_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    plan: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    memory: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    pending_action: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    approved_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_provider: Mapped[str] = mapped_column(String(32), default="")
    llm_model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_task_seq", "task_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tool: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Invoice(Base):
    """The simulated company invoice system's table."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("company", "invoice_number", name="uq_invoice_company_number"),
        CheckConstraint("amount_cents > 0", name="ck_invoice_amount_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(200))
    invoice_number: Mapped[str] = mapped_column(String(64))
    invoice_date: Mapped[str] = mapped_column(String(10))  # ISO yyyy-mm-dd
    due_date: Mapped[str] = mapped_column(String(10))
    amount_cents: Mapped[int] = mapped_column(Integer)  # exact money, no floats
    currency: Mapped[str] = mapped_column(String(3))
    source_file: Mapped[str] = mapped_column(String(300))
    source_sha256: Mapped[str] = mapped_column(String(64))
    created_by_task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def amount(self) -> Decimal:
        return (Decimal(self.amount_cents) / 100).quantize(Decimal("0.01"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "company": self.company,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "amount": f"{self.amount:.2f}",
            "currency": self.currency,
            "source_file": self.source_file,
            "created_by_task_id": self.created_by_task_id,
        }


class Verification(Base):
    __tablename__ = "verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # "tool" | "final"
    passed: Mapped[bool] = mapped_column(Boolean)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Document(Base):
    """A user-uploaded document in the general library (not an invoice)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(8))  # pdf | txt | md | csv | docx
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_chunks_doc_idx", "document_id", "idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    start_char: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(120), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conv_seq", "conversation_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    trace: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # tool calls the assistant made
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
