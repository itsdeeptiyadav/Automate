import {
  AlertTriangle, BadgeCheck, Ban, Brain, CircleDot, Flag, Hourglass, ListChecks, MessageCircleQuestion,
  RefreshCw, ShieldAlert, ShieldCheck, Wrench, XCircle, CheckCircle2, LifeBuoy,
} from "lucide-react";
import type { ReactNode } from "react";
import { clock } from "../lib";
import type { TaskEvent } from "../types";
import { Empty, JsonBlock, Section } from "./Common";

interface Style { icon: ReactNode; node: string; text?: string }
const S = (icon: ReactNode, node: string, text?: string): Style => ({ icon, node, text });

const STYLES: Record<string, Style> = {
  plan: S(<ListChecks size={14} />, "bg-signal text-white"),
  thought: S(<Brain size={14} />, "bg-white text-slate2 border border-rule", "text-slate2"),
  tool_call: S(<Wrench size={14} />, "bg-ink text-white"),
  tool_result: S(<CheckCircle2 size={14} />, "bg-ok text-white"),
  tool_error: S(<XCircle size={14} />, "bg-bad text-white", "text-bad"),
  retry: S(<RefreshCw size={14} />, "bg-warn text-white", "text-warn"),
  recovery: S(<LifeBuoy size={14} />, "bg-warn text-white", "text-warn"),
  approval_requested: S(<Hourglass size={14} />, "bg-warn text-white", "text-warn"),
  approval_granted: S(<ShieldCheck size={14} />, "bg-ok text-white"),
  approval_denied: S(<Ban size={14} />, "bg-bad text-white", "text-bad"),
  verification: S(<BadgeCheck size={14} />, "bg-signal text-white"),
  security: S(<ShieldAlert size={14} />, "bg-warn text-white", "text-warn"),
  needs_input: S(<MessageCircleQuestion size={14} />, "bg-warn text-white", "text-warn"),
  cancel: S(<Ban size={14} />, "bg-slate2 text-white"),
  final: S(<Flag size={14} />, "bg-ok text-white", "font-semibold"),
  error: S(<AlertTriangle size={14} />, "bg-bad text-white", "text-bad font-semibold"),
};

function detailFor(e: TaskEvent): unknown | null {
  const p = e.payload || {};
  if (e.type === "tool_call") return p.args;
  if (e.type === "tool_result") return p.data;
  if (e.type === "tool_error") return { error_type: p.error_type, error: p.error, ...(p.data && Object.keys(p.data).length ? { data: p.data } : {}), ...(p.requested_tool ? { requested_tool: p.requested_tool } : {}) };
  if (e.type === "verification") return p.checks;
  if (e.type === "plan") return null;
  if (e.type === "approval_requested" || e.type === "approval_granted") return p.args ?? null;
  return null;
}

function Row({ e, last }: { e: TaskEvent; last: boolean }) {
  if (e.type === "state") {
    return (
      <li className="relative pb-3 pl-10">
        {!last && <span className="absolute left-[13px] top-4 h-full w-px bg-rule" aria-hidden />}
        <span className="absolute left-[9px] top-1.5 text-slate2"><CircleDot size={9} /></span>
        <span className="text-xs text-slate2">{e.title}</span>
        <span className="ml-2 text-xs text-slate2/70">{clock(e.ts)}</span>
      </li>
    );
  }
  const st = STYLES[e.type] ?? STYLES.thought;
  const detail = detailFor(e);
  return (
    <li className="relative pb-4 pl-10" data-event-type={e.type}>
      {!last && <span className="absolute left-[13px] top-7 h-full w-px bg-rule" aria-hidden />}
      <span className={`absolute left-0 top-0 flex h-7 w-7 items-center justify-center rounded-full ${st.node}`}>{st.icon}</span>
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className={`text-sm leading-snug ${st.text ?? ""}`}>{e.title}</span>
        {e.tool && e.type !== "tool_call" && <code className="rounded bg-mist px-1.5 py-0.5 font-mono text-[11px] text-slate2">{e.tool}</code>}
        {e.step !== null && <span className="text-xs text-slate2">step {e.step}</span>}
        <span className="text-xs text-slate2/70">{clock(e.ts)}</span>
      </div>
      {detail !== null && detail !== undefined && (
        <details className="mt-1">
          <summary className="cursor-pointer text-xs font-medium text-signal">Details</summary>
          <JsonBlock value={detail} />
        </details>
      )}
    </li>
  );
}

export default function Timeline({ events }: { events: TaskEvent[] }) {
  return (
    <Section title="Execution trace" aside={<span className="text-xs text-slate2">{events.length} events, live from the backend</span>}>
      {events.length === 0 ? (
        <Empty>Nothing has run yet. Each action, observation, retry and check will be listed here as it happens.</Empty>
      ) : (
        <ol>{events.map((e, i) => <Row key={e.id} e={e} last={i === events.length - 1} />)}</ol>
      )}
    </Section>
  );
}
