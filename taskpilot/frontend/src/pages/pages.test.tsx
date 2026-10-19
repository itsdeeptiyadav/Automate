import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Chat from "./Chat";
import Documents from "./Documents";
import type { ChatMessage, DocumentInfo } from "../types";

const a = vi.hoisted(() => ({
  listConversations: vi.fn(), createConversation: vi.fn(), getConversation: vi.fn(), deleteConversation: vi.fn(), sendMessage: vi.fn(),
  listDocuments: vi.fn(), uploadDocument: vi.fn(), deleteDocument: vi.fn(), searchDocuments: vi.fn(),
}));
vi.mock("../services/api", async (orig) => {
  const m = await orig<typeof import("../services/api")>();
  return { ...m, api: { ...m.api, ...a } };
});

const msg = (over: Partial<ChatMessage>): ChatMessage => ({ id: 1, seq: 1, role: "user", content: "hi", trace: [], created_at: "2026-10-18T08:00:00Z", ...over });
const doc: DocumentInfo = { id: 7, filename: "policy.txt", kind: "txt", size_bytes: 2048, page_count: null, char_count: 900, chunk_count: 2, created_at: "2026-10-18T08:00:00Z" };

beforeEach(() => { Object.values(a).forEach((f) => f.mockReset()); a.listConversations.mockResolvedValue([]); a.listDocuments.mockResolvedValue([]); });

test("chat sends a message, shows the reply, the tools used and a link to the started task", async () => {
  a.createConversation.mockResolvedValue({ id: "c1", title: "New chat", created_at: "", updated_at: "" });
  a.sendMessage.mockResolvedValue({
    user: msg({ content: "process the latest invoice from Acme Corporation" }),
    assistant: msg({ id: 2, seq: 2, role: "assistant", content: "I started task t-99.", trace: [{ tool: "start_task", args: {}, ok: true, summary: "Started task t-99", task_id: "t-99" }] }),
    started_task_ids: ["t-99"],
  });
  const onOpenTask = vi.fn();
  render(<Chat offline={false} onOpenTask={onOpenTask} />);
  await userEvent.type(screen.getByLabelText("Message"), "process the latest invoice from Acme Corporation{Enter}");
  expect(await screen.findByText("I started task t-99.")).toBeInTheDocument();
  expect(a.sendMessage).toHaveBeenCalledWith("c1", "process the latest invoice from Acme Corporation");
  expect(screen.getByText(/Used 1 tool/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /open task in the tasks tab/i }));
  expect(onOpenTask).toHaveBeenCalledWith("t-99");
});

test("a failed chat turn shows the error and keeps the text so it can be retried", async () => {
  a.createConversation.mockResolvedValue({ id: "c1", title: "New chat", created_at: "", updated_at: "" });
  a.sendMessage.mockRejectedValue(new Error("Cannot reach Ollama Cloud"));
  render(<Chat offline={false} onOpenTask={() => undefined} />);
  await userEvent.type(screen.getByLabelText("Message"), "hello");
  await userEvent.click(screen.getByRole("button", { name: /send/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach Ollama Cloud");
  expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe("hello");
});

test("suggestion chips send a message and past conversations can be opened", async () => {
  a.listConversations.mockResolvedValue([{ id: "c9", title: "Old chat", created_at: "", updated_at: "" }]);
  a.getConversation.mockResolvedValue({ id: "c9", title: "Old chat", created_at: "", updated_at: "", messages: [msg({ content: "earlier question" })] });
  render(<Chat offline={false} onOpenTask={() => undefined} />);
  await userEvent.click(await screen.findByRole("button", { name: "Old chat" }));
  expect(await screen.findByText("earlier question")).toBeInTheDocument();
});

test("documents: uploads several files, reports per-file errors, and lists the library", async () => {
  a.uploadDocument.mockResolvedValueOnce(doc).mockRejectedValueOnce(new Error("This file is already in the library"));
  a.listDocuments.mockResolvedValueOnce([]).mockResolvedValue([doc]);
  render(<Documents offline={false} onAskAsTask={() => undefined} />);
  await userEvent.upload(screen.getByLabelText("Choose documents"), [new File(["a"], "a.txt"), new File(["b"], "b.txt")]);
  expect(await screen.findByText("Added 1 document.")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("b.txt: This file is already in the library");
  expect(await screen.findByText("policy.txt")).toBeInTheDocument();
});

test("documents: search shows passages and 'Ask as task' hands a Q&A task to the Tasks tab", async () => {
  a.listDocuments.mockResolvedValue([doc]);
  a.searchDocuments.mockResolvedValue([{ document_id: 7, filename: "policy.txt", chunk: 0, start_char: 0, score: 3.2, snippet: "returns within 30 days" }]);
  const onAsk = vi.fn();
  render(<Documents offline={false} onAskAsTask={onAsk} />);
  await userEvent.type(screen.getByLabelText("Search query"), "refund window");
  await userEvent.click(screen.getByRole("button", { name: /^search$/i }));
  expect(await screen.findByText("returns within 30 days")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /ask as task/i }));
  expect(onAsk).toHaveBeenCalledWith("According to my uploaded documents, refund window");
});

test("documents: deleting asks the API and refreshes", async () => {
  a.listDocuments.mockResolvedValueOnce([doc]).mockResolvedValue([]);
  a.deleteDocument.mockResolvedValue(undefined);
  render(<Documents offline={false} onAskAsTask={() => undefined} />);
  await userEvent.click(await screen.findByRole("button", { name: /delete policy.txt/i }));
  await waitFor(() => expect(a.deleteDocument).toHaveBeenCalledWith(7));
  expect(await screen.findByText(/No documents yet/)).toBeInTheDocument();
});
