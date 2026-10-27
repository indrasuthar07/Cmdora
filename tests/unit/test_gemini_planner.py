"""Offline tests for Gemini planning and provider fakes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeGeminiClient, FakePlanner
from google.genai.errors import APIError

from cmdora.cli.main import RequestOptions, _handle_planned_request, _planner_dotenv_path
from cmdora.context import SafeContext
from cmdora.llm import PlannerProvider
from cmdora.llm.gemini import (
    SYSTEM_PROMPT,
    GeminiPlanner,
    InvalidPlanResponseError,
    MissingAPIKeyError,
    PlannerError,
    _gemini_response_schema,
)
from cmdora.schemas import ActionPlan


class FakeResponse:
    def __init__(self, *, text: str | None = None, parsed: Any = None) -> None:
        self.text = text
        self.parsed = parsed


def make_context() -> SafeContext:
    return SafeContext(
        workspace_basename="Cmdora",
        is_git_repository=True,
        git_branch="feature/planning",
        git_status=" M src/cmdora/llm/gemini.py",
        supported_actions=["create_file", "git_status", "shell_command", "clarification"],
        autonomy_mode="off",
    )


def valid_plan_json(kind: str = "create_file") -> str:
    return json.dumps(
        {
            "intent": "Create a project file",
            "summary": "Proposes creating a small file in the workspace.",
            "needs_clarification": False,
            "clarifying_question": None,
            "steps": [
                {
                    "id": "step-1",
                    "kind": kind,
                    "command": "New-Item -ItemType File -Path '.\\notes.txt'",
                    "purpose": "Create the requested file",
                    "expected_effect": "A new empty file will exist in the workspace.",
                    "requested_paths": ["notes.txt"],
                    "risk_hint": "Writes one new file.",
                }
            ],
        }
    )


def test_gemini_returns_a_schema_valid_plan_with_system_rules_and_redacted_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-unit-test")
    home_path = str(__import__("pathlib").Path.home())
    context = make_context().model_copy(
        update={"git_status": f" M file.txt password=local-status-secret {home_path}"}
    )
    # Revalidate to ensure the fake context includes sanitizer behavior.
    context = SafeContext.model_validate(context.model_dump())
    client = FakeGeminiClient(FakeResponse(text=valid_plan_json()))

    plan = GeminiPlanner(client=client).create_plan(
        "create notes.txt; GEMINI_API_KEY=request-secret", context
    )

    assert isinstance(plan, ActionPlan)
    assert plan.steps[0].kind == "create_file"
    call = client.models.calls[0]
    config = call["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is None
    assert config.response_json_schema == _gemini_response_schema()
    assert "$defs" not in str(config.response_json_schema)
    assert "anyOf" not in str(config.response_json_schema)
    assert config.system_instruction == SYSTEM_PROMPT
    assert config.temperature is None
    assert "PowerShell syntax only" in config.system_instruction
    assert "Never propose destructive commands" in config.system_instruction
    assert "request-secret" not in call["contents"]
    assert "local-status-secret" not in call["contents"]
    assert home_path not in call["contents"]
    assert "file contents" not in call["contents"].lower()


def test_gemini_rejects_malformed_json_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-unit-test")
    client = FakeGeminiClient(FakeResponse(text='{"intent": invalid json'))

    with pytest.raises(InvalidPlanResponseError, match="does not match"):
        GeminiPlanner(client=client).create_plan("show git status", make_context())


def test_gemini_rejects_action_not_in_supported_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-unit-test")
    client = FakeGeminiClient(FakeResponse(text=valid_plan_json(kind="git_push")))

    with pytest.raises(InvalidPlanResponseError, match="unsupported action"):
        GeminiPlanner(client=client).create_plan("create notes.txt", make_context())


def test_missing_key_has_setup_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(MissingAPIKeyError, match="Set the GEMINI_API_KEY"):
        GeminiPlanner(
            client=FakeGeminiClient(), dotenv_path=tmp_path / "missing.env"
        ).create_plan("show git status", make_context())


def test_gemini_loads_key_from_workspace_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("GEMINI_API_KEY=key-from-dotenv\n", encoding="utf-8")
    client = FakeGeminiClient(FakeResponse(text=valid_plan_json()))

    plan = GeminiPlanner(client=client, dotenv_path=dotenv_path).create_plan(
        "create notes.txt", make_context()
    )

    assert plan.steps[0].kind == "create_file"


def test_cli_dotenv_path_uses_workspace_dotenv_when_present(tmp_path: Path) -> None:
    workspace_dotenv = tmp_path / ".env"
    workspace_dotenv.write_text("GEMINI_API_KEY=workspace-key\n", encoding="utf-8")

    assert _planner_dotenv_path(tmp_path) == workspace_dotenv


def test_cli_dotenv_path_falls_back_to_checkout_dotenv_for_demo_workspace(tmp_path: Path) -> None:
    checkout_root = Path(__file__).resolve().parents[2]
    test_env = checkout_root / ".env"
    created = False
    if not test_env.exists():
        test_env.write_text("GEMINI_API_KEY=test-fallback\n", encoding="utf-8")
        created = True
    try:
        path = _planner_dotenv_path(tmp_path)
        assert path.name == ".env"
        assert path.is_file()
        assert (path.parent / "pyproject.toml").is_file()
    finally:
        if created:
            test_env.unlink(missing_ok=True)


def test_bad_request_error_identifies_request_or_schema_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-unit-test")
    api_error = APIError(
        400,
        {"error": {"status": "INVALID_ARGUMENT", "message": "bad config"}},
    )

    with pytest.raises(PlannerError, match="400 INVALID_ARGUMENT.*bad config"):
        GeminiPlanner(client=FakeGeminiClient(api_error)).create_plan(
            "show git status", make_context()
        )


def test_html_bad_request_suggests_network_or_proxy_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-unit-test")
    html_error = APIError(
        400,
        {"error": {"status": "BAD_REQUEST", "message": "<html><title>Error 400</title>"}},
    )

    with pytest.raises(PlannerError, match=r"HTML 400.*HTTP\(S\)_PROXY"):
        GeminiPlanner(client=FakeGeminiClient(html_error)).create_plan(
            "show git status", make_context()
        )


def test_fake_planner_implements_provider_protocol_and_can_supply_unsafe_plan() -> None:
    context = make_context()
    safe_plan = ActionPlan.model_validate_json(valid_plan_json())
    unsafe_step = safe_plan.steps[0].model_copy(update={"command": "Remove-Item -Recurse ."})
    unsafe_plan = safe_plan.model_copy(update={"steps": [unsafe_step]})
    fake = FakePlanner(unsafe_plan)
    planner: PlannerProvider = fake

    returned = planner.create_plan("delete everything", context)

    assert returned.steps[0].command == "Remove-Item -Recurse ."
    assert returned is not unsafe_plan
    assert len(fake.calls) == 1


def test_fake_planner_can_simulate_provider_failure() -> None:
    planner = FakePlanner(InvalidPlanResponseError("malformed model output"))

    with pytest.raises(InvalidPlanResponseError, match="malformed model output"):
        planner.create_plan("show git status", make_context())


def test_cli_planning_path_accepts_fake_provider_without_calling_gemini(
    tmp_path: Path,
) -> None:
    fake = FakePlanner(ActionPlan.model_validate_json(valid_plan_json()))
    options = RequestOptions(dry_run=False, autonomy=None, json_output=True, verbose=False)

    plan = _handle_planned_request(
        "create notes.txt",
        options,
        "test-request-id",
        planner=fake,
        workspace_path=tmp_path,
    )

    assert plan.steps[0].kind == "create_file"
    assert len(fake.calls) == 1
    assert fake.calls[0][1].workspace_basename == tmp_path.name
