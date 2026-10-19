"""The agent as a LangGraph state machine.

    START -> plan -> decide -> guard -> execute -> decide ...        (tool loop)
                       |         |-> await_approval (interrupt) -> execute
                       |-> verify -> END                              (LLM said "finish")
                       |-> ask -> await_input (interrupt) -> plan | decide
                       |-> END                                        (failed / cancelled)

The LLM decides *what* to do next (nodes `plan` and `decide`); deterministic code validates arguments,
enforces permissions and approvals, executes tools, retries transient errors and performs the final
verification (nodes `guard`, `execute`, `verify`).

Human-in-the-loop uses LangGraph's `interrupt()`. The graph pauses at `await_approval` / `await_input`,
its state is saved by a SQLite checkpointer (so a pause survives a server restart), and the API resumes it
with `Command(resume=...)`. The `tasks` / `task_events` tables remain the projection the UI reads.

Rule for interrupt nodes: everything before `interrupt()` re-runs on resume, so those nodes have no side
effects before it (the side effects live in the node that routes to them).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.orm import Session, sessionmaker

from app.agent.events import add_event
from app.agent.gates import run_gate
from app.agent.llm import LLMClient, LLMError
from app.config import Settings
from app.enums import TERMINAL_STATUSES, ErrorType, EventType, TaskStatus
from app.models.entities import Task
from app.schemas.agent import Decision, DecisionContext, Plan
from app.tools.base import ToolContext, ToolFailure, ToolRegistry, ToolResult, canonical_key

log = logging.getLogger("taskpilot.agent")

PARAM_FOR_TYPE = {"invoice_ingest": "company", "file_search": "keyword", "document_qa": "question"}
# These parameters are names/keywords copied from the user's text, so an invented value is rejected.
# A document question is free text a model may legitimately rephrase, so it is not checked this way.
MUST_APPEAR_IN_TEXT = {"invoice_ingest", "file_search"}
MAX_OBS_CHARS = 20000
# Only the tools the workflow needs go into the prompt (smaller prompt = faster and less truncation).
TOOLS_FOR_TYPE = {
    "invoice_ingest": {"get_latest_invoice", "search_internal_invoices", "create_invoice_record", "verify_invoice_record", "get_invoice_record", "read_invoice"},
    "file_search": {"search_file_contents", "read_text_file"},
    "document_qa": {"search_documents", "read_document", "list_documents"},
}
NODES = ["plan", "decide", "guard", "await_approval", "execute", "verify", "ask", "await_input"]


class TaskState(TypedDict, total=False):
    """Graph state. Plain JSON types only, so any checkpointer can store it."""

    plan: Optional[dict]        # Plan.model_dump()
    facts: dict                 # working memory merged in by deterministic code
    history: list               # observations (tool results) so far
    decision: dict              # latest Decision.model_dump()
    call: Optional[dict]        # the tool call being executed {tool, args, key, step, thought}
    pending: Optional[dict]     # write call waiting for human approval
    ask: dict                   # {"question": str, "stage": "plan" | "decide"}
    route: str                  # the next hop, chosen by the node that just ran


def build_checkpointer(settings: Settings):
    """SQLite-backed LangGraph checkpointer (durable pauses); in-memory when the DB itself is in memory."""
    target = settings.checkpoint_file
    if target == ":memory:":
        return InMemorySaver()
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


class _TaskRun:
    """State for ONE run (or resume) of ONE task. Never shared between threads."""

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings, llm: LLMClient,
                 registry: ToolRegistry, checkpointer: Any = None):
        self.sf = session_factory
        self.settings = settings
        self.llm = llm
        self.registry = registry
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        self.started = time.monotonic()  # time spent waiting for a human does not count against the limit

    # ------------------------------------------------------------------ entry points
    def run(self, task_id: str) -> None:
        self._execute_graph(task_id, resume=None)

    def resume(self, task_id: str, value: dict) -> None:
        self._execute_graph(task_id, resume=value)

    def _config(self, task_id: str) -> dict:
        return {"configurable": {"thread_id": task_id}, "recursion_limit": 20 + self.settings.max_steps * 8}

    def _execute_graph(self, task_id: str, resume: Optional[dict]) -> None:
        with self.sf() as db:
            task = db.get(Task, task_id)
            if task is None or TaskStatus(task.status) in TERMINAL_STATUSES:
                return
            self.db, self.task = db, task
            self.ctx = ToolContext(self.settings, db, task_id)
            graph = self.build_graph()
            cfg = self._config(task_id)
            try:
                if resume is None:
                    mem = task.memory or {}
                    graph.invoke({"plan": task.plan, "facts": mem.get("facts", {}), "history": mem.get("history", [])}, cfg)
                else:
                    if not graph.get_state(cfg).next:
                        return self._fail("checkpoint_missing", "The saved agent state for this task was not found, so it cannot be resumed.", ErrorType.INTERNAL)
                    graph.invoke(Command(resume=resume), cfg)
            except LLMError as exc:
                self._fail(exc.code, str(exc), ErrorType.LLM)
            except GraphRecursionError:
                self._fail("step_limit", "The agent graph hit its recursion limit.", ErrorType.INTERNAL)
            except Exception as exc:  # noqa: BLE001
                log.exception("agent crashed")
                self._fail("internal_error", f"{type(exc).__name__}: {exc}", ErrorType.INTERNAL)

    # ------------------------------------------------------------------ graph
    def build_graph(self):
        g = StateGraph(TaskState)
        g.add_node("plan", self.n_plan)
        g.add_node("decide", self.n_decide)
        g.add_node("guard", self.n_guard)
        g.add_node("await_approval", self.n_await_approval)
        g.add_node("execute", self.n_execute)
        g.add_node("verify", self.n_verify)
        g.add_node("ask", self.n_ask)
        g.add_node("await_input", self.n_await_input)

        route = lambda s: s["route"]  # noqa: E731
        g.add_edge(START, "plan")
        g.add_conditional_edges("plan", route, {"decide": "decide", "ask": "ask", "end": END})
        g.add_conditional_edges("decide", route, {"guard": "guard", "verify": "verify", "ask": "ask", "decide": "decide", "end": END})
        g.add_conditional_edges("guard", route, {"decide": "decide", "approve": "await_approval", "execute": "execute", "end": END})
        g.add_conditional_edges("await_approval", route, {"execute": "execute", "end": END})
        g.add_edge("execute", "decide")
        g.add_edge("ask", "await_input")
        g.add_conditional_edges("await_input", route, {"plan": "plan", "decide": "decide"})
        g.add_edge("verify", END)
        return g.compile(checkpointer=self.checkpointer)

    # ------------------------------------------------------------------ helpers
    def emit(self, type_: EventType, title: str, payload: Optional[dict] = None, step: Optional[int] = None, tool: Optional[str] = None):
        add_event(self.db, self.task.id, type_.value, title, payload, step, tool)

    def set_state(self, status: TaskStatus, note: str = "") -> None:
        if self.task.status == status.value:
            return
        self.task.status = status.value
        self.db.commit()
        self.emit(EventType.STATE, f"Status: {status.value}" + (f" ({note})" if note else ""), {"status": status.value})

    def _cancelled(self) -> bool:
        self.db.refresh(self.task)
        if self.task.status == TaskStatus.CANCELLED.value:
            return True
        if self.task.cancel_requested:
            self.task.status = TaskStatus.CANCELLED.value
            self.task.pending_action = None
            self.db.commit()
            self.emit(EventType.CANCEL, "Task cancelled by user. Stopped safely; no further tool calls.")
            return True
        return False

    def _timed_out(self) -> bool:
        return time.monotonic() - self.started > self.settings.task_timeout_seconds

    def _fail(self, kind: str, message: str, error_type: ErrorType, extra: Optional[dict] = None) -> None:
        self.db.rollback()
        self.db.refresh(self.task)
        if self.task.status in {s.value for s in TERMINAL_STATUSES}:
            return
        self.task.status = TaskStatus.FAILED.value
        self.task.pending_action = None
        self.task.error = {"kind": kind, "error_type": error_type.value, "message": message, **(extra or {})}
        self.db.commit()
        self.emit(EventType.ERROR, f"Task failed: {message}", self.task.error)
        self.emit(EventType.STATE, "Status: FAILED", {"status": "FAILED"})

    def _end(self, kind: str, message: str, error_type: ErrorType, extra: Optional[dict] = None) -> dict:
        self._fail(kind, message, error_type, extra)
        return {"route": "end"}

    def _needs_input(self, question: str) -> None:
        self.task.status = TaskStatus.NEEDS_INPUT.value
        self.task.result = {"question": question}
        self.db.commit()
        self.emit(EventType.NEEDS_INPUT, question, {"question": question})
        self.emit(EventType.STATE, "Status: NEEDS_INPUT", {"status": "NEEDS_INPUT"})

    @staticmethod
    def _mem(state: TaskState) -> dict[str, Any]:
        return {"facts": dict(state.get("facts") or {}), "history": list(state.get("history") or [])}

    @staticmethod
    def _mem_update(mem: dict) -> dict[str, Any]:
        return {"facts": mem["facts"], "history": mem["history"]}

    # ------------------------------------------------------------------ node: plan
    def n_plan(self, state: TaskState) -> dict:
        t = self.task
        if self._cancelled():
            return {"route": "end"}
        if state.get("plan"):
            return {"route": "decide"}
        self.set_state(TaskStatus.PLANNING)
        plan = self.llm.plan(t.text, self.registry.describe())
        if self._cancelled():
            return {"route": "end"}
        return self._accept_plan(plan)

    def _accept_plan(self, plan: Plan) -> dict:
        t = self.task
        t.plan = plan.model_dump()
        t.task_type = plan.task_type
        self.db.commit()
        self.emit(EventType.PLAN, plan.goal or "Plan created", plan.model_dump())

        def reject() -> None:
            t.plan = None
            self.db.commit()

        if plan.task_type == "unsupported":
            reject()
            return self._end("unsupported_task", plan.goal or "This task type is not supported.", ErrorType.VALIDATION)
        param = PARAM_FOR_TYPE[plan.task_type]
        value = (plan.params.get(param) or "").strip()
        if plan.needs_clarification or not value:
            reject()
            return {"route": "ask", "plan": None,
                    "ask": {"question": plan.needs_clarification or f"Please specify the {param} for this task.", "stage": "plan"}}
        if plan.task_type in MUST_APPEAR_IN_TEXT and value.casefold() not in t.text.casefold():
            reject()
            return self._end(
                "plan_invalid",
                f"The plan used {param} '{value}', which does not appear in your task. Refusing to proceed with an invented value.",
                ErrorType.VALIDATION,
            )
        return {"plan": plan.model_dump(), "route": "decide"}

    # ------------------------------------------------------------------ node: decide
    def _context(self, plan: Plan, mem: dict) -> DecisionContext:
        return DecisionContext(
            task_text=self.task.text,
            plan=plan,
            facts=mem["facts"],
            history=mem["history"],
            tools=[t for t in self.registry.describe() if t["name"] in TOOLS_FOR_TYPE.get(plan.task_type, {t["name"]})],
            steps_used=self.task.step_count,
            max_steps=self.settings.max_steps,
        )

    def _loop_detected(self, d: Decision, mem: dict) -> bool:
        args = d.args
        try:  # history stores validated/normalised args, so compare like with like
            _, model = self.registry.prepare(d.tool or "", d.args)
            args = model.model_dump(mode="json")
        except ToolFailure:
            pass
        same = [o for o in mem["history"] if o.get("tool") == d.tool and o.get("args") == args]
        return len(same) >= 2

    def n_decide(self, state: TaskState) -> dict:
        t = self.task
        if self._cancelled():
            return {"route": "end"}
        if self._timed_out():
            return self._end("timeout", f"Stopped after {self.settings.task_timeout_seconds}s.", ErrorType.INTERNAL)
        if t.step_count >= self.settings.max_steps:
            return self._end("step_limit", f"Stopped after the maximum of {self.settings.max_steps} steps.", ErrorType.INTERNAL)

        self.set_state(TaskStatus.RUNNING)
        plan = Plan.model_validate(state["plan"])
        mem = self._mem(state)
        decision = self.llm.decide(self._context(plan, mem))
        if self._cancelled():  # the LLM call can take minutes; honour a cancel that arrived meanwhile
            return {"route": "end"}
        if self._timed_out():
            return self._end("timeout", f"Stopped after {self.settings.task_timeout_seconds}s.", ErrorType.INTERNAL)
        self.emit(
            EventType.THOUGHT,
            decision.thought or f"Decision: {decision.action}",
            {"action": decision.action, "tool": decision.tool, "message": decision.message},
        )
        if decision.action == "finish":
            return {"decision": decision.model_dump(), "route": "verify"}
        if decision.action == "fail":
            return self._end("agent_gave_up", decision.message or "The agent could not complete the task.", ErrorType.NOT_FOUND)
        if decision.action == "ask_user":
            refused = sum(1 for o in mem["history"] if o.get("tool") == "ask_user")
            if mem["facts"] and refused < 2:  # it already holds the data; do not bounce the question to the user
                t.step_count += 1
                self.db.commit()
                msg = ("Do not ask the user: the information is already in facts/observations "
                       "(e.g. facts.latest_invoice.source_file). Continue with the next tool in the plan.")
                self.emit(EventType.TOOL_ERROR, "Refused a question to the user: " + msg,
                          {"error_type": "VALIDATION", "error": msg, "asked": decision.message}, t.step_count, "ask_user")
                self._observe(mem, t.step_count, "ask_user", {}, ToolResult.failure(ErrorType.VALIDATION, msg))
                return {**self._mem_update(mem), "route": "decide"}
            return {"route": "ask", "ask": {"question": decision.message or "More information is needed.", "stage": "decide"}}
        if self._loop_detected(decision, mem):
            return self._end("loop_detected", "The agent repeated the same action too many times.", ErrorType.INTERNAL)
        return {"decision": decision.model_dump(), "route": "guard"}

    # ------------------------------------------------------------------ node: guard (validate, permit, approve?)
    def n_guard(self, state: TaskState) -> dict:
        t = self.task
        d = Decision.model_validate(state["decision"])
        mem = self._mem(state)
        t.step_count += 1
        self.db.commit()
        step = t.step_count
        try:
            tool, args = self.registry.prepare(d.tool or "", d.args)
        except ToolFailure as exc:
            self.emit(EventType.TOOL_ERROR, f"Rejected tool call: {exc.message}",
                      {"error_type": exc.error_type.value, "error": exc.message, "requested_tool": d.tool, "args": d.args}, step, d.tool)
            self._observe(mem, step, d.tool or "?", d.args, ToolResult.failure(exc.error_type, exc.message))
            return {**self._mem_update(mem), "route": "decide"}

        key = canonical_key(tool.name, args)
        norm_args = args.model_dump(mode="json")
        if tool.permission == "write" and any(
            o.get("ok") and o.get("tool") == tool.name and o.get("args") == norm_args for o in mem["history"]
        ):
            msg = f"{tool.name} already succeeded with these exact arguments. Not repeating the write; verify the saved record instead."
            self.emit(EventType.TOOL_ERROR, msg, {"error_type": ErrorType.CONFLICT.value, "error": msg}, step, tool.name)
            self._observe(mem, step, tool.name, norm_args, ToolResult.failure(ErrorType.CONFLICT, msg))
            return {**self._mem_update(mem), "route": "decide"}
        if tool.preflight is not None:
            problem = tool.preflight(self.ctx, args)
            if problem:
                self.emit(EventType.TOOL_ERROR, f"Rejected before writing: {problem}",
                          {"error_type": "VALIDATION", "error": problem, "args": norm_args}, step, tool.name)
                self._observe(mem, step, tool.name, norm_args, ToolResult.failure(ErrorType.VALIDATION, problem))
                return {**self._mem_update(mem), "route": "decide"}
        self.emit(EventType.TOOL_CALL, f"Call {tool.name}", {"args": norm_args, "permission": tool.permission}, step, tool.name)

        call = {"tool": tool.name, "args": norm_args, "key": key, "step": step, "thought": d.thought}
        if tool.permission == "write" and self.settings.require_write_approval and t.approved_key != key:
            t.pending_action = dict(call)
            self.db.commit()
            self.set_state(TaskStatus.AWAITING_APPROVAL)
            self.emit(EventType.APPROVAL_REQUESTED, f"Approval needed before running {tool.name}",
                      {"tool": tool.name, "args": norm_args}, step, tool.name)
            return {"call": call, "pending": call, "route": "approve"}
        return {"call": call, "route": "execute"}

    # ------------------------------------------------------------------ node: await_approval (interrupt)
    def n_await_approval(self, state: TaskState) -> dict:
        pending = state["pending"] or {}
        # Pauses here. Resumed by the API with {"key": <hash of the exact call>} once the human approves.
        resume = interrupt({"type": "approval", "tool": pending.get("tool"), "args": pending.get("args"), "key": pending.get("key")})
        t = self.task
        self.db.refresh(t)
        if (resume or {}).get("key") != pending.get("key") or t.approved_key != pending.get("key"):
            return self._end("approval_mismatch", "Approved action does not match the pending action.", ErrorType.PERMISSION)
        try:
            tool, args = self.registry.prepare(pending["tool"], pending["args"])
        except ToolFailure as exc:
            return self._end("approval_invalid", exc.message, ErrorType.VALIDATION)
        if canonical_key(tool.name, args) != t.approved_key:  # approval is bound to the exact call
            return self._end("approval_mismatch", "Approved action does not match the pending action.", ErrorType.PERMISSION)
        t.pending_action = None
        self.db.commit()
        return {"call": pending, "pending": None, "route": "execute"}

    # ------------------------------------------------------------------ node: execute
    def n_execute(self, state: TaskState) -> dict:
        call = state["call"] or {}
        mem = self._mem(state)
        try:
            tool, args = self.registry.prepare(call["tool"], call["args"])
        except ToolFailure as exc:
            self._fail("internal_error", exc.message, ErrorType.INTERNAL)
            return {"route": "end"}
        self._execute(tool, args, args.model_dump(mode="json"), call.get("step", self.task.step_count), mem)
        if self.task.approved_key:  # an approval is single use
            self.task.approved_key = None
            self.db.commit()
        return {**self._mem_update(mem), "call": None, "route": "decide"}

    def _execute(self, tool, args, norm_args: dict, step: int, mem: dict) -> None:
        s = self.settings
        attempts = 1 + max(0, s.max_tool_retries)
        result = ToolResult.failure(ErrorType.INTERNAL, "not executed")
        for attempt in range(attempts):
            result = self.registry.invoke(tool, args, self.ctx)
            if result.error_type != ErrorType.TRANSIENT:
                break
            if tool.permission == "write" and tool.recover is not None:
                try:
                    recovered = tool.recover(self.ctx, args)
                except Exception as exc:  # noqa: BLE001
                    recovered = ToolResult.failure(ErrorType.INTERNAL, f"recovery check failed: {exc}")
                if recovered is not None:
                    self.emit(EventType.RECOVERY, "Inspected the database after the timeout before retrying: " + recovered.summary,
                              {"outcome": "found_existing" if recovered.ok else "conflict"}, step, tool.name)
                    result = recovered
                    break
                self.emit(EventType.RECOVERY, "Inspected the database: the write did not land, so retrying is safe.", {}, step, tool.name)
            if attempt == attempts - 1:
                break
            self.emit(EventType.RETRY, f"Transient error ({result.error}). Retry {attempt + 1}/{attempts - 1}",
                      {"error": result.error, "attempt": attempt + 1}, step, tool.name)
            time.sleep(s.retry_backoff_seconds * (2 ** attempt))

        for w in result.warnings:
            self.emit(EventType.SECURITY, w, {"warning": w}, step, tool.name)
        if result.ok:
            self.emit(EventType.TOOL_RESULT, result.summary, {"data": _cap(result.data)}, step, tool.name)
            mem["facts"] = {**mem["facts"], **result.memory}
        else:
            self.emit(EventType.TOOL_ERROR, result.error or "tool failed",
                      {"error_type": result.error_type.value if result.error_type else None, "error": result.error, "data": _cap(result.data)},
                      step, tool.name)
        self._observe(mem, step, tool.name, norm_args, result)

    def _observe(self, mem: dict, step: int, tool: str, args: dict, r: ToolResult) -> None:
        obs = {
            "step": step,
            "tool": tool,
            "args": args,
            "ok": r.ok,
            "summary": r.summary,
            "data": _cap(r.data),
            "error_type": r.error_type.value if r.error_type else None,
            "error": r.error,
        }
        mem["history"] = [*mem["history"], obs]
        self.task.memory = {"facts": mem["facts"], "history": mem["history"]}  # mirror for the UI / API
        self.db.commit()

    # ------------------------------------------------------------------ nodes: ask / await_input (interrupt)
    def n_ask(self, state: TaskState) -> dict:
        self._needs_input(state["ask"]["question"])
        return {}

    def n_await_input(self, state: TaskState) -> dict:
        ask = state["ask"]
        answer = interrupt({"type": "needs_input", "question": ask["question"]})
        msg = str((answer or {}).get("message", "")).strip()
        t = self.task
        self.db.refresh(t)
        if ask["stage"] == "plan":  # the task itself was ambiguous: plan again with the clarification
            t.text = f"{t.text}\n\nUser clarification: {msg}"
            t.plan, t.task_type, t.result = None, None, None
            t.memory, t.step_count = {"facts": {}, "history": []}, 0
            self.db.commit()
            return {"plan": None, "facts": {}, "history": [], "route": "plan"}
        mem = self._mem(state)  # the agent asked mid-run: record the answer and carry on
        t.result = None
        mem["facts"] = {**mem["facts"], "user_answer": msg}
        t.step_count += 1
        self.db.commit()
        self._observe(mem, t.step_count, "ask_user", {}, ToolResult(summary=f"The user answered: {msg}", data={"answer": msg}))
        return {**self._mem_update(mem), "route": "decide"}

    # ------------------------------------------------------------------ node: verify
    def n_verify(self, state: TaskState) -> dict:
        plan = Plan.model_validate(state["plan"])
        decision = Decision.model_validate(state["decision"])
        mem = self._mem(state)
        self.set_state(TaskStatus.VERIFYING)
        gate = run_gate(self.ctx, plan, decision, mem)
        self.emit(EventType.VERIFICATION, gate.summary, {"passed": gate.passed, "checks": gate.checks})
        if gate.passed:
            self.task.result = {
                "summary": decision.message or gate.summary,
                "verification": gate.summary,
                "evidence": gate.evidence,
                "checks": gate.checks,
                "data": decision.data,
            }
            self.task.status = TaskStatus.COMPLETED.value
            self.db.commit()
            self.emit(EventType.FINAL, decision.message or gate.summary, {"evidence": gate.evidence})
            self.emit(EventType.STATE, "Status: COMPLETED", {"status": "COMPLETED"})
        else:
            self._fail("verification_failed", gate.summary, ErrorType.VERIFICATION,
                       {"checks": gate.checks, "agent_claim": decision.message})
        return {"route": "end"}


def _cap(data: dict) -> dict:
    blob = json.dumps(data, default=str)
    if len(blob) <= MAX_OBS_CHARS:
        return data
    return {"truncated": True, "preview": blob[:MAX_OBS_CHARS]}


class AgentRunner:
    """Thread-safe entry point used by the API.

    BackgroundTasks run in a thread pool, so two tasks can run at once. A fresh `_TaskRun` (and a freshly
    compiled graph bound to it) is created per call, so no run can overwrite another run's session or task.
    The only shared object is the checkpointer, which LangGraph's SQLite saver guards with a lock.
    """

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings, llm: LLMClient,
                 registry: ToolRegistry, checkpointer: Any = None):
        self._checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        self._parts = (session_factory, settings, llm, registry, self._checkpointer)

    @property
    def checkpointer(self):
        return self._checkpointer

    def run(self, task_id: str) -> None:
        """Start a fresh run of a PENDING task."""
        _TaskRun(*self._parts).run(task_id)

    def resume(self, task_id: str, value: dict) -> None:
        """Continue a graph paused at an approval or a question."""
        _TaskRun(*self._parts).resume(task_id, value)

    def graph_info(self, task_id: Optional[str] = None) -> dict[str, Any]:
        """Static graph (Mermaid) and, for a task, where its saved run currently waits."""
        run = _TaskRun(*self._parts)
        graph = run.build_graph()
        info: dict[str, Any] = {"nodes": NODES, "mermaid": graph.get_graph().draw_mermaid(), "waiting_at": []}
        if task_id:
            info["waiting_at"] = list(graph.get_state(run._config(task_id)).next)
        return info
