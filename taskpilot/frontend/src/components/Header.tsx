import { AlertTriangle, FlaskConical, FileText, ListChecks, MessageSquare } from "lucide-react";
import type { Health } from "../types";

export type Tab = "tasks" | "chat" | "docs";
const TABS: { id: Tab; label: string; icon: typeof ListChecks }[] = [
  { id: "tasks", label: "Tasks", icon: ListChecks },
  { id: "chat", label: "Chat", icon: MessageSquare },
  { id: "docs", label: "Documents", icon: FileText },
];

interface Props { health: Health | null; offline: boolean; tab: Tab; onTab: (t: Tab) => void }

export default function Header({ health, offline, tab, onTab }: Props) {
  return (
    <header>
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
        <div>
          <h1 className="text-xl font-bold tracking-tight">TaskPilot</h1>
          <p className="text-sm text-slate2">An AI worker you can chat with: it plans, uses tools, checks its own work, and shows its evidence.</p>
        </div>
        {health && (
          <div className="text-right text-xs text-slate2">
            <div className="font-semibold text-ink">{health.llm_is_demo ? "Demo mode" : `${health.llm_provider} / ${health.llm_model}`}</div>
            <div>{health.llm_ok ? "Reasoning engine ready" : "Reasoning engine unavailable"}{health.engine ? ` · ${health.engine}` : ""}</div>
          </div>
        )}
      </div>
      <nav aria-label="Sections" className="mx-auto flex max-w-7xl gap-1 px-4 sm:px-6">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button key={id} onClick={() => onTab(id)} aria-current={tab === id ? "page" : undefined}
            className={`inline-flex items-center gap-2 rounded-t-md border border-b-0 px-4 py-2 text-sm font-semibold ${tab === id ? "border-rule bg-white text-signal" : "border-transparent text-slate2 hover:text-ink"}`}>
            <Icon size={16} aria-hidden /> {label}
          </button>
        ))}
      </nav>
      <div className="border-b border-rule" />
      {offline && (
        <div role="alert" className="border-y border-bad/30 bg-bad/10 px-4 py-2 text-center text-sm text-bad">
          <AlertTriangle size={14} className="mr-1.5 inline" aria-hidden />
          Cannot reach the backend. Start it with <code className="font-mono">uvicorn app.main:app --port 8000</code> from the backend folder.
        </div>
      )}
      {health?.llm_is_demo && (
        <div className="border-y border-warn/30 bg-warn/10 px-4 py-2 text-center text-sm text-warn">
          <FlaskConical size={14} className="mr-1.5 inline" aria-hidden />
          Demo mode: next actions and chat replies come from a fixed rule set, not a language model. Everything else (tools, database, approvals, verification) is real.
        </div>
      )}
      {health && !health.llm_is_demo && !health.llm_ok && (
        <div role="alert" className="border-y border-bad/30 bg-bad/10 px-4 py-2 text-center text-sm text-bad">
          <AlertTriangle size={14} className="mr-1.5 inline" aria-hidden />
          {health.llm_detail} Tasks and chat will fail honestly until this is fixed. Nothing falls back to demo mode automatically.
        </div>
      )}
    </header>
  );
}
