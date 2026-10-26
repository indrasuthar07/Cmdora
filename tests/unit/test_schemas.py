"""Strict validation tests for provider-facing action plan schemas."""

import pytest
from pydantic import ValidationError

from cmdora.schemas import ActionPlan, ActionStep


def _step(**overrides: object) -> dict[str, object]:
    return {
        "id": "step-1",
        "kind": "git_status",
        "command": "git status --short",
        "purpose": "Inspect repository state",
        "expected_effect": "Shows changed files",
        "requested_paths": [],
        "risk_hint": "Read-only",
        **overrides,
    }


def test_action_plan_validates_a_complete_plan() -> None:
    plan = ActionPlan.model_validate(
        {
            "intent": "inspect repository",
            "summary": "Show current changes",
            "needs_clarification": False,
            "clarifying_question": None,
            "steps": [_step()],
        }
    )

    assert plan.steps[0].kind == "git_status"


@pytest.mark.parametrize(
    ("plan_changes", "message"),
    [
        ({"unexpected": True}, "Extra inputs are not permitted"),
        ({"steps": [_step(), _step()]}, "step IDs must be unique"),
        ({"needs_clarification": True}, "clarifying_question is required"),
        ({"clarifying_question": "Need more details?"}, "must be null"),
        ({"intent": "   "}, "must not be empty"),
    ],
)
def test_action_plan_rejects_invalid_or_inconsistent_data(
    plan_changes: dict[str, object], message: str
) -> None:
    plan: dict[str, object] = {
        "intent": "inspect repository",
        "summary": "Show current changes",
        "needs_clarification": False,
        "clarifying_question": None,
        "steps": [_step()],
    }
    plan.update(plan_changes)

    with pytest.raises(ValidationError, match=message):
        ActionPlan.model_validate(plan)


def test_action_step_rejects_unknown_actions_and_blank_fields() -> None:
    with pytest.raises(ValidationError, match="Input should be 'inspect'"):
        ActionStep.model_validate(_step(kind="delete_files"))

    with pytest.raises(ValidationError, match="must not be empty"):
        ActionStep.model_validate(_step(purpose="  "))
