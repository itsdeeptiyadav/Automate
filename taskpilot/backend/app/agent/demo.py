"""DEMO MODE - deterministic rule-based policy. THIS IS NOT AN LLM.

`DemoClient` implements the same LLMClient interface so the rest of the system (planning, validation,
approvals, recovery, verification) runs unchanged. `DemoChatModel` is a tiny LangChain chat model with
the same property for the chat agent: it picks a tool from keywords and summarises the tool result.
Both exist only so the app can be demonstrated and tested without a model. The UI labels them clearly.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.llm import LLMClient
from app.documents.search import tokenize
from app.schemas.agent import Decision, DecisionContext, Plan, PlanStep

_COMPANY_RE = re.compile(
    r"invoice\s+(?:from|of|for)\s+(.+?)(?:\s*[,.;]|\s+and\s+|\s+then\s+|\s+extract|$)", re.I
)
_KEYWORD_RE = re.compile(r"""word\s+["'“‘]?([\w-]+)["'”’]?""", re.I)
_DOC_RE = re.compile(r"\b(uploaded|documents?|library|summari[sz]e)\b", re.I)
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
INVOICE_TASK_TEMPLATE = (
    "Find the latest invoice from {company}, extract its invoice number, amount, and due date, "
    "save it in the internal invoice system, verify that it was saved correctly, and tell me the result."
)


class DemoClient(LLMClient):
    provider = "demo"
    model = "deterministic-policy"
    is_demo = True

    @property
    def chat_model(self) -> BaseChatModel:  # type: ignore[override]
        return DemoChatModel()

    def health(self) -> dict[str, Any]:
        return {"ok": True, "detail": "DEMO MODE: deterministic rules, not a language model"}

    # ------------------------------------------------------------------ planning
    def plan(self, task_text: str, tools: list[dict]) -> Plan:
        low = task_text.lower()
        if "invoice" in low:
            m = _COMPANY_RE.search(task_text)
            if not m:
                return Plan(
                    task_type="invoice_ingest",
                    goal="Process an invoice",
                    needs_clarification="Which company's invoice should I process?",
                )
            company = m.group(1).strip().strip("'\"")
            return Plan(
                task_type="invoice_ingest",
                goal=f"Save the latest invoice from {company} into the internal invoice system and verify it.",
                params={"company": company},
                steps=[
                    PlanStep(description=f"Find and read invoices from {company}; pick the latest by date", tool="get_latest_invoice"),
                    PlanStep(description="Check the internal system for an existing record", tool="search_internal_invoices"),
                    PlanStep(description="Save the invoice (needs approval)", tool="create_invoice_record"),
                    PlanStep(description="Verify saved record against the source PDF", tool="verify_invoice_record"),
                ],
                expected_outcome="One stored record whose fields equal the latest invoice PDF.",
                requires_write=True,
            )
        if _DOC_RE.search(task_text):
            return Plan(
                task_type="document_qa",
                goal="Answer the question from the uploaded documents, with verbatim citations.",
                params={"question": task_text.strip()},
                steps=[
                    PlanStep(description="Search the uploaded documents for relevant passages", tool="search_documents"),
                    PlanStep(description="Read the best passage in full", tool="read_document"),
                ],
                expected_outcome="An answer whose every citation is a verbatim quote from a document the agent read.",
            )
        if "contract" in low or "text file" in low or "files" in low:
            m = _KEYWORD_RE.search(task_text)
            if not m:
                return Plan(task_type="file_search", goal="Search files", needs_clarification="Which word should I search for?")
            kw = m.group(1)
            return Plan(
                task_type="file_search",
                goal=f"Find workspace text files containing '{kw}'.",
                params={"keyword": kw},
                steps=[
                    PlanStep(description=f"Search file contents for '{kw}'", tool="search_file_contents"),
                    PlanStep(description="Read each match to confirm", tool="read_text_file"),
                ],
                expected_outcome="Filenames match an independent scan of the workspace.",
            )
        return Plan(task_type="unsupported", goal="This prototype handles invoice ingestion, workspace file search and document Q&A.")

    # ------------------------------------------------------------------ deciding
    def decide(self, ctx: DecisionContext) -> Decision:
        if ctx.plan.task_type == "invoice_ingest":
            return self._invoice(ctx)
        if ctx.plan.task_type == "document_qa":
            return self._document_qa(ctx)
        return self._file_search(ctx)

    @staticmethod
    def _last(ctx: DecisionContext, tool: str) -> Optional[dict]:
        for obs in reversed(ctx.history):
            if obs.get("tool") == tool:
                return obs
        return None

    def _invoice(self, ctx: DecisionContext) -> Decision:
        company = ctx.plan.params["company"]
        latest = self._last(ctx, "get_latest_invoice")
        if latest is None:
            return Decision(thought="Start by locating the latest invoice PDF.", action="tool",
                            tool="get_latest_invoice", args={"company": company})
        if not latest["ok"]:
            if latest.get("error_type") == "NOT_FOUND":
                if self._last(ctx, "search_internal_invoices") is None:
                    return Decision(thought="No source PDF. Check whether the internal system has anything for this company.",
                                    action="tool", tool="search_internal_invoices", args={"company": company})
                return Decision(action="fail", thought="No source invoice exists.",
                                message=f"No invoice PDF from '{company}' was found, so nothing was saved. {latest.get('error')}")
            return Decision(action="fail", thought="Lookup failed.", message=f"Could not read invoices: {latest.get('error')}")

        inv = ctx.facts["latest_invoice"]
        db = self._last(ctx, "search_internal_invoices")
        if db is None:
            return Decision(thought="Check for an existing record before writing.", action="tool",
                            tool="search_internal_invoices",
                            args={"company": inv["company"], "invoice_number": inv["invoice_number"]})
        created = self._last(ctx, "create_invoice_record")
        verified = self._last(ctx, "verify_invoice_record")
        if verified is not None:
            if verified["ok"] and verified["data"].get("passed"):
                return Decision(action="finish", thought="Verification passed.",
                                message=f"Invoice {inv['invoice_number']} ({inv['currency']} {inv['amount']}, due {inv['due_date']}) "
                                        f"is stored and matches {inv['source_file']}.")
            return Decision(action="fail", thought="Verification did not pass.",
                            message="The saved record does not match the source PDF.")
        exists = db["ok"] and db["data"].get("count", 0) > 0
        if created is None and not exists:
            return Decision(thought="No existing record. Save the latest invoice.", action="tool",
                            tool="create_invoice_record", args=dict(inv))
        if created is not None and not created["ok"] and created.get("error_type") != "CONFLICT":
            return Decision(action="fail", thought="Write failed.", message=f"Could not save the invoice: {created.get('error')}")
        return Decision(thought="Record exists. Verify it against the PDF.", action="tool",
                        tool="verify_invoice_record",
                        args={"invoice_number": inv["invoice_number"], "source_file": inv["source_file"], "company": inv["company"]})

    def _file_search(self, ctx: DecisionContext) -> Decision:
        kw = ctx.plan.params["keyword"]
        search = self._last(ctx, "search_file_contents")
        if search is None:
            return Decision(thought="Search the workspace text files.", action="tool", tool="search_file_contents",
                            args={"query": kw, "directory": "workspace"})
        if not search["ok"]:
            return Decision(action="fail", message=f"Search failed: {search.get('error')}")
        files = search["data"]["files"]
        read = {o["args"].get("path") for o in ctx.history if o.get("tool") == "read_text_file"}
        for f in files[:5]:
            if f not in read:
                return Decision(thought=f"Confirm {f} by reading it.", action="tool", tool="read_text_file", args={"path": f})
        return Decision(action="finish", thought="All matches confirmed.", data={"files": files},
                        message=f"{len(files)} file(s) contain the word '{kw}'.")

    def _document_qa(self, ctx: DecisionContext) -> Decision:
        question = ctx.plan.params["question"]
        search = self._last(ctx, "search_documents")
        if search is None:
            return Decision(thought="Search the uploaded documents.", action="tool", tool="search_documents",
                            args={"query": " ".join(tokenize(question)[:12]) or question})
        if not search["ok"]:
            return Decision(action="fail", message=f"Search failed: {search.get('error')}")
        results = search["data"].get("results", [])
        if not results:
            return Decision(action="fail", thought="Nothing relevant.",
                            message="None of the uploaded documents contains a passage that matches the question.")
        best = results[0]
        read = self._last(ctx, "read_document")
        if read is None:
            return Decision(thought=f"Read the best passage in '{best['filename']}'.", action="tool", tool="read_document",
                            args={"document_id": best["document_id"], "start": best.get("start_char", 0), "max_chars": 2000})
        if not read["ok"]:
            return Decision(action="fail", message=f"Could not read the document: {read.get('error')}")
        content = read["data"]["content"]
        terms = set(tokenize(question))
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", content) if len(s.strip()) >= 8]
        quote = max(sentences, key=lambda s: len(terms & set(tokenize(s))), default=content[:200].strip())[:300].strip()
        return Decision(
            action="finish", thought="Found a passage that answers the question.",
            message=f"According to {best['filename']}: {quote}",
            data={"answer": f"According to {best['filename']}: {quote}",
                  "citations": [{"document_id": read["data"]["document_id"], "quote": quote}]},
        )


# ====================================================================== chat model (demo)
def _tool_payload(msg: ToolMessage) -> dict:
    try:
        return json.loads(msg.content) if isinstance(msg.content, str) else {}
    except json.JSONDecodeError:
        return {}


class DemoChatModel(BaseChatModel):
    """Rule-based stand-in for a chat model with tool calling. NOT an LLM."""

    @property
    def _llm_type(self) -> str:
        return "taskpilot-demo"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        names = [t.name if hasattr(t, "name") else t.get("name") for t in tools]
        return self.bind(tool_names=names)

    # -- routing
    def _route(self, text: str, names: list[str]) -> Optional[tuple[str, dict]]:
        low = text.lower()

        def has(n: str) -> bool:
            return n in names

        m = _UUID_RE.search(text)
        if m and has("get_task_status"):
            return "get_task_status", {"task_id": m.group(0)}
        company = _COMPANY_RE.search(text)
        if re.search(r"\b(start|run|process|ingest|save)\b", low) and "invoice" in low and company and has("start_task"):
            return "start_task", {"text": INVOICE_TASK_TEMPLATE.format(company=company.group(1).strip().strip("'\""))}
        if "invoice" in low and has("list_invoice_records"):
            args: dict[str, Any] = {"limit": 10}
            if company:
                args["company"] = company.group(1).strip().strip("'\"")
            return "list_invoice_records", args
        if re.search(r"\b(what|which|list)\b.*\b(documents|files)\b.*\b(have|uploaded|library)\b|\blist (my )?documents\b", low) and has("list_documents"):
            return "list_documents", {}
        if _DOC_RE.search(text) or "according to" in low or "search" in low:
            if has("search_documents"):
                return "search_documents", {"query": " ".join(tokenize(text)[:12]) or text}
        return None

    # -- replies
    def _answer_from_tool(self, messages: list[BaseMessage]) -> str:
        last = messages[-1]
        assert isinstance(last, ToolMessage)
        name = last.name or ""
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                for call in m.tool_calls:
                    if call["id"] == last.tool_call_id:
                        name = call["name"]
                break
        payload = _tool_payload(last)
        data = payload.get("data", {})
        if not payload.get("ok", True):
            return f"That didn't work: {payload.get('summary') or 'the tool returned an error'}."
        if name == "search_documents":
            results = data.get("results", [])
            if not results:
                return "I couldn't find anything in your uploaded documents that matches that."
            lines = [f"- {r['filename']}: {r['snippet']}" for r in results[:3]]
            return "Here is what I found in your documents:\n" + "\n".join(lines)
        if name == "list_documents":
            docs = data.get("documents", [])
            return ("Your library has: " + ", ".join(f"{d['filename']} (#{d['document_id']})" for d in docs) + ".") if docs else "Your library is empty. Upload a document in the Documents tab."
        if name == "list_invoice_records":
            recs = data.get("records", [])
            if not recs:
                return "There are no invoices in the internal invoice system yet."
            return "Invoices on file:\n" + "\n".join(f"- {r['invoice_number']} from {r['company']}: {r['currency']} {r['amount']}, due {r['due_date']}" for r in recs[:5])
        if name == "start_task":
            return (f"I started task {data.get('task_id')}. Open it in the Tasks tab to watch progress; "
                    "if it needs to save something it will ask for your approval there.")
        if name == "get_task_status":
            extra = f" Result: {data['summary']}" if data.get("summary") else ""
            return f"Task {data.get('task_id')} is {data.get('status')}.{extra}"
        return payload.get("summary", "Done.")

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        names: list[str] = list(kwargs.get("tool_names") or [])
        last = messages[-1]
        if isinstance(last, ToolMessage):
            msg = AIMessage(content=self._answer_from_tool(messages))
        else:
            text = last.content if isinstance(last.content, str) else str(last.content)
            routed = self._route(text, names)
            if routed:
                msg = AIMessage(content="", tool_calls=[{"name": routed[0], "args": routed[1], "id": f"demo_{len(messages)}", "type": "tool_call"}])
            else:
                msg = AIMessage(content=(
                    "I'm the demo assistant (rule-based, not a language model). I can search your uploaded documents, "
                    "look up invoices, and start tasks. Try: \"What do my documents say about refunds?\" or "
                    "\"Show my invoices\"."
                ))
        return ChatResult(generations=[ChatGeneration(message=msg)])
