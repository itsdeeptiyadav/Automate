import { Loader2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { api, errorMessage } from "../services/api";
import type { UploadResult } from "../types";
import { Section } from "./Common";

interface Props { onUploaded: () => void; onUse: (taskText: string) => void; disabled: boolean }

export default function UploadCard({ onUploaded, onUse, disabled }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);

  async function pick(file: File | undefined) {
    if (!file) return;
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await api.uploadInvoice(file));
      onUploaded();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <Section title="Upload an invoice PDF">
      <p className="mb-3 text-sm text-slate2">
        Add your own invoice so the worker can find it. The PDF needs text lines such as Vendor, Invoice Number, Invoice Date, Due Date, Total Due and Currency.
      </p>
      <input ref={input} type="file" accept="application/pdf,.pdf" className="sr-only" aria-label="Choose invoice PDF"
        onChange={(e) => pick(e.target.files?.[0])} />
      <button className="btn-quiet" disabled={busy || disabled} onClick={() => input.current?.click()}>
        {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Upload size={16} aria-hidden />}
        {busy ? "Uploading..." : "Choose PDF"}
      </button>
      {disabled && <p className="mt-2 text-xs text-slate2">Uploads are paused while a task is running.</p>}
      {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
      {result && (
        <div className="mt-3 space-y-2 text-sm">
          <p className="font-medium text-ok">Saved as {result.file}</p>
          <p className="text-slate2">{result.company}, {result.invoice_number}, {result.currency} {result.amount}, due {result.due_date}</p>
          {result.warnings.map((w) => <p key={w} className="text-warn">{w}</p>)}
          <button className="btn-primary" disabled={disabled} onClick={() => onUse(result.suggested_task)}>Use in a task</button>
        </div>
      )}
    </Section>
  );
}
