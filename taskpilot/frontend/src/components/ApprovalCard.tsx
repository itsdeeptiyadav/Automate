import { ShieldQuestion } from "lucide-react";
import type { TaskDetail } from "../types";
import { show } from "../lib";

interface Props { pending: NonNullable<TaskDetail["pending_action"]>; onDecide: (ok: boolean) => void; busy: boolean }

export default function ApprovalCard({ pending, onDecide, busy }: Props) {
  return (
    <section className="rounded-xl border-2 border-warn bg-white" role="alert" aria-label="Approval required">
      <header className="flex items-center gap-2 border-b border-warn/30 bg-warn/10 px-5 py-3">
        <ShieldQuestion size={18} className="text-warn" aria-hidden />
        <h2 className="text-[15px] font-semibold">Approve this write?</h2>
      </header>
      <div className="space-y-3 px-5 py-4 text-sm">
        <p>
          The worker wants to run <code className="rounded bg-mist px-1.5 py-0.5 font-mono text-[12px]">{pending.tool}</code>, which changes the invoice database. Nothing has been written yet.
        </p>
        <dl className="divide-y divide-rule rounded-md border border-rule">
          {Object.entries(pending.args).map(([k, v]) => (
            <div key={k} className="grid grid-cols-[8rem_1fr] gap-3 px-3 py-1.5">
              <dt className="text-slate2">{k}</dt>
              <dd className="break-all font-medium">{show(v)}</dd>
            </div>
          ))}
        </dl>
        <div className="flex gap-2">
          <button className="btn-ok" onClick={() => onDecide(true)} disabled={busy}>Approve and save</button>
          <button className="btn-danger" onClick={() => onDecide(false)} disabled={busy}>Deny</button>
        </div>
        <p className="text-xs text-slate2">Approval covers exactly these values. Denying stops the task and nothing is saved.</p>
      </div>
    </section>
  );
}
