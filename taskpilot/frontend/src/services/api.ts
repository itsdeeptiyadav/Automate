import axios from "axios";
import type { ChatReply, Conversation, ConversationDetail, DocumentInfo, Health, Invoice, SearchHit, TaskDetail, TaskEvent, TaskSummary, UploadResult } from "../types";

const http = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || "/api/v1", timeout: 20000 });

export function errorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    if (e.response) return (e.response.data?.detail as string) || `Request failed (HTTP ${e.response.status})`;
    return "Cannot reach the backend. Is it running on http://127.0.0.1:8000?";
  }
  return e instanceof Error ? e.message : String(e);
}

export const api = {
  health: () => http.get<Health>("/health").then((r) => r.data),
  createTask: (text: string) => http.post<TaskDetail>("/tasks", { text }).then((r) => r.data),
  startTask: (id: string) => http.post<TaskDetail>(`/tasks/${id}/start`).then((r) => r.data),
  getTask: (id: string) => http.get<TaskDetail>(`/tasks/${id}`).then((r) => r.data),
  listTasks: () => http.get<TaskSummary[]>("/tasks").then((r) => r.data),
  getEvents: (id: string, after = 0) => http.get<TaskEvent[]>(`/tasks/${id}/events`, { params: { after } }).then((r) => r.data),
  approve: (id: string, approved: boolean) => http.post<TaskDetail>(`/tasks/${id}/approve`, { approved }).then((r) => r.data),
  cancel: (id: string) => http.post<TaskDetail>(`/tasks/${id}/cancel`).then((r) => r.data),
  sendInput: (id: string, message: string) => http.post<TaskDetail>(`/tasks/${id}/input`, { message }).then((r) => r.data),
  uploadInvoice: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.post<UploadResult>("/invoices/upload", form).then((r) => r.data);
  },
  listDocuments: () => http.get<DocumentInfo[]>("/documents").then((r) => r.data),
  uploadDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.post<DocumentInfo>("/documents", form, { timeout: 120000 }).then((r) => r.data);
  },
  deleteDocument: (id: number) => http.delete(`/documents/${id}`).then(() => undefined),
  searchDocuments: (q: string) => http.get<SearchHit[]>("/documents/search", { params: { q } }).then((r) => r.data),
  listConversations: () => http.get<Conversation[]>("/conversations").then((r) => r.data),
  createConversation: () => http.post<Conversation>("/conversations", {}).then((r) => r.data),
  getConversation: (id: string) => http.get<ConversationDetail>(`/conversations/${id}`).then((r) => r.data),
  deleteConversation: (id: string) => http.delete(`/conversations/${id}`).then(() => undefined),
  sendMessage: (id: string, text: string) =>
    http.post<ChatReply>(`/conversations/${id}/messages`, { text }, { timeout: 300000 }).then((r) => r.data),
  invoices: () => http.get<Invoice[]>("/invoices").then((r) => r.data),
};
