import { useCallback, useEffect, useState } from "react";
import ApprovalCard from "../components/ApprovalCard";
import Composer from "../components/Composer";
import InvoiceTable from "../components/InvoiceTable";
import PlanPanel from "../components/PlanPanel";
import ResultPanel from "../components/ResultPanel";
import StatusPanel from "../components/StatusPanel";
import TaskHistory from "../components/TaskHistory";
import UploadCard from "../components/UploadCard";
import Timeline from "../components/Timeline";
import { useTaskRun } from "../hooks/useTaskRun";
import { api } from "../services/api";
import type { Invoice, TaskSummary } from "../types";

interface Props { offline: boolean; openTaskId?: { id: string; n: number }; prefill?: { text: string; n: number } }

export default function Console({ offline, openTaskId, prefill }: Props) {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [history, setHistory] = useState<TaskSummary[]>([]);
  const [uploadPrefill, setUploadPrefill] = useState<{ text: string; n: number } | undefined>();

  const reloadSide = useCallback(() => {
    api.invoices().then(setInvoices).catch(() => undefined);
    api.listTasks().then(setHistory).catch(() => undefined);
  }, []);

  const run = useTaskRun(reloadSide);
  const { task, events } = run;

  useEffect(() => { reloadSide(); }, [reloadSide]);
  useEffect(() => { if (openTaskId) run.load(openTaskId.id); }, [openTaskId?.n]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <main className="mx-auto grid max-w-7xl gap-5 px-4 pb-16 pt-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="min-w-0 space-y-5">
          <Composer onRun={run.submit} disabled={run.busy || run.active || offline} prefill={prefill ?? uploadPrefill} />
          {run.error && (
            <p role="alert" className="rounded-md border border-bad/30 bg-bad/10 px-4 py-2 text-sm text-bad">{run.error}</p>
          )}
          {task && task.status === "AWAITING_APPROVAL" && task.pending_action && (
            <div className="lg:hidden"><ApprovalCard pending={task.pending_action} onDecide={run.approve} busy={run.busy} /></div>
          )}
          {task && <StatusPanel task={task} events={events} onCancel={run.cancel} busy={run.busy} />}
          {task && <PlanPanel plan={task.plan} />}
          {task && <ResultPanel task={task} onInput={run.sendInput} busy={run.busy} />}
          <Timeline events={events} />
        </div>
        <aside className="min-w-0 space-y-5">
          {task && task.status === "AWAITING_APPROVAL" && task.pending_action && (
            <div className="hidden lg:block"><ApprovalCard pending={task.pending_action} onDecide={run.approve} busy={run.busy} /></div>
          )}
          <UploadCard onUploaded={reloadSide} onUse={(text) => setUploadPrefill({ text, n: Date.now() })} disabled={run.active || offline} />
          <InvoiceTable invoices={invoices} highlightTaskId={task?.id} />
          <TaskHistory tasks={history} selectedId={task?.id} onSelect={run.load} />
        </aside>
    </main>
  );
}
