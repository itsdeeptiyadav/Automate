export type TaskStatus =
  | "PENDING" | "PLANNING" | "RUNNING" | "AWAITING_APPROVAL" | "VERIFYING"
  | "COMPLETED" | "FAILED" | "NEEDS_INPUT" | "CANCELLED";

export interface PlanStep { description: string; tool: string | null }
export interface Plan {
  task_type: string;
  goal: string;
  params: Record<string, string>;
  steps: PlanStep[];
  expected_outcome: string;
  requires_write: boolean;
  needs_clarification: string | null;
}
export interface Check { field: string; expected: unknown; actual: unknown; match: boolean }
export interface Verification { id: number; invoice_id: number | null; kind: string; passed: boolean; checks: Check[]; created_at: string }

export interface TaskSummary {
  id: string; text: string; status: TaskStatus; task_type: string | null;
  llm_provider: string; llm_model: string; step_count: number; created_at: string; updated_at: string;
}
export interface TaskDetail extends TaskSummary {
  plan: Plan | null;
  result: Record<string, any> | null;
  error: { kind: string; error_type: string; message: string; checks?: Check[] } | null;
  pending_action: { tool: string; args: Record<string, unknown>; key: string; thought?: string; step?: number } | null;
  max_steps: number;
  verifications: Verification[];
}
export interface TaskEvent {
  id: number; seq: number; ts: string; type: string; title: string;
  step: number | null; tool: string | null; payload: Record<string, any>;
}
export interface Invoice {
  id: number; company: string; invoice_number: string; invoice_date: string; due_date: string;
  amount: string; currency: string; source_file: string; created_by_task_id: string | null; created_at: string;
}
export interface UploadResult {
  file: string; company: string; invoice_number: string; invoice_date: string; due_date: string;
  amount: string; currency: string; warnings: string[]; suggested_task: string;
}
export interface Health {
  status: string; llm_provider: string; llm_model: string; llm_is_demo: boolean; llm_ok: boolean; llm_detail: string;
  engine?: string; chat_available?: boolean; tool_calling?: string;
}

export interface DocumentInfo {
  id: number; filename: string; kind: string; size_bytes: number; page_count: number | null;
  char_count: number; chunk_count: number; created_at: string;
}
export interface SearchHit { document_id: number; filename: string; chunk: number; start_char: number; score: number; snippet: string }
export interface Conversation { id: string; title: string; created_at: string; updated_at: string }
export interface ToolTrace { tool: string; args: Record<string, unknown>; ok: boolean; summary: string; task_id?: string }
export interface ChatMessage { id: number; seq: number; role: "user" | "assistant"; content: string; trace: ToolTrace[]; created_at: string }
export interface ConversationDetail extends Conversation { messages: ChatMessage[] }
export interface ChatReply { user: ChatMessage; assistant: ChatMessage; started_task_ids: string[] }
