import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ApprovalCard from "./ApprovalCard";
import Composer from "./Composer";
import InvoiceTable from "./InvoiceTable";
import ResultPanel from "./ResultPanel";
import StatusPanel from "./StatusPanel";
import Timeline from "./Timeline";
import { currentAction } from "../lib";
import type { TaskDetail, TaskEvent } from "../types";

const baseTask: TaskDetail = {
  id: "t1", text: "x", status: "RUNNING", task_type: "invoice_ingest", llm_provider: "demo", llm_model: "deterministic-policy",
  step_count: 3, created_at: "2026-10-13T08:00:00Z", updated_at: "2026-10-13T08:00:01Z", plan: null, result: null, error: null,
  pending_action: null, max_steps: 12, verifications: [],
};
const ev = (over: Partial<TaskEvent>): TaskEvent => ({
  id: 1, seq: 1, ts: "2026-10-13T08:00:00Z", type: "tool_call", title: "Call get_latest_invoice", step: 1, tool: "get_latest_invoice", payload: {}, ...over,
});

test("composer fills the example task and submits trimmed text", async () => {
  const onRun = vi.fn();
  render(<Composer onRun={onRun} disabled={false} />);
  expect(screen.getByRole("button", { name: /run task/i })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: /example: invoice/i }));
  expect((screen.getByLabelText(/what should the worker do/i) as HTMLTextAreaElement).value).toMatch(/Acme Corporation/);
  await userEvent.click(screen.getByRole("button", { name: /run task/i }));
  expect(onRun).toHaveBeenCalledWith(expect.stringContaining("latest invoice from Acme Corporation"));
});

test("status panel shows the step count, current action and retries", () => {
  const events = [ev({ id: 1, seq: 1, type: "retry", title: "retry", tool: "x" }), ev({ id: 2, seq: 2, tool: "search_internal_invoices" })];
  render(<StatusPanel task={baseTask} events={events} onCancel={() => {}} busy={false} />);
  expect(screen.getAllByText("Running").length).toBe(2); // status chip + progress label
  expect(screen.getByText(/Step 3 of 12/)).toBeInTheDocument();
  expect(screen.getByText("search_internal_invoices")).toBeInTheDocument();
  expect(screen.getByText("Demo rules (not an LLM)")).toBeInTheDocument();
});

test("currentAction is null once the call has a result", () => {
  expect(currentAction([ev({ seq: 1 }), ev({ seq: 2, type: "tool_result" })])).toBeNull();
  expect(currentAction([ev({ seq: 1 })])).toBe("get_latest_invoice");
});

test("approval card shows exact arguments and reports the decision", async () => {
  const onDecide = vi.fn();
  render(<ApprovalCard pending={{ tool: "create_invoice_record", args: { amount: "4275.00", invoice_number: "ACM-2026-0467" }, key: "k" }} onDecide={onDecide} busy={false} />);
  expect(screen.getByText("4275.00")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /approve and save/i }));
  await userEvent.click(screen.getByRole("button", { name: /deny/i }));
  expect(onDecide.mock.calls).toEqual([[true], [false]]);
});

test("timeline lists events and highlights retry/recovery", () => {
  render(<Timeline events={[
    ev({ id: 1, seq: 1, type: "retry", title: "Transient error. Retry 1/2", tool: "create_invoice_record" }),
    ev({ id: 2, seq: 2, type: "recovery", title: "Inspected the database after the timeout" }),
  ]} />);
  expect(screen.getByText(/Retry 1\/2/)).toBeInTheDocument();
  expect(screen.getByText(/Inspected the database/)).toBeInTheDocument();
});

test("failed result never looks like success and shows the mismatching field", () => {
  const failed: TaskDetail = { ...baseTask, status: "FAILED", error: {
    kind: "verification_failed", error_type: "VERIFICATION", message: "Independent check FAILED: amount",
    checks: [{ field: "amount", expected: "4275.00", actual: "1.00", match: false }] } };
  render(<ResultPanel task={failed} onInput={() => {}} busy={false} />);
  expect(screen.getByText(/did not succeed/i)).toBeInTheDocument();
  expect(screen.getByText("1.00")).toBeInTheDocument();
  expect(screen.getByLabelText("mismatch")).toBeInTheDocument();
  expect(screen.queryByText(/verified/i)).not.toBeInTheDocument();
});

test("invoice table renders database rows and an empty state", () => {
  const { rerender } = render(<InvoiceTable invoices={[]} />);
  expect(screen.getByText(/No invoices saved yet/)).toBeInTheDocument();
  rerender(<InvoiceTable invoices={[{ id: 1, company: "Acme Corporation", invoice_number: "ACM-2026-0467", invoice_date: "2026-09-22", due_date: "2026-10-22", amount: "4275.00", currency: "USD", source_file: "invoices/invoice_003.pdf", created_by_task_id: "t1", created_at: "2026-10-13T08:00:00Z" }]} />);
  expect(screen.getByText("ACM-2026-0467")).toBeInTheDocument();
  expect(screen.getByText("USD 4275.00")).toBeInTheDocument();
});
