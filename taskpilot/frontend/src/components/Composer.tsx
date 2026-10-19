import { BookOpen, FileSearch, Play, Receipt } from "lucide-react";
import { useEffect, useState } from "react";
import { EXAMPLE_DOC_TASK, EXAMPLE_FILE_TASK, EXAMPLE_INVOICE_TASK } from "../lib";

interface Props { onRun: (text: string) => void; disabled: boolean; prefill?: { text: string; n: number } }

export default function Composer({ onRun, disabled, prefill }: Props) {
  const [text, setText] = useState("");
  useEffect(() => { if (prefill) setText(prefill.text); }, [prefill]);
  return (
    <div className="rounded-xl border border-rule bg-white p-5">
      <label htmlFor="task-input" className="mb-2 block text-[15px] font-semibold tracking-tight">
        What should the worker do?
      </label>
      <textarea
        id="task-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        placeholder="Describe a task in plain English"
        className="w-full resize-y rounded-md border border-rule bg-mist/60 px-3 py-2 text-sm leading-relaxed placeholder:text-slate2/70 focus:border-signal focus:bg-white"
      />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button className="btn-primary" disabled={disabled || text.trim().length < 3} onClick={() => onRun(text.trim())}>
          <Play size={16} aria-hidden /> Run task
        </button>
        <button className="btn-quiet" disabled={disabled} onClick={() => setText(EXAMPLE_INVOICE_TASK)}>
          <Receipt size={16} aria-hidden /> Example: invoice
        </button>
        <button className="btn-quiet" disabled={disabled} onClick={() => setText(EXAMPLE_FILE_TASK)}>
          <FileSearch size={16} aria-hidden /> Example: file search
        </button>
        <button className="btn-quiet" disabled={disabled} onClick={() => setText(EXAMPLE_DOC_TASK)}>
          <BookOpen size={16} aria-hidden /> Example: document Q&amp;A
        </button>
      </div>
    </div>
  );
}
