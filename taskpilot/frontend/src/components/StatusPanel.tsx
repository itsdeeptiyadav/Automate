import { Ban, Loader2 } from "lucide-react";
import { currentAction, TERMINAL } from "../lib";
import type { TaskDetail, TaskEvent, TaskStatus } from "../types";
import { StatusChip } from "./Common";

const STAGES = ["Planning", "Running", "Verifying", "Done"];

function stageIndex(s: TaskStatus): number {
  switch (s) {
    case "PLANNING": return 0;
    case "RUNNING": case "AWAITING_APPROVAL": return 1;
    case "VERIFYING": return 2;
    case "COMPLETED": return 3;
    default: return -1;
  }
}

interface Props { task: TaskDetail; events: TaskEvent[]; onCancel: () => void; busy: boolean }

export default function StatusPanel({ task, events, onCancel, busy }: Props) {
  const idx = stageIndex(task.status);
  const action = currentAction(events);
  const retries = events.filter((e) => e.type === "retry").length;
  const working = ["PLANNING", "RUNNING", "VERIFYING"].includes(task.status);
  const stopped = task.status === "FAILED" || task.status === "CANCELLED" || task.status === "NEEDS_INPUT";

  return (
    <div className="rounded-xl border border-rule bg-white p-5" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <StatusChip status={task.status} />
          {working && <Loader2 size={16} className="animate-spin text-signal" aria-label="working" />}
          <span className="text-sm text-slate2">
            Step {task.step_count} of {task.max_steps} allowed
          </span>
        </div>
        {!TERMINAL.has(task.status) && (
          <button className="btn-danger !py-1.5" onClick={onCancel} disabled={busy}>
            <Ban size={15} aria-hidden /> Cancel
          </button>
        )}
      </div>

      <ol className="mt-4 grid grid-cols-4 gap-1.5" aria-label="Progress">
        {STAGES.map((label, i) => {
          const done = idx > i || (idx === 3 && i === 3);
          const current = idx === i && !done;
          const tone = done ? "bg-ok" : current ? (task.status === "AWAITING_APPROVAL" ? "bg-warn" : "bg-signal") : stopped ? "bg-bad/25" : "bg-rule";
          return (
            <li key={label}>
              <div className={`h-1.5 rounded-full ${tone}`} />
              <span className={`mt-1.5 block text-xs ${current || done ? "font-semibold text-ink" : "text-slate2"}`}>{label}</span>
            </li>
          );
        })}
      </ol>

      <dl className="mt-4 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-slate2">Current action</dt>
          <dd className="font-medium">{action ?? (working ? "Deciding next step" : "None")}</dd>
        </div>
        <div>
          <dt className="text-slate2">Retries so far</dt>
          <dd className="font-medium">{retries}</dd>
        </div>
        <div>
          <dt className="text-slate2">Reasoning engine</dt>
          <dd className="font-medium">{task.llm_provider === "demo" ? "Demo rules (not an LLM)" : `${task.llm_provider} / ${task.llm_model}`}</dd>
        </div>
      </dl>
    </div>
  );
}
