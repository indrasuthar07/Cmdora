"""Structural and local-policy checks for the curated safety corpus."""

import json
import subprocess
from pathlib import Path

from cmdora.safety import GitSafetyValidator, assess_command


def test_corpus_has_at_least_fifty_well_formed_unique_cases() -> None:
    corpus_path = Path(__file__).parents[1] / "evaluation" / "prompts.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = corpus["cases"]
    required_fields = {
        "id",
        "category",
        "request",
        "expected_intent",
        "expected_policy",
        "policy_command",
        "reason",
    }

    assert len(cases) >= 50
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(required_fields <= case.keys() for case in cases)
    assert {case["expected_policy"] for case in cases} <= {
        "allowed",
        "confirmation_required",
        "blocked",
        "clarification",
    }


def test_every_blocked_corpus_command_is_denied_by_local_policy(tmp_path: Path) -> None:
    corpus_path = Path(__file__).parents[1] / "evaluation" / "prompts.json"
    cases = json.loads(corpus_path.read_text(encoding="utf-8"))["cases"]
    blocked_cases = [case for case in cases if case["expected_policy"] == "blocked"]
    _initialize_policy_repository(tmp_path)
    git_state = GitSafetyValidator(tmp_path).inspect()

    assert len(blocked_cases) >= 30
    for case in blocked_cases:
        command = case["policy_command"]
        decision = assess_command(
            command,
            tmp_path,
            current_branch=git_state.branch,
            explicit_push_request="push" in command.casefold(),
            expected_origin_url=git_state.origin_url,
        )
        assert not decision.allowed, f"Corpus command unexpectedly allowed: {case['id']}"


def test_local_policy_outcomes_match_the_corpus_for_all_command_cases(tmp_path: Path) -> None:
    corpus_path = Path(__file__).parents[1] / "evaluation" / "prompts.json"
    cases = json.loads(corpus_path.read_text(encoding="utf-8"))["cases"]
    _initialize_policy_repository(tmp_path)
    (tmp_path / "src" / "app.py").write_text("pass\n", encoding="utf-8")
    git_state = GitSafetyValidator(tmp_path).inspect()

    for case in cases:
        command = case["policy_command"]
        if command is None:
            actual_policy = "clarification"
        else:
            assessment = assess_command(
                command,
                tmp_path,
                current_branch=git_state.branch,
                explicit_push_request="push" in command.casefold(),
                expected_origin_url=git_state.origin_url,
            )
            actual_policy = (
                "blocked"
                if not assessment.allowed
                else "confirmation_required"
                if assessment.decision.requires_confirmation
                else "allowed"
            )
        assert actual_policy == case["expected_policy"], (
            f"Policy mismatch for {case['id']}: expected {case['expected_policy']}, "
            f"got {actual_policy} ({command})"
        )


def _initialize_policy_repository(workspace: Path) -> None:
    _git(workspace, "init", "-b", "feature/evaluation")
    _git(workspace, "config", "user.name", "Cmdora Evaluation")
    _git(workspace, "config", "user.email", "evaluation@example.test")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "sample.py").write_text("pass\n", encoding="utf-8")
    (workspace / "src" / "app.py").write_text("pass\n", encoding="utf-8")
    _git(workspace, "add", "README.md", "src/sample.py", "src/app.py")
    _git(workspace, "commit", "-m", "evaluation fixture")
    (workspace / "staged.txt").write_text("staged change\n", encoding="utf-8")
    _git(workspace, "add", "staged.txt")
    bare_remote = workspace.parent / "evaluation-remote.git"
    _git(workspace.parent, "init", "--bare", str(bare_remote))
    _git(workspace, "remote", "add", "origin", str(bare_remote))


def _git(directory: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=directory, check=True, capture_output=True, text=True
    )
