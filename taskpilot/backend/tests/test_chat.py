"""Chat agent: LangGraph agent/tools loop, persistence, safety and provider errors."""
import json

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.agent.chat import CHAT_REGISTRY_TOOLS, ChatRun
from app.agent.demo import DemoClient
from app.agent.llm import build_llm
from app.config import Settings
from app.models.entities import Invoice, Message, Task
from tests.conftest import INVOICE_TASK, run_task
from tests.test_documents import POLICY, upload
from tests.test_langchain_ollama import cfg, ollama_reply

API = "/api/v1"


def chat(client, text, conv=None):
    if conv is None:
        conv = client.post(f"{API}/conversations", json={}).json()["id"]
    r = client.post(f"{API}/conversations/{conv}/messages", json={"text": text})
    return conv, r


def test_chat_answers_from_uploaded_documents_and_shows_the_tool_trace(client):
    upload(client, "policy.txt", POLICY.encode())
    conv, r = chat(client, "What do my documents say about refunds?")
    assert r.status_code == 200, r.text
    out = r.json()
    assert "policy.txt" in out["assistant"]["content"] and "30 days" in out["assistant"]["content"]
    assert [t["tool"] for t in out["assistant"]["trace"]] == ["search_documents"] and out["assistant"]["trace"][0]["ok"]
    detail = client.get(f"{API}/conversations/{conv}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["title"].startswith("What do my documents")  # first message becomes the title


def test_conversation_history_is_persisted_and_ordered(client):
    conv, _ = chat(client, "hello there")
    chat(client, "list my documents", conv)
    msgs = client.get(f"{API}/conversations/{conv}").json()["messages"]
    assert [m["seq"] for m in msgs] == [1, 2, 3, 4]
    assert len(client.get(f"{API}/conversations").json()) == 1
    assert client.delete(f"{API}/conversations/{conv}").status_code == 204
    assert client.get(f"{API}/conversations/{conv}").status_code == 404


def test_chat_can_start_a_task_but_cannot_approve_or_write(make_client):
    client, app = make_client()
    conv, r = chat(client, "Please process the latest invoice from Acme Corporation and save it")
    out = r.json()
    (tid,) = out["started_task_ids"]
    assert out["assistant"]["trace"][0]["task_id"] == tid and tid in out["assistant"]["content"]
    # the background run already happened (TestClient runs background tasks): it is waiting for a HUMAN
    t = client.get(f"{API}/tasks/{tid}").json()
    assert t["status"] == "AWAITING_APPROVAL"
    with app.state.session_factory() as db:
        assert db.scalars(select(Invoice)).all() == []
    _, r2 = chat(client, f"What is the status of task {tid}?", conv)
    assert "AWAITING_APPROVAL" in r2.json()["assistant"]["content"]
    client.post(f"{API}/tasks/{tid}/approve", json={"approved": True})
    assert client.get(f"{API}/tasks/{tid}").json()["status"] == "COMPLETED"


def test_chat_tools_are_read_only_plus_task_control(make_client):
    client, app = make_client()
    run = ChatRun(app.state.session_factory, app.state.settings, app.state.llm, app.state.registry)
    names = {t.name for t in run.tools()}
    assert names == set(CHAT_REGISTRY_TOOLS) | {"start_task", "get_task_status", "list_tasks"}
    assert "create_invoice_record" not in names
    assert all(app.state.registry.get(n).permission == "read" for n in CHAT_REGISTRY_TOOLS)


def test_invoice_questions_read_the_internal_system(client):
    run_task(client, INVOICE_TASK)
    _, r = chat(client, "Show my invoices")
    assert "ACM-2026-0467" in r.json()["assistant"]["content"]


def test_unmatched_question_gets_a_plain_reply_without_tools(client):
    _, r = chat(client, "good morning")
    out = r.json()["assistant"]
    assert out["trace"] == [] and "demo assistant" in out["content"]


def test_task_limit_per_message(make_client):
    client, app = make_client()
    run = ChatRun(app.state.session_factory, app.state.settings, app.state.llm, app.state.registry)
    start = next(t for t in run.tools() if t.name == "start_task")
    for _ in range(2):
        assert json.loads(start.invoke({"text": "Find files with the word 'contract'"}))["ok"]
    assert json.loads(start.invoke({"text": "Find files with the word 'contract'"}))["ok"] is False
    with app.state.session_factory() as db:
        assert len(db.scalars(select(Task)).all()) == 2


# ---- real provider path -------------------------------------------------------------
def test_native_tool_calling_through_chat_ollama(make_client):
    """Fake Ollama returns a tool call first, then a final answer after seeing the tool result."""
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if not any(m["role"] == "tool" for m in body["messages"]):
            return httpx.Response(200, json={"model": "m", "created_at": "2026-01-01T00:00:00Z", "done": True, "message": {
                "role": "assistant", "content": "", "tool_calls": [{"function": {"name": "list_documents", "arguments": {}}}]}})
        return ollama_reply("You have one document: policy.txt.")

    client, _ = make_client(llm=build_llm(cfg(), transport=httpx.MockTransport(handler)))
    upload(client, "policy.txt", POLICY.encode())
    _, r = chat(client, "what do I have?")
    out = r.json()["assistant"]
    assert out["content"] == "You have one document: policy.txt." and out["trace"][0]["tool"] == "list_documents"
    assert {t["function"]["name"] for t in seen[0]["tools"]} >= {"search_documents", "start_task"}
    tool_msg = next(m for m in seen[1]["messages"] if m["role"] == "tool")
    assert "policy.txt" in tool_msg["content"]
    assert seen[0]["messages"][0]["role"] == "system" and "UNTRUSTED" in seen[0]["messages"][0]["content"]


def test_json_tool_calling_fallback(make_client):
    replies = iter([
        json.dumps({"action": "tool", "tool": "list_documents", "args": {}}),
        json.dumps({"action": "reply", "message": "One document: policy.txt."}),
    ])
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        return ollama_reply(next(replies))

    llm = build_llm(cfg(tool_calling="json"), transport=httpx.MockTransport(handler))
    client, _ = make_client(llm=llm, tool_calling="json")
    upload(client, "policy.txt", POLICY.encode())
    _, r = chat(client, "what do I have?")
    assert r.json()["assistant"]["content"] == "One document: policy.txt."
    assert all(not b.get("tools") for b in sent) and sent[0]["format"] == "json"


def test_unknown_tool_and_bad_arguments_go_back_to_the_model(make_client):
    replies = iter([
        json.dumps({"action": "tool", "tool": "run_shell", "args": {"cmd": "rm -rf /"}}),
        json.dumps({"action": "tool", "tool": "read_document", "args": {"document_id": "abc"}}),
        json.dumps({"action": "reply", "message": "Sorry, I can't do that."}),
    ])
    llm = build_llm(cfg(tool_calling="json"), transport=httpx.MockTransport(lambda req: ollama_reply(next(replies))))
    client, _ = make_client(llm=llm, tool_calling="json")
    _, r = chat(client, "run a shell command")
    out = r.json()["assistant"]
    assert out["content"] == "Sorry, I can't do that." and [t["ok"] for t in out["trace"]] == [False, False]


def test_tool_loop_is_capped(make_client):
    def handler(req):
        body = json.loads(req.content)
        if body.get("tools"):
            return httpx.Response(200, json={"model": "m", "created_at": "2026-01-01T00:00:00Z", "done": True, "message": {
                "role": "assistant", "content": "", "tool_calls": [{"function": {"name": "list_documents", "arguments": {}}}]}})
        return ollama_reply("Here is my best answer.")  # asked without tools after the cap

    client, _ = make_client(llm=build_llm(cfg(), transport=httpx.MockTransport(handler)), chat_max_tool_rounds=3)
    _, r = chat(client, "loop forever")
    out = r.json()["assistant"]
    assert len(out["trace"]) == 3 and out["content"] == "Here is my best answer."


def test_llm_failure_is_a_503_and_nothing_is_saved(make_client):
    def refuse(req):
        raise httpx.ConnectError("refused")

    client, _ = make_client(llm=build_llm(cfg(), transport=httpx.MockTransport(refuse)))
    conv, r = chat(client, "hello")
    assert r.status_code == 503 and "Cannot reach Ollama Cloud" in r.json()["detail"]
    assert client.get(f"{API}/conversations/{conv}").json()["messages"] == []


def test_model_without_tool_support_gets_an_actionable_message(make_client):
    llm = build_llm(cfg(), transport=httpx.MockTransport(lambda req: httpx.Response(400, json={"error": "registry.ollama.ai/library/x does not support tools"})))
    client, _ = make_client(llm=llm)
    _, r = chat(client, "hello")
    assert r.status_code == 503 and "TOOL_CALLING=json" in r.json()["detail"]


def test_missing_conversation_and_empty_message(client):
    assert client.post(f"{API}/conversations/nope/messages", json={"text": "hi"}).status_code == 404
    conv = client.post(f"{API}/conversations", json={}).json()["id"]
    assert client.post(f"{API}/conversations/{conv}/messages", json={"text": ""}).status_code == 422


def test_provider_without_chat_model_reports_unavailable(make_client):
    from tests.helpers import GarbageLLM

    class NoChat(DemoClient):
        chat_model = None

    client, _ = make_client(llm=NoChat())
    conv, r = chat(client, "hi")
    assert r.status_code == 503 and "chat model" in r.json()["detail"]
    assert client.get(f"{API}/health").json()["chat_available"] is False
