"""The real LangChain path (ChatOllama / ChatOpenAI), exercised against a fake HTTP server.

No network: httpx.MockTransport stands in for Ollama / the OpenAI API, so these tests check exactly what is
sent (URL, auth header, JSON mode, options), how errors are classified, and that a full task can run through
prompt -> chat model -> HTTP -> parser -> LangGraph.
"""
import json

import httpx
import pytest

from app.agent import prompts
from app.agent.demo import DemoClient
from app.agent.llm import LLMUnavailable, build_llm
from app.config import Settings
from app.schemas.agent import DecisionContext
from tests.conftest import FILE_TASK, INVOICE_TASK, events, run_task


def cfg(**kw) -> Settings:
    base = dict(llm_provider="ollama", ollama_base_url="https://ollama.com", ollama_model="gemma4:31b",
                ollama_api_key="key-123", ollama_num_ctx=8192, llm_timeout_seconds=5, llm_max_repairs=0, _env_file=None)
    base.update(kw)
    return Settings(**base)


def ollama_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"model": "m", "created_at": "2026-01-01T00:00:00Z", "done": True,
                                     "message": {"role": "assistant", "content": content}})


def capture(response: httpx.Response):
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), auth=req.headers.get("authorization"), body=json.loads(req.content))
        return response

    return seen, httpx.MockTransport(handler)


def test_cloud_sends_bearer_key_json_mode_and_no_num_ctx():
    seen, transport = capture(ollama_reply('{"task_type": "unsupported", "goal": "x"}'))
    llm = build_llm(cfg(), transport=transport)
    assert llm.provider == "ollama-cloud"
    llm.plan("hello", [])
    assert seen["url"] == "https://ollama.com/api/chat"
    assert seen["auth"] == "Bearer key-123"
    assert seen["body"]["model"] == "gemma4:31b" and seen["body"]["format"] == "json"
    assert "num_ctx" not in seen["body"]["options"]
    assert seen["body"]["messages"][0]["role"] == "system" and "planning module" in seen["body"]["messages"][0]["content"]


def test_local_ollama_sends_no_auth_and_sets_num_ctx():
    seen, transport = capture(ollama_reply('{"task_type": "unsupported", "goal": "x"}'))
    llm = build_llm(cfg(ollama_base_url="http://127.0.0.1:11434", ollama_model="mistral", ollama_api_key=""), transport=transport)
    assert llm.provider == "ollama"
    llm.plan("hello", [])
    assert seen["auth"] is None and seen["body"]["options"]["num_ctx"] == 8192


def test_cloud_without_key_fails_before_any_request():
    seen, transport = capture(ollama_reply("{}"))
    llm = build_llm(cfg(ollama_api_key=""), transport=transport)
    with pytest.raises(LLMUnavailable, match="OLLAMA_API_KEY"):
        llm.plan("hello", [])
    assert seen == {} and llm.health()["ok"] is False


@pytest.mark.parametrize("status,needle", [(401, "rejected the API key"), (404, "gemma4:cloud"), (429, "limit"), (500, "HTTP 500")])
def test_http_errors_are_classified(status, needle):
    _, transport = capture(httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(LLMUnavailable, match=needle):
        build_llm(cfg(), transport=transport).plan("hello", [])


def test_connection_failure_and_timeout_messages():
    def refuse(req):
        raise httpx.ConnectError("refused")

    def slow(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LLMUnavailable, match="Cannot reach Ollama Cloud"):
        build_llm(cfg(), transport=httpx.MockTransport(refuse)).plan("hello", [])
    with pytest.raises(LLMUnavailable, match="timed out"):
        build_llm(cfg(), transport=httpx.MockTransport(slow)).plan("hello", [])


def test_openai_provider_uses_json_mode_and_classifies_errors():
    seen, transport = capture(httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 1, "model": "gpt-4o-mini",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"task_type": "unsupported", "goal": "x"}'}}]}))
    llm = build_llm(Settings(llm_provider="openai", openai_api_key="sk-test", openai_base_url="https://api.test/v1",
                             llm_max_repairs=0, _env_file=None), transport=transport)
    assert llm.plan("hello", []).task_type == "unsupported"
    assert seen["url"] == "https://api.test/v1/chat/completions" and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    _, bad = capture(httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(LLMUnavailable, match="rejected the API key"):
        build_llm(Settings(llm_provider="openai", openai_api_key="sk-test", llm_max_repairs=0, _env_file=None), transport=bad).plan("hi", [])


def test_invalid_json_is_repaired_once_then_accepted():
    replies = iter(["not json", '```json\n{"task_type": "unsupported", "goal": "ok"}\n```'])
    seen_prompts = []

    def handler(req):
        seen_prompts.append(json.loads(req.content)["messages"][-1]["content"])
        return ollama_reply(next(replies))

    llm = build_llm(cfg(llm_max_repairs=1), transport=httpx.MockTransport(handler))
    assert llm.plan("hello", []).goal == "ok"
    assert len(seen_prompts) == 2 and "previous reply was invalid" in seen_prompts[1]


def test_health_reports_ok_and_missing_model(monkeypatch):
    from app.agent import llm as llm_mod

    def fake_get(models):
        return lambda url, headers=None, timeout=None: httpx.Response(200, json={"models": [{"name": n} for n in models]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(llm_mod.httpx, "get", fake_get(["gemma4:31b"]))
    assert build_llm(cfg()).health()["ok"] is True
    monkeypatch.setattr(llm_mod.httpx, "get", fake_get(["other:7b"]))
    h = build_llm(cfg()).health()
    assert h["ok"] is False and "gemma4:31b" in h["detail"]


# ---- a whole task through the real LangChain chain, with a scripted "model" behind the HTTP layer ----
def scripted_ollama():
    """Fake Ollama whose replies come from the demo rules, so the model's JSON goes through the real parser."""
    demo, calls = DemoClient(), []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        system, user = body["messages"][0]["content"], json.loads(body["messages"][-1]["content"])
        if system == prompts.PLAN_SYSTEM:
            calls.append("plan")
            return ollama_reply(demo.plan(user["task"], user["tools"]).model_dump_json())
        calls.append("decide")
        ctx = DecisionContext(task_text=user["task"], plan=user["plan"], facts=user["facts"], history=user["history"],
                              tools=user["tools"], steps_used=user["steps_used"], max_steps=user["max_steps"])
        return ollama_reply(demo.decide(ctx).model_dump_json())

    return httpx.MockTransport(handler), calls


def test_invoice_task_end_to_end_through_chat_ollama(make_client):
    transport, calls = scripted_ollama()
    client, app = make_client(llm=build_llm(cfg(), transport=transport))
    d = run_task(client, INVOICE_TASK)
    assert d["status"] == "COMPLETED", d["error"]
    assert d["llm_provider"] == "ollama-cloud" and calls[0] == "plan" and calls.count("decide") >= 5
    assert any(e["type"] == "approval_requested" for e in events(client, d["id"]))


def test_file_search_task_through_chat_ollama(make_client):
    transport, _ = scripted_ollama()
    client, _ = make_client(llm=build_llm(cfg(), transport=transport))
    d = run_task(client, FILE_TASK)
    assert d["status"] == "COMPLETED", d["error"]
