"""Deterministic final verification ("gates").

The LLM may say "finish", but the task only becomes COMPLETED if these checks
pass. They re-derive the expected outcome from the source of truth (the PDFs /
workspace files) with code that does not trust the LLM's claims.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select

from app.documents.search import normalise_for_match, quote_in_text
from app.models.entities import Document, Invoice
from app.schemas.agent import Decision, Plan
from app.tools.base import ToolContext, ToolFailure
from app.tools.invoices import compare_invoice, pick_latest, scan_company_invoices, store_verification


class GateResult(BaseModel):
    passed: bool
    summary: str
    checks: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}


def run_gate(ctx: ToolContext, plan: Plan, decision: Decision, memory: dict[str, Any]) -> GateResult:
    try:
        if plan.task_type == "invoice_ingest":
            return _invoice_gate(ctx, plan)
        if plan.task_type == "file_search":
            return _file_search_gate(ctx, plan, decision, memory)
        if plan.task_type == "document_qa":
            return _document_qa_gate(ctx, plan, decision, memory)
    except ToolFailure as exc:
        return GateResult(passed=False, summary=f"Verification could not run: {exc.message}")
    return GateResult(passed=False, summary="No verifier for this task type.")


def _invoice_gate(ctx: ToolContext, plan: Plan) -> GateResult:
    company = plan.params["company"]
    scan = scan_company_invoices(ctx, company)
    if not scan.invoices:
        return GateResult(
            passed=False,
            summary=f"No source invoice from '{company}' exists, so success cannot be claimed.",
            checks=[{"field": "source_invoice", "expected": "present", "actual": "none", "match": False}],
        )
    latest = pick_latest(scan.invoices)
    row = ctx.db.scalars(
        select(Invoice).where(Invoice.company == latest.company, Invoice.invoice_number == latest.invoice_number)
    ).first()
    if row is None:
        checks = [{"field": "record", "expected": f"{latest.invoice_number} saved", "actual": "missing", "match": False}]
    else:
        checks = compare_invoice(row, latest)
        count = ctx.db.scalar(
            select(func.count()).select_from(Invoice).where(
                Invoice.company == latest.company, Invoice.invoice_number == latest.invoice_number
            )
        )
        checks.append({"field": "no_duplicates", "expected": "1 record", "actual": f"{count} record(s)", "match": count == 1})
        stray = ctx.db.scalars(
            select(Invoice).where(Invoice.created_by_task_id == ctx.task_id, Invoice.id != row.id)
        ).all()
        checks.append(
            {
                "field": "no_unintended_records",
                "expected": "0 other records written by this task",
                "actual": f"{len(stray)}: {[s.invoice_number for s in stray]}" if stray else "0",
                "match": not stray,
            }
        )
    passed = store_verification(ctx, row.id if row else None, "final", checks)
    candidates = [
        {"file": i.source_file, "invoice_number": i.invoice_number, "invoice_date": i.invoice_date.isoformat()}
        for i in sorted(scan.invoices, key=lambda i: (i.invoice_date, i.invoice_number), reverse=True)
    ]
    evidence = {
        "invoice": row.as_dict() if row else None,
        "source_file": latest.source_file,
        "selection_rule": f"Latest invoice_date among {len(candidates)} invoice(s) whose Vendor is exactly '{company}'.",
        "candidates_considered": candidates,
    }
    summary = (
        f"Independent check passed: record {latest.invoice_number} matches {latest.source_file} on every field."
        if passed
        else "Independent check FAILED: " + ", ".join(c["field"] for c in checks if not c["match"])
    )
    return GateResult(passed=passed, summary=summary, checks=checks, evidence=evidence)


def _independent_text_search(ctx: ToolContext, keyword: str) -> dict[str, list[dict[str, Any]]]:
    """Token-based scan (deliberately a different method from the tool's regex)."""
    kw = keyword.casefold()
    single = re.fullmatch(r"\w+", kw) is not None
    base = ctx.settings.text_workspace_dir
    found: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".txt", ".md"}:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            low = line.casefold()
            hit = kw in re.findall(r"\w+", low) if single else kw in low
            if hit:
                rel = p.resolve().relative_to(ctx.root.resolve()).as_posix()
                found.setdefault(rel, []).append({"line": n, "text": line.strip()[:160]})
    return found


def _file_search_gate(ctx: ToolContext, plan: Plan, decision: Decision, memory: dict[str, Any]) -> GateResult:
    keyword = plan.params["keyword"]
    expected = _independent_text_search(ctx, keyword)
    claimed_raw = decision.data.get("files")
    if claimed_raw is None:
        claimed_raw = memory.get("facts", {}).get("search_matches", [])
    if isinstance(claimed_raw, str):
        claimed_raw = [claimed_raw]
    claimed = {str(f).replace("\\", "/") for f in claimed_raw or []}
    exp = set(expected)
    checks = [
        {
            "field": "file_list",
            "expected": sorted(exp),
            "actual": sorted(claimed),
            "match": exp == claimed,
        }
    ]
    evidence = {"keyword": keyword, "files": [{"path": p, "lines": expected[p][:3]} for p in sorted(expected)]}
    ok = exp == claimed
    return GateResult(
        passed=ok,
        summary=f"Independent scan found {len(exp)} file(s) containing '{keyword}'; the reported list "
        + ("matches." if ok else "does NOT match."),
        checks=checks,
        evidence=evidence,
    )


def _documents_seen(memory: dict[str, Any]) -> set[int]:
    """Document ids the agent actually saw through a tool in this task (read, or returned by a search)."""
    seen: set[int] = set()
    for obs in memory.get("history", []):
        if not obs.get("ok"):
            continue
        if obs.get("tool") == "read_document":
            did = (obs.get("args") or {}).get("document_id")
            if isinstance(did, int):
                seen.add(did)
        elif obs.get("tool") == "search_documents":
            for r in (obs.get("data") or {}).get("results", []):
                if isinstance(r.get("document_id"), int):
                    seen.add(r["document_id"])
    return seen


def _document_qa_gate(ctx: ToolContext, plan: Plan, decision: Decision, memory: dict[str, Any]) -> GateResult:
    """An answer only counts if every citation is a verbatim quote from a document the agent really read."""
    data = decision.data or {}
    answer = str(data.get("answer") or decision.message or "").strip()
    raw = data.get("citations")
    citations = [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
    seen = _documents_seen(memory)
    checks: list[dict[str, Any]] = [
        {"field": "answer", "expected": "non-empty", "actual": f"{len(answer)} characters", "match": bool(answer)},
        {"field": "citations", "expected": "at least 1", "actual": str(len(citations)), "match": len(citations) >= 1},
    ]
    evidence_cites: list[dict[str, Any]] = []
    for n, c in enumerate(citations, 1):
        quote = str(c.get("quote") or "")
        try:
            did = int(c.get("document_id"))
        except (TypeError, ValueError):
            did = None
        doc = ctx.db.get(Document, did) if did is not None else None
        if doc is None:
            problem = "document does not exist"
        elif did not in seen:
            problem = "the agent never read or searched this document in this task"
        elif len(normalise_for_match(quote).strip("\"'“”‘’ .…")) < 8:
            problem = "quote is too short to be evidence"
        elif not quote_in_text(quote, doc.text):
            problem = "quote does not appear verbatim in the document"
        else:
            problem = ""
        checks.append({"field": f"citation_{n}", "expected": "verbatim quote from a document the agent read",
                       "actual": problem or f"verified in '{doc.filename}'", "match": not problem})
        if not problem and doc is not None:
            evidence_cites.append({"document_id": doc.id, "filename": doc.filename, "quote": quote.strip()})
    passed = all(c["match"] for c in checks)
    summary = (f"Independent check passed: {len(evidence_cites)} citation(s) are verbatim quotes from documents the agent read."
               if passed else "Independent check FAILED: " + ", ".join(c["field"] for c in checks if not c["match"]))
    return GateResult(passed=passed, summary=summary, checks=checks,
                      evidence={"question": plan.params.get("question", ""), "answer": answer, "citations": evidence_cites})
