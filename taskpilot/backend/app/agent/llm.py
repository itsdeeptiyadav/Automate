"""LLM access through LangChain.

The agent depends only on the small `LLMClient` interface:
    plan(task_text, tools)  -> Plan
    decide(context)         -> Decision
    chat_model              -> a LangChain BaseChatModel (used by the chat agent)

Real providers are LangChain chat models (`ChatOllama`, `ChatOpenAI`). The worker asks them for ONE
JSON object per turn with an LCEL chain (prompt | model | parser) and validates it with Pydantic,
with bounded repair attempts. Failures raise LLMError subclasses; nothing falls back silently.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TypeVar
from urllib.parse import urlparse

import httpx
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from app.agent import prompts
from app.config import Settings
from app.schemas.agent import Decision, DecisionContext, Plan

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    code = "llm_error"


class LLMUnavailable(LLMError):
    code = "llm_unavailable"


class LLMOutputError(LLMError):
    code = "llm_invalid_output"


class LLMClient(ABC):
    provider: str = ""
    model: str = ""
    is_demo: bool = False
    chat_model: Optional[BaseChatModel] = None  # used by the chat agent (None = chat unavailable)

    @abstractmethod
    def plan(self, task_text: str, tools: list[dict]) -> Plan: ...

    @abstractmethod
    def decide(self, ctx: DecisionContext) -> Decision: ...

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    def map_error(self, exc: Exception) -> Optional[LLMError]:
        """Translate a provider exception into an LLMError (None = not an LLM problem)."""
        return exc if isinstance(exc, LLMError) else None


def _json_slice(text: str) -> str:
    """Models sometimes wrap JSON in prose or fences; keep the outermost {...}."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def message_text(content: Any) -> str:
    """AIMessage.content may be a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b if isinstance(b, str) else (b.get("text", "") if isinstance(b, dict) else "") for b in content)
    return str(content or "")


@dataclass
class ProviderInfo:
    provider: str
    label: str
    model: str
    base: str = ""
    timeout: float = 120.0
    api_key: str = ""
    key_env: str = ""
    is_cloud: bool = False
    json_kwargs: dict[str, Any] = field(default_factory=dict)


class ChainClient(LLMClient):
    """Planner/decider built from a LangChain chat model."""

    def __init__(self, chat_model: BaseChatModel, info: ProviderInfo, max_repairs: int = 2):
        self.chat_model = chat_model
        self.info = info
        self.provider, self.model = info.provider, info.model
        self.max_repairs = max_repairs

    # ------------------------------------------------------------------ errors
    def _require_key(self) -> None:
        if self.info.is_cloud and not self.info.api_key:
            raise LLMUnavailable(
                "OLLAMA_API_KEY is not set. Create a key at https://ollama.com/settings/keys and put it in backend/.env."
            )
        if self.info.provider == "openai" and not self.info.api_key:
            raise LLMUnavailable("OPENAI_API_KEY is not set.")

    def map_error(self, exc: Exception) -> Optional[LLMError]:
        if isinstance(exc, LLMError):
            return exc
        i, label = self.info, self.info.label
        status: Optional[int] = None
        detail = str(exc)[:200]
        try:
            import ollama

            if isinstance(exc, ollama.ResponseError):
                status, detail = exc.status_code, str(getattr(exc, "error", "") or exc)[:200]
        except ImportError:  # pragma: no cover
            pass
        try:
            import openai

            if isinstance(exc, openai.APITimeoutError):
                return LLMUnavailable(f"{label} timed out after {i.timeout}s.")
            if isinstance(exc, openai.APIConnectionError):
                return LLMUnavailable(f"Cannot reach {i.base}: {type(exc).__name__}")
            if isinstance(exc, openai.APIStatusError):
                status, detail = exc.status_code, str(exc.message)[:200]
        except ImportError:  # pragma: no cover
            pass
        if status is not None:
            if status in (401, 403):
                return LLMUnavailable(f"{label} rejected the API key (HTTP {status}). Check {i.key_env or 'the API key'}.")
            if status == 404:
                if i.provider.startswith("ollama"):
                    fix = ("Use the exact API name from https://ollama.com/api/tags (e.g. 'gemma4:31b', not 'gemma4:cloud')."
                           if i.is_cloud else f"Run: ollama pull {i.model}")
                else:
                    fix = "Check the model name and base URL."
                return LLMUnavailable(f"{label} model '{i.model}' not found. {fix}")
            if status == 429:
                return LLMUnavailable(f"{label} rate/usage limit reached (HTTP 429). Wait a bit and retry.")
            if status == 400 and "tool" in detail.lower():
                return LLMUnavailable(
                    f"{label} model '{i.model}' does not support tool calling ({detail}). "
                    "Set TOOL_CALLING=json in backend/.env or choose a model with tool support."
                )
            return LLMUnavailable(f"{label} returned HTTP {status}: {detail}")
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            hint = "Check your internet connection." if i.is_cloud else "Is it running? (try: ollama serve)"
            return LLMUnavailable(f"Cannot reach {label} at {i.base}. {hint}")
        if isinstance(exc, httpx.TimeoutException):
            return LLMUnavailable(f"{label} timed out after {i.timeout}s.")
        if isinstance(exc, httpx.HTTPError):
            return LLMUnavailable(f"{label} request failed: {exc}")
        return None

    # ------------------------------------------------------------------ structured JSON turns
    def _json_model(self):
        return self.chat_model.bind(**self.info.json_kwargs) if self.info.json_kwargs else self.chat_model

    def structured(self, system: str, payload: str, model_cls: type[T]) -> T:
        self._require_key()
        parser = PydanticOutputParser(pydantic_object=model_cls)
        prompt = ChatPromptTemplate.from_messages([SystemMessage(content=system), ("human", "{payload}")])
        chain = prompt | self._json_model() | StrOutputParser() | RunnableLambda(_json_slice) | parser
        last_error, text = "", payload
        for _ in range(self.max_repairs + 1):
            try:
                return chain.invoke({"payload": text})
            except OutputParserException as exc:
                last_error = str(exc)[:400]
                text = (payload + "\n\nYour previous reply was invalid: " + last_error
                        + "\nReturn ONLY one valid JSON object that follows the schema.")
            except Exception as exc:  # noqa: BLE001
                mapped = self.map_error(exc)
                if mapped is not None:
                    raise mapped from exc
                raise
        raise LLMOutputError(f"Model did not return valid structured output: {last_error}")

    def plan(self, task_text: str, tools: list[dict]) -> Plan:
        return self.structured(prompts.PLAN_SYSTEM, prompts.plan_user_prompt(task_text, tools), Plan)

    def decide(self, ctx: DecisionContext) -> Decision:
        return self.structured(prompts.DECIDE_SYSTEM, prompts.decide_user_prompt(ctx), Decision)

    # ------------------------------------------------------------------ health
    def health(self) -> dict[str, Any]:
        i = self.info
        if i.provider == "openai":
            if not i.api_key:
                return {"ok": False, "detail": "OPENAI_API_KEY is not set"}
            return {"ok": True, "detail": f"API key configured for {i.base} (not pinged)"}
        label = i.label
        if i.is_cloud and not i.api_key:
            return {"ok": False, "detail": "OLLAMA_API_KEY is not set (create one at ollama.com/settings/keys)"}
        headers = {"Authorization": f"Bearer {i.api_key}"} if i.api_key else {}
        try:
            r = httpx.get(f"{i.base}/api/tags", headers=headers, timeout=5)
            if r.status_code in (401, 403):
                return {"ok": False, "detail": f"{label} rejected the API key"}
            r.raise_for_status()
            names = [m.get("name") or m.get("model") or "" for m in r.json().get("models", [])]
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{label} not reachable at {i.base} ({type(exc).__name__})"}
        if not any(n == i.model or n.startswith(f"{i.model}:") for n in names):
            fix = "see https://ollama.com/api/tags for exact names" if i.is_cloud else f"Run: ollama pull {i.model}"
            return {"ok": False, "detail": f"{label} is up but model '{i.model}' is missing; {fix}"}
        return {"ok": True, "detail": f"{label} reachable, model '{i.model}' available"}


def build_llm(settings: Settings, *, transport: Optional[httpx.BaseTransport] = None) -> LLMClient:
    """Create the configured provider. `transport` is a test hook (httpx.MockTransport)."""
    if settings.llm_provider == "ollama":
        from langchain_ollama import ChatOllama

        base = settings.ollama_base_url.rstrip("/")
        key = (settings.ollama_api_key or "").strip()
        host = (urlparse(base).hostname or "").lower()
        is_cloud = host == "ollama.com" or host.endswith(".ollama.com")
        client_kwargs: dict[str, Any] = {"timeout": settings.llm_timeout_seconds}
        if key:
            client_kwargs["headers"] = {"Authorization": f"Bearer {key}"}
        if transport is not None:
            client_kwargs["transport"] = transport
        kwargs: dict[str, Any] = dict(model=settings.ollama_model, base_url=base, temperature=0, client_kwargs=client_kwargs)
        if not is_cloud and settings.ollama_num_ctx:
            kwargs["num_ctx"] = settings.ollama_num_ctx  # local Ollama silently truncates beyond this
        info = ProviderInfo(
            provider="ollama-cloud" if is_cloud else "ollama", label="Ollama Cloud" if is_cloud else "Ollama",
            model=settings.ollama_model, base=base, timeout=settings.llm_timeout_seconds, api_key=key,
            key_env="OLLAMA_API_KEY", is_cloud=is_cloud, json_kwargs={"format": "json"},
        )
        return ChainClient(ChatOllama(**kwargs), info, settings.llm_max_repairs)
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        base = settings.openai_base_url.rstrip("/")
        extra: dict[str, Any] = {}
        if transport is not None:
            extra["http_client"] = httpx.Client(transport=transport)
        chat = ChatOpenAI(model=settings.openai_model, api_key=SecretStr(settings.openai_api_key or "not-set"),
                          base_url=base, temperature=0, timeout=settings.llm_timeout_seconds, max_retries=0, **extra)
        info = ProviderInfo(
            provider="openai", label="LLM provider", model=settings.openai_model, base=base,
            timeout=settings.llm_timeout_seconds, api_key=settings.openai_api_key, key_env="OPENAI_API_KEY",
            json_kwargs={"response_format": {"type": "json_object"}},
        )
        return ChainClient(chat, info, settings.llm_max_repairs)
    from app.agent.demo import DemoClient

    return DemoClient()
