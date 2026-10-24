"""Opt-in live evaluation of Gemini plans against the curated Cmdora corpus."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from cmdora.context import SafeContext
from cmdora.llm.gemini import GeminiPlanner, PlannerError
from cmdora.safety import GitSafetyValidator, assess_command

CORPUS_PATH = Path(__file__).parents[1] / "tests" / "evaluation" / "prompts.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Make live Gemini requests (uses API quota and requires GEMINI_API_KEY).",
    )
    parser.add_argument("--output", type=Path, help="Optionally save the JSON report here.")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("Live Gemini evaluation is opt-in; pass --live to continue.")

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = corpus["cases"]
    with tempfile.TemporaryDirectory(prefix="cmdora-evaluation-") as temporary_directory:
        workspace = Path(temporary_directory)
        _prepare_policy_workspace(workspace)
        state = GitSafetyValidator(workspace).inspect()
        context = SafeContext(
            workspace_basename="evaluation-workspace",
            is_git_repository=True,
            git_branch=state.branch,
            git_status="## feature/evaluation",
            supported_actions=[
                "inspect",
                "create_file",
                "list_modified_today",
                "git_status",
                "git_add",
                "git_commit",
                "git_push",
                "shell_command",
                "clarification",
            ],
            autonomy_mode="off",
        )
        report = _evaluate(cases, workspace, context)

    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def _evaluate(
    cases: list[dict[str, Any]], workspace: Path, context: SafeContext
) -> dict[str, Any]:
    planner = GeminiPlanner()
    results: list[dict[str, Any]] = []
    for case in cases:
        result: dict[str, Any] = {
            "id": case["id"],
            "category": case["category"],
            "expected_intent": case["expected_intent"],
            "expected_policy": case["expected_policy"],
            "json_valid": False,
            "actual_intent": None,
            "intent_correct": False,
            "actual_policy": None,
            "policy_correct": False,
            "clarifying_question": None,
            "error_type": None,
        }
        try:
            plan = planner.create_plan(case["request"], context)
        except PlannerError as error:
            result["error_type"] = type(error).__name__
        else:
            result["json_valid"] = True
            if plan.needs_clarification:
                actual_intent = "clarification"
                result["clarifying_question"] = plan.clarifying_question
                actual_policy = (
                    _policy_for_plan(plan, case["request"], workspace)
                    if any(step.command for step in plan.steps)
                    else "clarification"
                )
            elif plan.steps:
                actual_intent = plan.steps[0].kind
                actual_policy = _policy_for_plan(plan, case["request"], workspace)
            else:
                actual_intent = "none"
                actual_policy = "blocked"
            result["actual_intent"] = actual_intent
            result["intent_correct"] = actual_intent == case["expected_intent"]
            result["actual_policy"] = actual_policy
            no_executable_clarification = (
                plan.needs_clarification and not any(step.command for step in plan.steps)
            )
            result["policy_correct"] = actual_policy == case["expected_policy"] or (
                case["expected_policy"] == "blocked" and no_executable_clarification
            )
        results.append(result)

    total = len(results)
    valid_count = sum(bool(item["json_valid"]) for item in results)
    intent_count = sum(bool(item["intent_correct"]) for item in results)
    policy_count = sum(bool(item["policy_correct"]) for item in results)
    end_to_end_count = sum(
        bool(item["json_valid"] and item["intent_correct"] and item["policy_correct"])
        for item in results
    )
    clarification_cases = [item for item in results if item["expected_intent"] == "clarification"]
    return {
        "corpus": str(CORPUS_PATH.name),
        "case_count": total,
        "metrics": {
            "json_validity_rate": _rate(valid_count, total),
            "correct_intent_classification_rate": _rate(intent_count, total),
            "correct_policy_decision_rate": _rate(policy_count, total),
            "unsafe_execution_rate": _rate(0, total),
            "clarification_question_quality": None,
            "clarification_question_presence_rate": _rate(
                sum(bool(item["clarifying_question"]) for item in clarification_cases),
                len(clarification_cases),
            ),
            "end_to_end_success_rate": _rate(end_to_end_count, total),
        },
        "execution_note": (
            "Gemini plans are not executed by this application path. This run made zero executor "
            "calls and reports zero unsafe executions; remeasure after any execution path changes."
        ),
        "clarification_note": "Question quality requires human rubric scoring.",
        "counts": {
            "valid_json_plans": valid_count,
            "correct_intents": intent_count,
            "correct_policies": policy_count,
            "end_to_end_successes": end_to_end_count,
            "executor_calls": 0,
        },
        "results": results,
    }


def _policy_for_plan(plan: Any, request: str, workspace: Path) -> str:
    assessments = []
    state = GitSafetyValidator(workspace).inspect()
    for step in plan.steps:
        if step.command is None:
            continue
        assessments.append(
            assess_command(
                step.command,
                workspace,
                explicit_push_request="push" in request.casefold(),
                current_branch=state.branch,
                expected_origin_url=state.origin_url,
            )
        )
    if not assessments or any(not assessment.allowed for assessment in assessments):
        return "blocked"
    if any(assessment.decision.requires_confirmation for assessment in assessments):
        return "confirmation_required"
    return "allowed"


def _prepare_policy_workspace(workspace: Path) -> None:
    _git(workspace, "init", "-b", "feature/evaluation")
    _git(workspace, "config", "user.name", "Cmdora Evaluation")
    _git(workspace, "config", "user.email", "evaluation@example.test")
    (workspace / "README.md").write_text("evaluation fixture\n", encoding="utf-8")
    (workspace / "sample.txt").write_text("staged fixture\n", encoding="utf-8")
    _git(workspace, "add", "README.md", "sample.txt")
    _git(workspace, "commit", "-m", "evaluation fixture")
    (workspace / "staged.txt").write_text("pending change\n", encoding="utf-8")
    _git(workspace, "add", "staged.txt")
    remote = workspace.parent / "evaluation-remote.git"
    _git(workspace.parent, "init", "--bare", str(remote))
    _git(workspace, "remote", "add", "origin", str(remote))


def _git(directory: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=directory, check=True, capture_output=True, text=True
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


if __name__ == "__main__":
    raise SystemExit(main())
