import type { Plan } from "../types";
import { Empty, Section } from "./Common";

export default function PlanPanel({ plan }: { plan: Plan | null }) {
  return (
    <Section title="Plan">
      {!plan ? (
        <Empty>The plan appears here once the worker has read the task.</Empty>
      ) : (
        <div className="space-y-3 text-sm">
          <p className="text-[15px] leading-snug">{plan.goal}</p>
          <ol className="space-y-1.5">
            {plan.steps.map((s, i) => (
              <li key={i} className="flex gap-3">
                <span className="mt-0.5 w-5 shrink-0 text-right text-slate2">{i + 1}.</span>
                <span>
                  {s.description}
                  {s.tool && <code className="ml-2 rounded bg-mist px-1.5 py-0.5 font-mono text-[12px] text-signal">{s.tool}</code>}
                </span>
              </li>
            ))}
          </ol>
          <p className="text-slate2">
            <span className="font-medium text-ink">Success means:</span> {plan.expected_outcome}
            {plan.requires_write && " This task writes to the database, so it needs your approval first."}
          </p>
        </div>
      )}
    </Section>
  );
}
