from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class PlanStep(BaseModel):
    description: str
    tool: Optional[str] = None


class Plan(BaseModel):
    task_type: Literal["invoice_ingest", "file_search", "document_qa", "unsupported"]
    goal: str
    params: dict[str, str] = Field(default_factory=dict)
    steps: list[PlanStep] = Field(default_factory=list)
    expected_outcome: str = ""
    requires_write: bool = False
    needs_clarification: Optional[str] = None

    @field_validator("params", mode="before")
    @classmethod
    def _stringify(cls, v):
        if isinstance(v, dict):
            return {str(k): str(val) for k, val in v.items() if val is not None}
        return v


class Decision(BaseModel):
    thought: str = ""
    action: Literal["tool", "finish", "ask_user", "fail"]
    tool: Optional[str] = None
    args: dict[str, Any] = Field(default_factory=dict)
    message: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class DecisionContext(BaseModel):
    task_text: str
    plan: Plan
    facts: dict[str, Any]
    history: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    steps_used: int
    max_steps: int
