"""The chat agent: a conversation where the model can use tools.

A small LangGraph graph:

    START -> agent --(tool calls)--> tools --> agent ... --> END

* `agent` calls the chat model with the conversation. With TOOL_CALLING=native the model gets the tools through
  LangChain's `bind_tools`; with TOOL_CALLING=json it is asked for one JSON action per turn and the result is
  converted into the same kind of AIMessage, so the rest of the graph is identical.
* `tools` runs the requested tools. Chat tools are READ-ONLY views over documents, invoices, workspace files and
  tasks, plus `start_task`, which hands work to the task worker. The worker still needs a human to approve any
  write, so the chat model can never save or delete anything by itself.

Conversation history lives in SQLite (messages table); the graph itself is stateless per request.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Annotated, Any, Literal, Optional, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage, trim_messages
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent import prompts
from app.agent.llm import LLMClient, LLMError, LLMUnavailable, message_text
from app.config import Settings
from app.models.entities import Conversation, Message, Task, utcnow
from app.task_service import claim_for_start, create_task
from app.tools.base import ToolContext, ToolFailure, ToolRegistry

log = logging.getLogger("taskpilot.chat")

# Read-only registry tools the chat agent may use. Anything with permission "write" is refused regardless.
CHAT_REGISTRY_TOOLS = [
    "list_documents", "search_documents", "read_document",
    "list_invoice_records", "search_internal_invoices",
    "search_file_contents", "read_text_file", "list_workspace_files",
]
MAX_TOOL_CHARS = 6000
MAX_TASKS_PER_TURN = 2
_persist_lock = threading.Lock()


class ChatAction(BaseModel):
    """One JSON turn in TOOL_CALLING=json mode."""

    action: Literal["tool", "reply"]
    tool: Optional[str] = None
    args: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None


class ChatState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    rounds: int


class StartTaskArgs(BaseModel):
    text: str = Field(min_length=3, max_length=2000, description="The task in plain English, e.g. 'Find the latest invoice from Acme Corporation, extract ..., save it ..., verify it ...'")


class TaskIdArgs(BaseModel):
    task_id: str = Field(min_length=8, max_length=36, description="Task id returned by start_task or list_tasks")


class ListTasksArgs(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)


def _pack(ok: bool, summary: str, data: Optional[dict] = None, warnings: Optional[list] = None, error: Optional[str] = None) -> str:
    """Tool output as JSON text for the model (bounded size)."""
    body: dict[str, Any] = {"ok": ok, "summary": summary, "data": data or {}}
    if warnings:
        body["warnings"] = warnings
    if error:
        body["error"] = error
    text = json.dumps(body, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_CHARS:
        body["data"] = {"truncated": True, "preview": json.dumps(data or {}, ensure_ascii=False, default=str)[: MAX_TOOL_CHARS - 400]}
        text = json.dumps(body, ensure_ascii=False, default=str)
    return text


class ChatRun:
    """Everything one chat turn needs. Created per request, so nothing is shared between conversations."""

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings, llm: LLMClient, registry: ToolRegistry):
        self.sf, self.settings, self.llm, self.registry = session_factory, settings, llm, registry
        self.trace: list[dict[str, Any]] = []
        self.started_task_ids: list[str] = []

    # ------------------------------------------------------------------ tools
    def _registry_tool(self, name: str) -> StructuredTool:
        tool = self.registry.get(name)
        assert tool is not None and tool.permission == "read", f"{name} must be a registered read-only tool"

        def run(**kwargs: Any) -> str:
            with self.sf() as db:
                ctx = ToolContext(self.settings, db, task_id="chat")
                try:
                    _, args = self.registry.prepare(name, kwargs)
                except ToolFailure as exc:
                    return _pack(False, exc.message, error=exc.message)
                res = self.registry.invoke(tool, args, ctx)
                return _pack(res.ok, res.summary, res.data, res.warnings, res.error)

        return StructuredTool.from_function(func=run, name=name, description=tool.description, args_schema=tool.args_model)

    def _start_task(self) -> StructuredTool:
        def run(text: str) -> str:
            if len(self.started_task_ids) >= MAX_TASKS_PER_TURN:
                return _pack(False, f"At most {MAX_TASKS_PER_TURN} tasks can be started per message.")
            with self.sf() as db:
                task = create_task(db, self.llm, text, origin="from chat")
                claim_for_start(db, task)
                self.started_task_ids.append(task.id)
                return _pack(True, f"Started task {task.id}. It runs in the background; any write will wait for the user's approval in the Tasks tab.",
                             {"task_id": task.id, "status": task.status})

        return StructuredTool.from_function(
            func=run, name="start_task", args_schema=StartTaskArgs,
            description=("Start a TaskPilot task. Supported: (1) save the latest invoice from a company into the internal invoice system "
                         "(needs the user's approval before writing), (2) find workspace text files containing a word, "
                         "(3) answer a question from the uploaded documents with verified citations. Returns a task id."),
        )

    def _task_status(self) -> StructuredTool:
        def run(task_id: str) -> str:
            with self.sf() as db:
                t = db.get(Task, task_id.strip())
                if t is None:
                    return _pack(False, f"No task with id {task_id}.")
                data: dict[str, Any] = {"task_id": t.id, "status": t.status, "text": t.text[:200]}
                if t.plan:
                    data["goal"] = t.plan.get("goal")
                if t.result and t.result.get("summary"):
                    data["summary"] = t.result["summary"]
                if t.result and t.result.get("question"):
                    data["question_for_user"] = t.result["question"]
                if t.error:
                    data["error"] = t.error.get("message")
                if t.pending_action:
                    data["waiting_for_approval"] = t.pending_action.get("tool")
                return _pack(True, f"Task {t.id} is {t.status}.", data)

        return StructuredTool.from_function(func=run, name="get_task_status", args_schema=TaskIdArgs,
                                            description="Get the status, result or error of a task by id.")

    def _list_tasks(self) -> StructuredTool:
        def run(limit: int = 5) -> str:
            with self.sf() as db:
                rows = db.scalars(select(Task).order_by(Task.created_at.desc()).limit(limit)).all()
                items = [{"task_id": t.id, "status": t.status, "text": t.text[:120]} for t in rows]
                return _pack(True, f"{len(items)} recent task(s).", {"tasks": items})

        return StructuredTool.from_function(func=run, name="list_tasks", args_schema=ListTasksArgs,
                                            description="List the most recent tasks with their status.")

    def tools(self) -> list[StructuredTool]:
        return [self._registry_tool(n) for n in CHAT_REGISTRY_TOOLS if self.registry.get(n)] + [
            self._start_task(), self._task_status(), self._list_tasks(),
        ]

    # ------------------------------------------------------------------ model access
    def _guard(self, fn):
        """Run a model call; turn provider errors into LLMError with a helpful message."""
        require = getattr(self.llm, "_require_key", None)
        if require:
            require()
        try:
            return fn()
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            mapped = self.llm.map_error(exc)
            if mapped is not None:
                raise mapped from exc
            raise

    def _json_step(self, messages: list[BaseMessage], tools: list[StructuredTool]) -> AIMessage:
        """TOOL_CALLING=json: ask for one JSON action and convert it to an AIMessage."""
        structured = getattr(self.llm, "structured", None)
        if structured is None:
            raise LLMUnavailable("TOOL_CALLING=json needs a real LLM provider (not demo mode).")
        catalogue = [{"name": t.name, "description": t.description, "arguments": list(t.args.keys())} for t in tools]
        transcript: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, HumanMessage):
                transcript.append({"role": "user", "content": message_text(m.content)})
            elif isinstance(m, AIMessage):
                if m.tool_calls:
                    transcript.append({"role": "assistant", "tool_call": {"tool": m.tool_calls[0]["name"], "args": m.tool_calls[0]["args"]}})
                else:
                    transcript.append({"role": "assistant", "content": message_text(m.content)})
            elif isinstance(m, ToolMessage):
                transcript.append({"role": "tool", "tool": m.name, "result": message_text(m.content)})
        action = self.llm.structured(prompts.CHAT_JSON_SYSTEM, prompts.chat_json_user_prompt(catalogue, transcript), ChatAction)
        if action.action == "tool" and action.tool:
            return AIMessage(content="", tool_calls=[{"name": action.tool, "args": action.args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}])
        return AIMessage(content=action.message or "")

    # ------------------------------------------------------------------ graph
    def build_graph(self, chat_model: BaseChatModel):
        tools = self.tools()
        by_name = {t.name: t for t in tools}
        native = self.settings.tool_calling == "native"
        with_tools = chat_model.bind_tools(tools) if native else None
        max_rounds = self.settings.chat_max_tool_rounds

        def agent(state: ChatState) -> dict:
            exhausted = state["rounds"] >= max_rounds
            messages: list[BaseMessage] = [SystemMessage(content=prompts.CHAT_SYSTEM)] + list(state["messages"])
            if exhausted:
                messages.append(SystemMessage(content="You have used all available tool calls. Answer now using only what the tool results above contain."))
                reply = self._guard(lambda: chat_model.invoke(messages))  # no tools bound: it must answer
            elif native:
                reply = self._guard(lambda: with_tools.invoke(messages))
            else:
                reply = self._guard(lambda: self._json_step(list(state["messages"]), tools))
            return {"messages": [reply]}

        def run_tools(state: ChatState) -> dict:
            last = state["messages"][-1]
            out: list[ToolMessage] = []
            for call in getattr(last, "tool_calls", []) or []:
                name, args = call["name"], call.get("args") or {}
                tool = by_name.get(name)
                if tool is None:
                    content = _pack(False, f"Unknown tool '{name}'. Available: {', '.join(by_name)}.")
                else:
                    try:
                        content = tool.invoke(args)
                    except Exception as exc:  # noqa: BLE001 - bad arguments etc. go back to the model, not to the user
                        content = _pack(False, f"Invalid call to {name}: {str(exc)[:300]}")
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    parsed = {"ok": False, "summary": content[:200]}
                entry = {"tool": name, "args": args, "ok": bool(parsed.get("ok")), "summary": str(parsed.get("summary", ""))[:300]}
                if name == "start_task" and parsed.get("ok"):
                    entry["task_id"] = parsed.get("data", {}).get("task_id")
                self.trace.append(entry)
                out.append(ToolMessage(content=content, tool_call_id=call["id"], name=name))
            return {"messages": out, "rounds": state["rounds"] + 1}

        def after_agent(state: ChatState) -> str:
            last = state["messages"][-1]
            return "tools" if getattr(last, "tool_calls", None) and state["rounds"] < max_rounds else END

        g = StateGraph(ChatState)
        g.add_node("agent", agent)
        g.add_node("tools", run_tools)
        g.add_edge(START, "agent")
        g.add_conditional_edges("agent", after_agent, {"tools": "tools", END: END})
        g.add_edge("tools", "agent")
        return g.compile()


class ChatService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings, llm: LLMClient, registry: ToolRegistry):
        self.sf, self.settings, self.llm, self.registry = session_factory, settings, llm, registry

    def _history(self, db: Session, conversation_id: str) -> list[BaseMessage]:
        rows = db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.seq)).all()
        msgs: list[BaseMessage] = [HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content) for m in rows]
        return trim_messages(msgs, strategy="last", token_counter=len, max_tokens=self.settings.chat_history_messages,
                             start_on="human", include_system=False)

    def reply(self, conversation_id: str, text: str) -> dict[str, Any]:
        """Run one chat turn. Nothing is saved unless the model call succeeds, so a failed turn can be retried."""
        chat_model = self.llm.chat_model
        if chat_model is None:
            raise LLMUnavailable("This LLM provider does not offer a chat model.")
        with self.sf() as db:
            history = self._history(db, conversation_id)
        run = ChatRun(self.sf, self.settings, self.llm, self.registry)
        graph = run.build_graph(chat_model)
        final = graph.invoke({"messages": history + [HumanMessage(content=text)], "rounds": 0},
                             {"recursion_limit": 10 + 2 * self.settings.chat_max_tool_rounds})
        answer = ""
        for m in reversed(final["messages"]):
            if isinstance(m, AIMessage) and not m.tool_calls:
                answer = message_text(m.content).strip()
                break
        if not answer:
            answer = "I couldn't produce an answer. Please try rephrasing."

        with _persist_lock, self.sf() as db:
            conv = db.get(Conversation, conversation_id)
            if conv is None:  # deleted while the model was thinking
                raise LookupError("conversation deleted")
            seq = (db.scalar(select(func.max(Message.seq)).where(Message.conversation_id == conversation_id)) or 0) + 1
            user_msg = Message(conversation_id=conversation_id, seq=seq, role="user", content=text, trace=[])
            bot_msg = Message(conversation_id=conversation_id, seq=seq + 1, role="assistant", content=answer, trace=run.trace)
            db.add_all([user_msg, bot_msg])
            if seq == 1 and conv.title == "New chat":
                conv.title = (text.strip().splitlines()[0] if text.strip() else "New chat")[:60]
            conv.updated_at = utcnow()
            db.commit()
            return {"user": user_msg, "assistant": bot_msg, "started_task_ids": run.started_task_ids}
