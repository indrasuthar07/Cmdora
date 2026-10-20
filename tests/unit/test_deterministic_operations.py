"""Tests for the deterministic workspace and Git operations."""

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cmdora.executor import DeterministicOperations


def _initialize_repository(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Cmdora Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "cmdora@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True)


def test_create_file_creates_parent_directories_without_overwriting(tmp_path: Path) -> None:
    operations = DeterministicOperations(tmp_path)

    created = operations.create_file("src/components/Button.js")

    assert created == tmp_path / "src" / "components" / "Button.js"
    assert created.is_file()
    with pytest.raises(FileExistsError):
        operations.create_file("src/components/Button.js")
    with pytest.raises(ValueError, match="outside the selected workspace"):
        operations.create_file(tmp_path.parent / "outside.txt")


def test_list_modified_today_returns_only_current_day_files(tmp_path: Path) -> None:
    operations = DeterministicOperations(tmp_path)
    today_file = tmp_path / "today.txt"
    old_file = tmp_path / "yesterday.txt"
    ignored_file = tmp_path / ".git" / "ignored.txt"
    today_file.write_text("today", encoding="utf-8")
    old_file.write_text("yesterday", encoding="utf-8")
    ignored_file.parent.mkdir()
    ignored_file.write_text("git metadata", encoding="utf-8")
    yesterday = datetime.now().astimezone() - timedelta(days=1)
    old_timestamp = yesterday.timestamp()
    os.utime(old_file, (old_timestamp, old_timestamp))

    results = operations.list_modified_today()

    assert [item.path for item in results] == [Path("today.txt")]
    assert results[0].size_bytes == len("today")


def test_git_status_and_commit_workflow_in_temporary_repository(tmp_path: Path) -> None:
    _initialize_repository(tmp_path)
    operations = DeterministicOperations(tmp_path)

    (tmp_path / "README.md").write_text("initial\n", encoding="utf-8")
    assert operations.git_add(["README.md"]).succeeded
    assert operations.git_commit("initial commit").succeeded

    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    status = operations.git_status()
    assert status.succeeded
    assert "feature.txt" in status.stdout

    assert operations.git_add_all().succeeded
    commit = operations.git_commit("add feature")
    assert commit.succeeded
    assert "add feature" in commit.stdout
    assert operations.git_status().stdout.strip() == "## main"


def test_git_commit_requires_a_message(tmp_path: Path) -> None:
    operations = DeterministicOperations(tmp_path)

    with pytest.raises(ValueError, match="Commit message cannot be empty"):
        operations.git_commit("  ")
