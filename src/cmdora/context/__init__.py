"""Safe workspace context preparation for the model layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cmdora.safety import redact_sensitive_text
from cmdora.schemas import ActionKind


class SafeContext(BaseModel):
    """Allowlisted, repository-scoped metadata that may be sent to a planner."""

    model_config = ConfigDict(extra="forbid", strict=True)

    shell: Literal["PowerShell"] = "PowerShell"
    operating_system: Literal["Windows"] = "Windows"
    workspace_basename: str = Field(min_length=1)
    is_git_repository: bool
    git_branch: str | None
    git_status: str
    supported_actions: list[ActionKind]
    autonomy_mode: Literal["off", "git"]

    @field_validator("workspace_basename")
    @classmethod
    def require_basename_only(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "/" in normalized or "\\" in normalized:
            raise ValueError("workspace_basename must contain only the workspace name")
        return sanitize_model_context(normalized)

    @field_validator("git_branch")
    @classmethod
    def trim_branch(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("git_branch must be null or non-empty")
        return sanitize_model_context(normalized)

    @field_validator("git_status")
    @classmethod
    def sanitize_git_status(cls, value: str) -> str:
        return sanitize_model_context(value)

    @field_validator("supported_actions")
    @classmethod
    def require_unique_actions(cls, value: list[ActionKind]) -> list[ActionKind]:
        if len(value) != len(set(value)):
            raise ValueError("supported_actions must not contain duplicates")
        return value


def sanitize_model_context(context: str) -> str:
    """Return context suitable for a model without secrets or local home paths."""
    return redact_sensitive_text(context)


__all__ = ["SafeContext", "sanitize_model_context"]
