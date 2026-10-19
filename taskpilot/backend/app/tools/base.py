"""Tool registry primitives.

A Tool has: name, description, validated inputs (Pydantic), permission
("read" | "write"), and an execution function. The agent can only invoke tools
that are registered here - there is no shell or arbitrary-code tool.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import ErrorType


class ToolFailure(Exception):
    def __init__(self, error_type: ErrorType, message: str, data: Optional[dict] = None):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.data = data or {}


class TransientToolError(ToolFailure):
    def __init__(self, message: str, data: Optional[dict] = None):
        super().__init__(ErrorType.TRANSIENT, message, data)


class ToolResult(BaseModel):
    ok: bool = True
    summary: str = ""
    data: dict[str, Any] = {}
    error_type: Optional[ErrorType] = None
    error: Optional[str] = None
    # facts merged into the agent's working memory by deterministic code
    memory: dict[str, Any] = {}
    # security notes (e.g. instruction-like text found inside a document)
    warnings: list[str] = []

    @classmethod
    def failure(cls, error_type: ErrorType, message: str, data: Optional[dict] = None) -> "ToolResult":
        return cls(ok=False, summary=message, error_type=error_type, error=message, data=data or {})


@dataclass
class ToolContext:
    settings: Settings
    db: Session
    task_id: str

    @property
    def root(self):
        return self.settings.workspace_root


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    permission: Literal["read", "write"]
    func: Callable[[ToolContext, Any], ToolResult]
    # Optional: after a TRANSIENT failure of a write, inspect state before retrying.
    recover: Optional[Callable[[ToolContext, Any], Optional[ToolResult]]] = None
    # Optional: return an error message to reject a write BEFORE asking the human to approve it.
    preflight: Optional[Callable[[ToolContext, Any], Optional[str]]] = None

    def spec(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission,
            "requires_approval": self.permission == "write",
            "arguments": {
                k: {
                    "type": v.get("type") or v.get("anyOf"),
                    "description": v.get("description", ""),
                    "default": v.get("default"),
                }
                for k, v in schema.get("properties", {}).items()
            },
            "required": schema.get("required", []),
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def describe(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def prepare(self, name: str, raw_args: dict[str, Any]) -> tuple[Tool, BaseModel]:
        """Resolve the tool and validate arguments. Raises ToolFailure."""
        tool = self._tools.get(name or "")
        if tool is None:
            raise ToolFailure(
                ErrorType.VALIDATION,
                f"Unknown tool '{name}'. Registered tools: {', '.join(self._tools)}",
            )
        try:
            args = tool.args_model.model_validate(raw_args or {})
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in exc.errors()
            )
            raise ToolFailure(ErrorType.VALIDATION, f"Invalid arguments for {name}: {problems}")
        return tool, args

    def invoke(self, tool: Tool, args: BaseModel, ctx: ToolContext) -> ToolResult:
        """Run a tool. Never raises: every failure becomes a classified ToolResult."""
        try:
            return tool.func(ctx, args)
        except ToolFailure as exc:
            ctx.db.rollback()
            return ToolResult.failure(exc.error_type, exc.message, exc.data)
        except (TimeoutError, ConnectionError) as exc:
            ctx.db.rollback()
            return ToolResult.failure(ErrorType.TRANSIENT, f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - last line of defence
            ctx.db.rollback()
            return ToolResult.failure(ErrorType.INTERNAL, f"{type(exc).__name__}: {exc}")


def canonical_key(tool_name: str, args: BaseModel) -> str:
    """Stable fingerprint of an exact tool call (used to bind approvals)."""
    import hashlib

    blob = json.dumps({"t": tool_name, "a": args.model_dump(mode="json")}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()
