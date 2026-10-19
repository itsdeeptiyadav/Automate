import { ExternalLink, Loader2, Plus, Send, Trash2, Wrench } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { CHAT_SUGGESTIONS } from "../lib";
import { api, errorMessage } from "../services/api";
import type { ChatMessage, Conversation } from "../types";
import { Section } from "../components/Common";

interface Props { offline: boolean; onOpenTask: (id: string) => void }

function Bubble({ m, onOpenTask }: { m: ChatMessage; onOpenTask: (id: string) => void }) {
  const mine = m.role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] rounded-xl px-4 py-2.5 text-sm leading-relaxed ${mine ? "bg-signal text-white" : "border border-rule bg-white"}`}>
        <p className="whitespace-pre-wrap break-words">{m.content}</p>
        {!mine && m.trace.length > 0 && (
          <details className="mt-2 text-xs text-slate2">
            <summary className="cursor-pointer select-none"><Wrench size={12} className="mr-1 inline" aria-hidden />Used {m.trace.length} tool{m.trace.length > 1 ? "s" : ""}</summary>
            <ul className="mt-1 space-y-1">
              {m.trace.map((t, i) => (
                <li key={i} className="rounded border border-rule bg-mist/60 px-2 py-1">
                  <span className="font-mono font-semibold text-ink">{t.tool}</span>{" "}
                  <span className={t.ok ? "text-ok" : "text-bad"}>{t.ok ? "ok" : "failed"}</span>
                  <div>{t.summary}</div>
                </li>
              ))}
            </ul>
          </details>
        )}
        {!mine && m.trace.filter((t) => t.task_id).map((t) => (
          <button key={t.task_id} className="btn-quiet mt-2 !px-3 !py-1 text-xs" onClick={() => onOpenTask(t.task_id!)}>
            <ExternalLink size={12} aria-hidden /> Open task in the Tasks tab
          </button>
        ))}
      </div>
    </div>
  );
}

export default function Chat({ offline, onOpenTask }: Props) {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const reloadList = useCallback(() => api.listConversations().then(setConvs).catch(() => undefined), []);
  useEffect(() => { reloadList(); }, [reloadList]);
  useEffect(() => { bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" }); }, [messages, sending]);

  async function open(id: string) {
    setError(null); setActiveId(id);
    try { setMessages((await api.getConversation(id)).messages); } catch (e) { setError(errorMessage(e)); }
  }
  function fresh() { setActiveId(null); setMessages([]); setError(null); }

  async function remove(id: string) {
    try { await api.deleteConversation(id); if (id === activeId) fresh(); reloadList(); } catch (e) { setError(errorMessage(e)); }
  }

  async function send(raw: string) {
    const body = raw.trim();
    if (!body || sending) return;
    setSending(true); setError(null);
    const optimistic: ChatMessage = { id: -1, seq: -1, role: "user", content: body, trace: [], created_at: new Date().toISOString() };
    setMessages((prev) => [...prev, optimistic]); setText("");
    try {
      let id = activeId;
      if (!id) { id = (await api.createConversation()).id; setActiveId(id); }
      const reply = await api.sendMessage(id, body);
      setMessages((prev) => [...prev.filter((m) => m.id !== -1), reply.user, reply.assistant]);
      reloadList();
    } catch (e) {
      setMessages((prev) => prev.filter((m) => m.id !== -1)); setText(body); setError(errorMessage(e)); // nothing was saved: let the user retry
    } finally { setSending(false); }
  }

  return (
    <main className="mx-auto grid max-w-7xl gap-5 px-4 pb-16 pt-5 sm:px-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="space-y-3">
        <button className="btn-primary w-full" onClick={fresh}><Plus size={16} aria-hidden /> New chat</button>
        <ul className="space-y-1" aria-label="Conversations">
          {convs.map((c) => (
            <li key={c.id} className={`flex items-center gap-1 rounded-md border px-2 py-1.5 text-sm ${c.id === activeId ? "border-signal/40 bg-white" : "border-transparent hover:bg-white"}`}>
              <button className="min-w-0 flex-1 truncate text-left" onClick={() => open(c.id)}>{c.title}</button>
              <button aria-label={`Delete ${c.title}`} className="text-slate2 hover:text-bad" onClick={() => remove(c.id)}><Trash2 size={14} aria-hidden /></button>
            </li>
          ))}
          {convs.length === 0 && <li className="text-sm text-slate2">No conversations yet.</li>}
        </ul>
      </aside>
      <Section title="Chat" className="flex min-h-[28rem] flex-col">
        <div className="mb-3 max-h-[60vh] min-h-[16rem] flex-1 space-y-3 overflow-y-auto pr-1" aria-live="polite">
          {messages.length === 0 && (
            <div className="space-y-3 text-sm text-slate2">
              <p>Ask about your documents and invoices, or tell me to start a task. I can read, search and report; saving anything still needs your approval in the Tasks tab.</p>
              <div className="flex flex-wrap gap-2">
                {CHAT_SUGGESTIONS.map((s) => <button key={s} className="btn-quiet !px-3 !py-1 text-xs" disabled={offline || sending} onClick={() => send(s)}>{s}</button>)}
              </div>
            </div>
          )}
          {messages.map((m, i) => <Bubble key={`${m.id}-${i}`} m={m} onOpenTask={onOpenTask} />)}
          {sending && <p className="text-sm text-slate2"><Loader2 size={14} className="mr-1 inline animate-spin" aria-hidden />Thinking...</p>}
          <div ref={bottom} />
        </div>
        {error && <p role="alert" className="mb-2 rounded-md border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">{error}</p>}
        <div className="flex items-end gap-2">
          <textarea aria-label="Message" rows={2} value={text} disabled={offline}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
            placeholder="Message TaskPilot (Enter to send, Shift+Enter for a new line)"
            className="min-w-0 flex-1 resize-y rounded-md border border-rule bg-mist/60 px-3 py-2 text-sm focus:border-signal focus:bg-white" />
          <button className="btn-primary" disabled={offline || sending || !text.trim()} onClick={() => send(text)}>
            <Send size={16} aria-hidden /> Send
          </button>
        </div>
      </Section>
    </main>
  );
}
