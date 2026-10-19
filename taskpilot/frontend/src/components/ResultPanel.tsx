import { Check, X } from "lucide-react";
import { useState } from "react";
import { show } from "../lib";
import type { Check as CheckT, TaskDetail } from "../types";
import { Section } from "./Common";

function ChecksTable({ checks }: { checks: CheckT[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-slate2">
          <tr><th className="py-1 pr-3 font-medium">Field</th><th className="py-1 pr-3 font-medium">Source of truth</th><th className="py-1 pr-3 font-medium">Stored / reported</th><th className="py-1" /></tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {checks.map((c) => (
            <tr key={c.field} className={c.match ? "" : "bg-bad/5"}>
              <td className="py-1.5 pr-3 font-medium">{c.field}</td>
              <td className="py-1.5 pr-3 break-all">{show(c.expected)}</td>
              <td className="py-1.5 pr-3 break-all">{show(c.actual)}</td>
              <td className="py-1.5">{c.match ? <Check size={16} className="text-ok" aria-label="match" /> : <X size={16} className="text-bad" aria-label="mismatch" />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Evidence({ task }: { task: TaskDetail }) {
  const ev = task.result?.evidence;
  if (!ev) return null;
  if (task.task_type === "document_qa") {
    return (
      <div className="space-y-3 text-sm">
        <p className="leading-relaxed">{ev.answer}</p>
        <ul className="space-y-2">
          {ev.citations.map((c: { document_id: number; filename: string; quote: string }, i: number) => (
            <li key={i} className="rounded-md border border-ok/30 bg-ok/5 px-3 py-2">
              <blockquote className="italic">“{c.quote}”</blockquote>
              <div className="mt-1 text-xs text-slate2">{c.filename} (document #{c.document_id}), verified verbatim</div>
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (task.task_type === "file_search") {
    return (
      <ul className="space-y-2 text-sm">
        {ev.files.map((f: { path: string; lines: { line: number; text: string }[] }) => (
          <li key={f.path}>
            <div className="font-medium">{f.path}</div>
            {f.lines.map((l) => <div key={l.line} className="text-slate2">line {l.line}: {l.text}</div>)}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="space-y-2 text-sm">
      <p className="text-slate2">{ev.selection_rule}</p>
      <ul className="divide-y divide-rule rounded-md border border-rule">
        {ev.candidates_considered.map((c: { file: string; invoice_number: string; invoice_date: string }) => (
          <li key={c.file} className={`flex flex-wrap justify-between gap-2 px-3 py-1.5 ${c.file === ev.source_file ? "bg-ok/10 font-semibold" : ""}`}>
            <span>{c.invoice_number}</span><span className="text-slate2">{c.invoice_date}</span><span className="text-slate2">{c.file}{c.file === ev.source_file ? " (selected)" : ""}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface Props { task: TaskDetail; onInput: (m: string) => void; busy: boolean }

export default function ResultPanel({ task, onInput, busy }: Props) {
  const [answer, setAnswer] = useState("");

  if (task.status === "NEEDS_INPUT") {
    return (
      <Section title="The worker needs more information">
        <p className="mb-3 text-sm">{task.result?.question}</p>
        <div className="flex gap-2">
          <input aria-label="Your answer" value={answer} onChange={(e) => setAnswer(e.target.value)}
            className="min-w-0 flex-1 rounded-md border border-rule px-3 py-2 text-sm focus:border-signal" placeholder="Type your answer" />
          <button className="btn-primary" disabled={busy || !answer.trim()} onClick={() => { onInput(answer.trim()); setAnswer(""); }}>Continue</button>
        </div>
      </Section>
    );
  }
  if (task.status === "FAILED" && task.error) {
    return (
      <Section title="The task did not succeed" className="border-bad/40">
        <p className="text-sm font-medium text-bad">{task.error.message}</p>
        <p className="mt-1 text-xs text-slate2">Reason: {task.error.kind} ({task.error.error_type}). No success was reported.</p>
        {task.error.checks && <div className="mt-3"><ChecksTable checks={task.error.checks} /></div>}
      </Section>
    );
  }
  if (task.status === "CANCELLED") {
    return <Section title="Cancelled"><p className="text-sm text-slate2">The task was stopped before completion. Nothing further was run.</p></Section>;
  }
  if (task.status === "COMPLETED" && task.result) {
    return (
      <Section title="Result" className="border-ok/40">
        <p className="text-[15px] font-medium leading-snug">{task.result.summary}</p>
        <p className="mt-1 text-sm text-ok">{task.result.verification}</p>
        <h3 className="mb-1 mt-4 text-sm font-semibold">Independent verification</h3>
        <ChecksTable checks={task.result.checks} />
        <h3 className="mb-1 mt-4 text-sm font-semibold">Evidence</h3>
        <Evidence task={task} />
      </Section>
    );
  }
  return null;
}
