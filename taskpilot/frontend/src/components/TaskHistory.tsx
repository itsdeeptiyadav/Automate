import type { TaskSummary } from "../types";
import { Empty, Section, StatusChip } from "./Common";

interface Props { tasks: TaskSummary[]; selectedId?: string; onSelect: (id: string) => void }

export default function TaskHistory({ tasks, selectedId, onSelect }: Props) {
  return (
    <Section title="Recent tasks">
      {tasks.length === 0 ? (
        <Empty>Tasks you run are kept here.</Empty>
      ) : (
        <ul className="-my-1 divide-y divide-rule">
          {tasks.slice(0, 8).map((t) => (
            <li key={t.id}>
              <button onClick={() => onSelect(t.id)} className={`w-full py-2 text-left hover:bg-mist/70 ${t.id === selectedId ? "bg-mist" : ""}`}>
                <span className="line-clamp-2 text-sm">{t.text}</span>
                <span className="mt-1 flex items-center gap-2"><StatusChip status={t.status} /><span className="text-xs text-slate2">{new Date(t.created_at).toLocaleString()}</span></span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
