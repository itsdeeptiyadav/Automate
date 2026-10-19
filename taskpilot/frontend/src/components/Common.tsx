import type { ReactNode } from "react";
import type { TaskStatus } from "../types";
import { STATUS_LABEL, statusTone } from "../lib";

const TONES = {
  ok: "bg-ok/10 text-ok border-ok/30",
  bad: "bg-bad/10 text-bad border-bad/30",
  warn: "bg-warn/10 text-warn border-warn/30",
  signal: "bg-signal/10 text-signal border-signal/30",
  muted: "bg-slate2/10 text-slate2 border-slate2/25",
};

export function StatusChip({ status }: { status: TaskStatus }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${TONES[statusTone(status)]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

export function Section({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-rule bg-white ${className}`}>
      <header className="flex items-center justify-between gap-3 border-b border-rule px-5 py-3">
        <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
        {aside}
      </header>
      <div className="px-5 py-4">{children}</div>
    </section>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-rule bg-mist p-3 font-mono text-[12px] leading-relaxed text-ink">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-slate2">{children}</p>;
}
