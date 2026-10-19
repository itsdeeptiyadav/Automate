import type { TaskEvent, TaskStatus } from "./types";

export const POLLING: ReadonlySet<TaskStatus> = new Set<TaskStatus>(["PLANNING", "RUNNING", "VERIFYING"]);
export const TERMINAL: ReadonlySet<TaskStatus> = new Set<TaskStatus>(["COMPLETED", "FAILED", "CANCELLED"]);

export const EXAMPLE_INVOICE_TASK =
  "Find the latest invoice from Acme Corporation, extract its invoice number, amount, and due date, save it in the internal invoice system, verify that it was saved correctly, and tell me the result.";
export const EXAMPLE_FILE_TASK =
  "Find the text files in the sample workspace that contain the word 'contract' and return their filenames with evidence.";

export const EXAMPLE_DOC_TASK =
  "According to my uploaded documents, what does the refund policy say? Cite the exact wording.";

export const CHAT_SUGGESTIONS = [
  "What documents do I have?",
  "What do my documents say about refunds?",
  "Show my invoices",
  "Process the latest invoice from Acme Corporation and save it",
];

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export const STATUS_LABEL: Record<TaskStatus, string> = {
  PENDING: "Not started",
  PLANNING: "Planning",
  RUNNING: "Running",
  AWAITING_APPROVAL: "Waiting for your approval",
  VERIFYING: "Verifying",
  COMPLETED: "Completed and verified",
  FAILED: "Failed",
  NEEDS_INPUT: "Needs your input",
  CANCELLED: "Cancelled",
};

export function statusTone(s: TaskStatus): "ok" | "bad" | "warn" | "signal" | "muted" {
  if (s === "COMPLETED") return "ok";
  if (s === "FAILED") return "bad";
  if (s === "AWAITING_APPROVAL" || s === "NEEDS_INPUT") return "warn";
  if (s === "CANCELLED" || s === "PENDING") return "muted";
  return "signal";
}

/** The tool call that has started but not produced a result/error yet. */
export function currentAction(events: TaskEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type === "tool_result" || e.type === "tool_error") return null;
    if (e.type === "tool_call") return e.tool;
  }
  return null;
}

export function clock(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour12: false });
}

export function show(v: unknown): string {
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  if (v === null || v === undefined) return "-";
  return String(v);
}
