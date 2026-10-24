"""Integration checks against temporary workspaces and local Git repositories."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cmdora.executor import DeterministicOperations
from cmdora.executor.powershell import PowerShellExecutor


def _git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=path, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _initialize_repo(path: Path, *, branch: str = "feature/integration") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", branch)
    _git(path, "config", "user.name", "Cmdora Integration Test")
    _git(path, "config", "user.email", "cmdora@example.test")
    _git(path, "config", "commit.gpgsign", "false")


def test_workspace_creation_search_and_git_status_commit_and_local_push(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "workspace"
    bare_remote = tmp_path / "remote.git"
    _initialize_repo(repository)
    _git(tmp_path, "init", "--bare", str(bare_remote))
    _git(repository, "remote", "add", "origin", str(bare_remote))
    operations = DeterministicOperations(repository)

    created = operations.create_file("src/new_file.py")
    assert created.is_file()
    with pytest.raises(FileExistsError):
        operations.create_file("src/new_file.py")
    with pytest.raises(ValueError, match="outside the selected workspace"):
        operations.create_file(tmp_path / "outside.py")

    today_file = repository / "today.txt"
    yesterday_file = repository / "yesterday.txt"
    today_file.write_text("today", encoding="utf-8")
    yesterday_file.write_text("yesterday", encoding="utf-8")
    now = datetime.now().astimezone()
    old_timestamp = datetime.combine(
        now.date() - timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo
    ).timestamp()
    os.utime(yesterday_file, (old_timestamp, old_timestamp))
    assert {item.path for item in operations.list_modified_today()} >= {
        Path("today.txt"),
        Path("src/new_file.py"),
    }
    assert Path("yesterday.txt") not in {item.path for item in operations.list_modified_today()}

    (repository / "README.md").write_text("integration fixture\n", encoding="utf-8")
    assert operations.git_status().succeeded
    assert "README.md" in operations.git_status().stdout
    assert operations.git_add(["README.md"]).succeeded
    assert operations.git_commit("integration fixture").succeeded
    pushed = operations.git_push()
    assert pushed.succeeded, pushed.stderr

    local_head = _git(repository, "rev-parse", "HEAD")
    remote_head = _git(bare_remote, "rev-parse", "refs/heads/feature/integration")
    assert remote_head == local_head


@pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="Windows PowerShell unavailable")
def test_real_powershell_exit_code_and_timeout(tmp_path: Path) -> None:
    normal = PowerShellExecutor(tmp_path, timeout_seconds=5).execute(
        "[Console]::Out.Write('stdout-marker'); [Console]::Error.Write('stderr-marker'); exit 7"
    )
    assert normal.exit_code == 7
    assert "stdout-marker" in normal.stdout
    assert "stderr-marker" in normal.stderr

    timed_out = PowerShellExecutor(tmp_path, timeout_seconds=0.25).execute(
        "Start-Sleep -Seconds 5"
    )
    assert timed_out.timed_out
    assert not timed_out.succeeded
