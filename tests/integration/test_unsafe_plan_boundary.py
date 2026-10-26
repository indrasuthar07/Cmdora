"""Ensure model-proposed destructive steps remain proposals and never reach PowerShell."""

from __future__ import annotations

import json
from pathlib import Path

from pytest_mock import MockerFixture

from cmdora.cli.main import RequestOptions, _handle_planned_request
from cmdora.schemas import ActionPlan


def test_blocked_corpus_plan_commands_never_reach_the_executor(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    corpus_path = Path(__file__).parents[1] / "evaluation" / "prompts.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    blocked_cases = [item for item in corpus["cases"] if item["expected_policy"] == "blocked"]
    execute = mocker.patch("cmdora.executor.powershell.PowerShellExecutor.execute")
    options = RequestOptions(dry_run=False, autonomy=None, json_output=True, verbose=False)

    for index, case in enumerate(blocked_cases):
        plan = ActionPlan.model_validate(
            {
                "intent": "unsafe request",
                "summary": "This is only an untrusted model proposal.",
                "needs_clarification": False,
                "clarifying_question": None,
                "steps": [
                    {
                        "id": "step-1",
                        "kind": "shell_command",
                        "command": case["policy_command"],
                        "purpose": "Corpus safety probe",
                        "expected_effect": "Must not execute",
                        "requested_paths": [],
                        "risk_hint": "blocked corpus case",
                    }
                ],
            }
        )

        _handle_planned_request(
            case["request"],
            options,
            f"blocked-corpus-{index}",
            planner=_StaticPlanner(plan),
            workspace_path=tmp_path,
        )

    execute.assert_not_called()


class _StaticPlanner:
    """Minimal planner returning a fixed schema-valid but unsafe proposal."""

    def __init__(self, plan: ActionPlan) -> None:
        self.plan = plan

    def create_plan(self, request: str, context: object) -> ActionPlan:
        del request, context
        return self.plan
