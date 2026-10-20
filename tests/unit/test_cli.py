"""Tests for the Cmdora command-line interface."""

import json
import subprocess
from pathlib import Path

from pytest import MonkeyPatch
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from cmdora.cli.main import app

runner = CliRunner()


def test_help_is_available() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "secure natural-language PowerShell" in result.output
    assert "config" in result.output
    assert "history" in result.output
    assert "--dry-run" in result.output
    assert "--autonomy" in result.output
    assert "--json" in result.output
    assert "--verbose" in result.output


def test_version_is_available() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "Cmdora 0.1.0" in result.output


def test_config_init_command_is_available() -> None:
    result = runner.invoke(app, ["config", "init"])

    assert result.exit_code == 0
    assert "available in Phase 2" in result.output


def test_history_command_is_available() -> None:
    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0
    assert "available after audit storage" in result.output


def test_natural_language_request_accepts_execution_flags() -> None:
    result = runner.invoke(
        app,
        [
            "list files modified today",
            "--dry-run",
            "--autonomy",
            "git",
            "--verbose",
        ],
    )

    assert result.exit_code == 0
    assert "Dry run only" in result.output
    assert "Proposed command" in result.output


def test_natural_language_request_can_return_json() -> None:
    result = runner.invoke(app, ["list files modified today", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert "files" in payload


def test_create_file_dry_run_shows_proposal_and_starts_no_process(
    mocker: MockerFixture, monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    run = mocker.patch("cmdora.executor.powershell.subprocess.run")

    result = runner.invoke(app, ["make a file named test.js", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run only — no command executed." in result.output
    assert 'New-Item -ItemType File -Path ".\\test.js"' in result.output
    assert "Creates a new empty JavaScript file" in result.output
    assert not (tmp_path / "test.js").exists()
    run.assert_not_called()


def test_create_file_request_creates_the_requested_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["make a file named test.js"])

    assert result.exit_code == 0
    assert (tmp_path / "test.js").is_file()


def test_cli_git_status_and_commit_requests_in_temporary_repository(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Cmdora Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "cmdora@example.test"], cwd=tmp_path, check=True)
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    status = runner.invoke(app, ["git status"])
    commit = runner.invoke(app, ["commit my latest changes with message add feature"])

    assert status.exit_code == 0
    assert "feature.txt" in status.output
    assert commit.exit_code == 0
    assert "add feature" in commit.output
    log = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert log.stdout.strip() == "add feature"
