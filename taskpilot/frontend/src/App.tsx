import { useEffect, useState } from "react";
import Header, { type Tab } from "./components/Header";
import Chat from "./pages/Chat";
import Console from "./pages/Console";
import Documents from "./pages/Documents";
import { api } from "./services/api";
import type { Health } from "./types";

export default function App() {
  const [tab, setTab] = useState<Tab>("tasks");
  const [health, setHealth] = useState<Health | null>(null);
  const [offline, setOffline] = useState(false);
  const [openTask, setOpenTask] = useState<{ id: string; n: number } | undefined>();
  const [prefill, setPrefill] = useState<{ text: string; n: number } | undefined>();

  useEffect(() => {
    api.health().then((h) => { setHealth(h); setOffline(false); }).catch(() => setOffline(true));
  }, []);

  // The Console stays mounted (just hidden) so a running task keeps polling while you chat.
  return (
    <div className="min-h-screen">
      <Header health={health} offline={offline} tab={tab} onTab={setTab} />
      <div hidden={tab !== "tasks"}><Console offline={offline} openTaskId={openTask} prefill={prefill} /></div>
      {tab === "chat" && <Chat offline={offline} onOpenTask={(id) => { setOpenTask({ id, n: Date.now() }); setTab("tasks"); }} />}
      {tab === "docs" && <Documents offline={offline} onAskAsTask={(text) => { setPrefill({ text, n: Date.now() }); setTab("tasks"); }} />}
    </div>
  );
}
