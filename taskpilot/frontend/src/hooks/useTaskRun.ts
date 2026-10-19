import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../services/api";
import { POLLING, TERMINAL } from "../lib";
import type { TaskDetail, TaskEvent } from "../types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function useTaskRun(onChange?: () => void) {
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lastSeq = useRef(0);
  const currentId = useRef<string | null>(null);
  const lastStatus = useRef<string | null>(null);

  const fetchEvents = useCallback(async (id: string) => {
    const fresh = await api.getEvents(id, lastSeq.current);
    if (id !== currentId.current || !fresh.length) return;
    lastSeq.current = Math.max(lastSeq.current, fresh[fresh.length - 1].seq);
    // overlapping polls can return the same rows twice; de-duplicate by primary key
    setEvents((prev) => {
      const seen = new Set(prev.map((e) => e.id));
      return [...prev, ...fresh.filter((e) => !seen.has(e.id))];
    });
  }, []);

  const refresh = useCallback(async (id: string) => {
    const t = await api.getTask(id);
    await fetchEvents(id);
    if (!POLLING.has(t.status)) {
      await sleep(250); // events are written just after the status flips
      await fetchEvents(id);
    }
    if (id === currentId.current) setTask(t);
    if (t.status !== lastStatus.current) { lastStatus.current = t.status; onChange?.(); } // not on every poll
  }, [fetchEvents, onChange]);

  const track = useCallback((t: TaskDetail, reset: boolean) => {
    currentId.current = t.id;
    if (reset) { lastSeq.current = 0; lastStatus.current = null; setEvents([]); }
    setTask(t);
  }, []);

  useEffect(() => {
    if (!task || !POLLING.has(task.status)) return;
    const id = task.id;
    const h = setInterval(() => refresh(id).catch((e) => setError(errorMessage(e))), 800);
    return () => clearInterval(h);
  }, [task?.id, task?.status, refresh]);

  const guard = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }, []);

  const submit = (text: string) => guard(async () => {
    const created = await api.createTask(text);
    track(created, true);
    const started = await api.startTask(created.id);
    track(started, false);
    await refresh(created.id);
  });
  const act = (fn: (id: string) => Promise<unknown>) => guard(async () => {
    if (!task) return;
    await fn(task.id);
    await refresh(task.id);
  });
  const approve = (ok: boolean) => act((id) => api.approve(id, ok));
  const cancel = () => act((id) => api.cancel(id));
  const sendInput = (msg: string) => act((id) => api.sendInput(id, msg));
  const load = (id: string) => guard(async () => {
    const t = await api.getTask(id);
    track(t, true);
    await refresh(id);
  });

  const active = !!task && !TERMINAL.has(task.status);
  return { task, events, error, busy, active, submit, approve, cancel, sendInput, load, setError };
}
