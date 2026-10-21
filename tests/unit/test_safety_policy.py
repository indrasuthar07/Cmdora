"""Tests for Phase 4 local command and workspace safety decisions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cmdora.safety import CommandCategory, UnsafePathError, assess_command, validate_workspace_path


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
