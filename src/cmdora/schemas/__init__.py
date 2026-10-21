"""Validated schemas for model-generated action plans."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ActionKind = Literal[
    "inspect",
    "create_file",
    "list_modified_today",
    "git_status",
    "git_add",
    "git_commit",
    "git_push",
    "shell_command",
    "clarification",
]


class ActionStep(BaseModel):
    """One proposed operation in a structured Cmdora plan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    kind: ActionKind
    command: str | None
    purpose: str = Field(min_length=1)
    expected_effect: str = Field(min_length=1)
    requested_paths: list[str]
    risk_hint: str = Field(min_length=1)

    @field_validator("id", "purpose", "expected_effect", "risk_hint")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("command")
    @classmethod
    def trim_command(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("command must be non-empty when provided")
        return normalized

    @field_validator("requested_paths")
    @classmethod
    def trim_requested_paths(cls, paths: list[str]) -> list[str]:
        normalized = [path.strip() for path in paths]
        if any(not path for path in normalized):
            raise ValueError("requested_paths entries must not be empty")
        return normalized


class ActionPlan(BaseModel):
    """A complete, validated plan produced from one natural-language request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    intent: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    needs_clarification: bool
    clarifying_question: str | None
    steps: list[ActionStep]

    @field_validator("intent", "summary")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("clarifying_question")
    @classmethod
    def trim_question(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("clarifying_question must be non-empty when provided")
        return normalized

    @model_validator(mode="after")
    def validate_plan_consistency(self) -> ActionPlan:
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step IDs must be unique within an action plan")
        if self.needs_clarification and self.clarifying_question is None:
            raise ValueError("a clarifying_question is required when needs_clarification is true")
        if not self.needs_clarification and self.clarifying_question is not None:
            raise ValueError("clarifying_question must be null when clarification is not needed")
        return self


__all__ = ["ActionKind", "ActionPlan", "ActionStep"]
