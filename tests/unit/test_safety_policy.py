"""Tests for Phase 4 local command and workspace safety decisions."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cmdora.safety import (
    CommandCategory,
    PolicyDecision,
    UnsafePathError,
    assess_command,
    format_policy_decision,
    validate_workspace_path,
)


@pytest.mark.parametrize(
    "command",
    [
        "Remove-Item notes.txt",
        "del notes.txt",
        "rd cache",
        "rmdir cache",
        "Format-Volume -DriveLetter C",
        "Clear-Disk -Number 0",
        "Set-ExecutionPolicy Bypass",
        "Invoke-Expression 'Get-Date'",
        "Start-Process notepad.exe",
        "cmd /c whoami",
        "powershell -EncodedCommand AA==",
        "New-Item -Path HKCU:\\Software\\Cmdora",
        "Register-ScheduledTask -TaskName Cmdora",
        "runas /user:Administrator cmd.exe",
        "cmdkey /list",
        "Get-Content .ssh\\id_rsa",
        "Get-Content .env",
        "Get-Content 'Browser Data'",
        "git reset --hard",
        "git clean -fd",
        "git push --force origin HEAD",
        "git branch -D old-feature",
        "git remote set-url origin https://example.test/repo.git",
        "pip install example-package",
        "Invoke-WebRequest https://example.test/script.ps1",
    ],
)
def test_explicitly_blocked_commands_are_never_allowlisted(tmp_path: Path, command: str) -> None:
    decision = assess_command(command, tmp_path)

    assert not decision.allowed
    assert decision.category == CommandCategory.BLOCKED
    assert decision.reason


def test_safe_allowlist_entries_are_classified(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    file_path = source / "sample.py"
    file_path.write_text("print('safe')\n", encoding="utf-8")

    status = assess_command("git status --short --branch", tmp_path)
    diff = assess_command("git diff --stat", tmp_path)
    content = assess_command(
        "Get-Content src\\sample.py", tmp_path, approved_read_files=[file_path]
    )
    stage = assess_command("git add src\\sample.py", tmp_path)

    assert status.allowed and status.category == CommandCategory.READ_ONLY
    assert diff.allowed and diff.category == CommandCategory.READ_ONLY
    assert content.allowed and content.category == CommandCategory.READ_ONLY
    assert stage.allowed and stage.category == CommandCategory.GIT_STAGE


@pytest.mark.parametrize(
    "target",
    [
        "..\\outside.txt",
        "src\\..\\secret.txt",
        ".env",
        ".env.local",
        ".git\\config",
        ".ssh\\id_ed25519",
        ".Cmdora\\config.toml",
    ],
)
def test_workspace_path_validation_blocks_traversal_and_protected_paths(
    tmp_path: Path, target: str
) -> None:
    with pytest.raises(UnsafePathError):
        validate_workspace_path(tmp_path, target)


def test_workspace_path_validation_allows_a_normal_workspace_file(tmp_path: Path) -> None:
    target = validate_workspace_path(tmp_path, "src\\main.py")

    assert target == tmp_path / "src" / "main.py"


def test_workspace_path_validation_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Cannot create a directory symlink on this system: {error}")

    with pytest.raises(UnsafePathError, match="outside"):
        validate_workspace_path(tmp_path, "linked\\escape.txt")


def test_policy_decision_is_structured_and_sanitizes_commands(tmp_path: Path) -> None:
    assessment = assess_command("git status --short", tmp_path)

    assert isinstance(assessment.decision, PolicyDecision)
    assert assessment.decision.model_dump() == {
        "allowed": True,
        "risk_level": "safe",
        "requires_confirmation": False,
        "reasons": ["Git status is read-only."],
        "sanitized_command": "git status",
    }


def test_blocked_policy_output_is_readable_and_suggests_an_alternative(tmp_path: Path) -> None:
    decision = assess_command("Remove-Item -Recurse temporary", tmp_path).decision

    output = format_policy_decision(decision)

    assert output.startswith("Blocked:")
    assert "Why:" in output
    assert "Suggested alternative:" in output
    assert "File Explorer" in output


def test_commit_requires_repository_changes_staged_files_and_message(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-b", "feature/safety")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Cmdora Test")
    (tmp_path / "sample.txt").write_text("example\n", encoding="utf-8")

    unstaged = assess_command('git commit -m "save sample"', tmp_path)
    assert not unstaged.allowed
    assert "No files are staged" in " ".join(unstaged.decision.reasons)

    _git(tmp_path, "add", "sample.txt")
    ready = assess_command('git commit -m "save sample"', tmp_path)
    assert ready.allowed
    assert ready.decision.risk_level == "caution"
    assert ready.decision.requires_confirmation
    assert "sample.txt" in " ".join(ready.decision.reasons)

    missing_message = assess_command("git commit", tmp_path, autonomy_mode="git")
    assert not missing_message.allowed
    assert "cannot fabricate" in " ".join(missing_message.decision.reasons)


def test_push_requires_origin_explicit_request_non_protected_branch_and_unchanged_remote(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-b", "feature/safety")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Cmdora Test")
    (tmp_path / "sample.txt").write_text("example\n", encoding="utf-8")
    _git(tmp_path, "add", "sample.txt")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "remote", "add", "origin", "https://example.test/cmdora.git")

    implicit = assess_command("git push origin HEAD", tmp_path)
    assert not implicit.allowed
    assert "explicit user request" in " ".join(implicit.decision.reasons)

    safe = assess_command(
        "git push origin HEAD",
        tmp_path,
        current_branch="feature/safety",
        explicit_push_request=True,
        expected_origin_url="https://example.test/cmdora.git",
    )
    assert safe.allowed
    assert safe.decision.risk_level == "high"
    assert safe.decision.requires_confirmation

    changed_remote = assess_command(
        "git push origin HEAD",
        tmp_path,
        current_branch="feature/safety",
        explicit_push_request=True,
        expected_origin_url="https://other.example/cmdora.git",
    )
    assert not changed_remote.allowed
    assert "remote URL changed" in " ".join(changed_remote.decision.reasons)


def test_push_to_protected_branch_is_rejected(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "remote", "add", "origin", "https://example.test/cmdora.git")

    assessment = assess_command(
        "git push origin HEAD",
        tmp_path,
        current_branch="main",
        explicit_push_request=True,
        expected_origin_url="https://example.test/cmdora.git",
    )

    assert not assessment.allowed
    assert "protected branch" in " ".join(assessment.decision.reasons)


def test_push_with_detached_head_is_rejected(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-b", "feature/safety")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Cmdora Test")
    (tmp_path / "sample.txt").write_text("example\n", encoding="utf-8")
    _git(tmp_path, "add", "sample.txt")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "remote", "add", "origin", "https://example.test/cmdora.git")
    _git(tmp_path, "checkout", "--detach")

    assessment = assess_command(
        "git push origin HEAD",
        tmp_path,
        explicit_push_request=True,
        expected_origin_url="https://example.test/cmdora.git",
    )

    assert not assessment.allowed
    assert "detached" in " ".join(assessment.decision.reasons)


def _git(workspace: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
