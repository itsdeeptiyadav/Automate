import { BookOpen, Loader2, Search, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Empty, Section } from "../components/Common";
import { formatBytes } from "../lib";
import { api, errorMessage } from "../services/api";
import type { DocumentInfo, SearchHit } from "../types";

interface Props { offline: boolean; onAskAsTask: (text: string) => void }

export default function Documents({ offline, onAskAsTask }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);

  const reload = useCallback(() => api.listDocuments().then(setDocs).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { reload(); }, [reload]);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true); setError(null); setNotice(null);
    const problems: string[] = []; let added = 0;
    for (const f of Array.from(files)) {
      try { await api.uploadDocument(f); added++; } catch (e) { problems.push(`${f.name}: ${errorMessage(e)}`); }
    }
    if (added) setNotice(`Added ${added} document${added > 1 ? "s" : ""}.`);
    if (problems.length) setError(problems.join(" "));
    setBusy(false);
    if (input.current) input.current.value = "";
    reload();
  }

  async function search() {
    if (query.trim().length < 2) return;
    setError(null);
    try { setHits(await api.searchDocuments(query.trim())); } catch (e) { setError(errorMessage(e)); }
  }

  async function remove(d: DocumentInfo) {
    try { await api.deleteDocument(d.id); setHits(null); reload(); } catch (e) { setError(errorMessage(e)); }
  }

  return (
    <main className="mx-auto grid max-w-7xl gap-5 px-4 pb-16 pt-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
      <div className="min-w-0 space-y-5">
        <Section title="Library" aside={<span className="text-xs text-slate2">{docs.length} document{docs.length === 1 ? "" : "s"}</span>}>
          {docs.length === 0 ? <Empty>No documents yet. Upload PDF, Word, text, Markdown or CSV files; the chat and document Q&amp;A tasks read from here.</Empty> : (
            <ul className="divide-y divide-rule">
              {docs.map((d) => (
                <li key={d.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{d.filename}</div>
                    <div className="text-xs text-slate2">#{d.id} · {d.kind.toUpperCase()} · {formatBytes(d.size_bytes)} · {d.char_count.toLocaleString()} characters · {d.chunk_count} passages{d.page_count ? ` · ${d.page_count} pages` : ""}</div>
                  </div>
                  <button aria-label={`Delete ${d.filename}`} className="btn-danger !px-2 !py-1" onClick={() => remove(d)}><Trash2 size={14} aria-hidden /></button>
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Section title="Search your documents">
          <div className="flex gap-2">
            <input aria-label="Search query" value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()}
              placeholder="e.g. refund within 30 days" className="min-w-0 flex-1 rounded-md border border-rule px-3 py-2 text-sm focus:border-signal" />
            <button className="btn-quiet" disabled={offline || query.trim().length < 2} onClick={search}><Search size={16} aria-hidden /> Search</button>
            <button className="btn-primary" disabled={offline || query.trim().length < 2} onClick={() => onAskAsTask(`According to my uploaded documents, ${query.trim()}`)}>
              <BookOpen size={16} aria-hidden /> Ask as task
            </button>
          </div>
          <p className="mt-2 text-xs text-slate2">“Ask as task” runs a verified Q&amp;A: every citation must be a verbatim quote from a document the worker actually read.</p>
          {hits && (hits.length === 0 ? <p className="mt-3 text-sm text-slate2">No passages match.</p> : (
            <ul className="mt-3 space-y-2">
              {hits.map((h, i) => (
                <li key={i} className="rounded-md border border-rule bg-mist/60 px-3 py-2 text-sm">
                  <div className="font-medium">{h.filename} <span className="text-xs font-normal text-slate2">passage {h.chunk + 1} · score {h.score}</span></div>
                  <div className="text-slate2">{h.snippet}</div>
                </li>
              ))}
            </ul>
          ))}
        </Section>
      </div>
      <aside className="min-w-0 space-y-5">
        <Section title="Add documents">
          <p className="mb-3 text-sm text-slate2">PDF, DOCX, TXT, MD or CSV, up to 5 MB each. Scanned or image-only files are rejected (no OCR).</p>
          <input ref={input} type="file" multiple className="sr-only" aria-label="Choose documents"
            accept=".pdf,.docx,.txt,.md,.csv" onChange={(e) => upload(e.target.files)} />
          <button className="btn-quiet" disabled={busy || offline} onClick={() => input.current?.click()}>
            {busy ? <Loader2 size={16} className="animate-spin" aria-hidden /> : <Upload size={16} aria-hidden />}
            {busy ? "Uploading..." : "Choose files"}
          </button>
          {notice && <p className="mt-3 text-sm font-medium text-ok">{notice}</p>}
          {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
        </Section>
      </aside>
    </main>
  );
}
