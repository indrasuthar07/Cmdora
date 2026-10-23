"""Tests for the Cmdora command-line interface."""

import json
import subprocess
import tomllib
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


def test_config_init_creates_default_user_configuration(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path))

    result = runner.invoke(app, ["config", "init"])

    assert result.exit_code == 0
    config_path = tmp_path / "CmdoraAI" / "config.toml"
    assert config_path.is_file()
    assert "Created user configuration" in result.output
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == {
        "model": "gemini-3.8-flash",
        "default_timeout_seconds": 30,
        "max_output_characters": 8000,
        "default_autonomy_mode": "off",
        "protected_branches": ["main", "master"],
    }


def test_config_init_project_creates_workspace_scoped_configuration(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["config", "init", "--project"])

    assert result.exit_code == 0
    config_path = tmp_path / ".Cmdora" / "config.toml"
    assert tomllib.loads(config_path.read_text(encoding="utf-8")) == {
        "autonomy_mode": "off",
        "allowed_write_directories": ["src", "tests"],
        "protected_branches": ["main", "master"],
    }
    assert "Created project configuration" in result.output


def test_history_command_shows_empty_state() -> None:
    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0
    assert "No command history yet." in result.output


def test_history_command_displays_sqlite_audit_fields() -> None:
    from cmdora.storage import AuditLog, AuditRecord

    AuditLog().append(
        AuditRecord(
            policy_decision="allowed",
            approval_status="not_required",
            executed_command="git status --short --branch",
            exit_code=0,
            duration_seconds=0.125,
            stdout="## main",
            stderr="",
            final_status="succeeded",
            user_request="show git status",
            workspace_identifier="C:/workspace/project",
            llm_plan=None,
        )
    )

    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0
    assert "Request ID:" in result.output
    assert "Request: show git status" in result.output
    assert "Workspace: C:/workspace/project" in result.output
    assert "Command: git status --short --branch" in result.output
    assert "policy: allowed" in result.output
    assert "not_required" in result.output
    assert "Exit: 0" in result.output
    assert "Duration: 0.125s" in result.output
    assert "stdout: ## main" in result.output


def test_history_last_limits_records_to_the_most_recent_number() -> None:
    from cmdora.storage import AuditLog, AuditRecord

    log = AuditLog()
    for index in range(3):
        log.append(
            AuditRecord(
                policy_decision="allowed",
                approval_status="not_required",
                executed_command=f"command-{index}",
                exit_code=0,
                duration_seconds=0.01,
                stdout="",
                stderr="",
                final_status="succeeded",
                request_id=f"request-{index}",
                user_request=f"request text {index}",
            )
        )

    result = runner.invoke(app, ["history", "--last", "2"])

    assert result.exit_code == 0
    assert "request text 0" not in result.output
    assert "request text 1" in result.output
    assert "request text 2" in result.output


def test_history_show_displays_all_events_for_exact_request_id() -> None:
    from cmdora.storage import AuditLog, AuditRecord

    log = AuditLog()
    for request_id, command in (
        ("shared-request-id", "git add --all"),
        ("shared-request-id", "git commit -m update"),
        ("other-request-id", "git status"),
    ):
        log.append(
            AuditRecord(
                policy_decision="allowed",
                approval_status="not_required",
                executed_command=command,
                exit_code=0,
                duration_seconds=0.01,
                stdout="",
                stderr="",
                final_status="succeeded",
                request_id=request_id,
                user_request="commit my changes",
            )
        )

    result = runner.invoke(app, ["history", "show", "shared-request-id"])

    assert result.exit_code == 0
    assert "git add --all" in result.output
    assert "git commit -m update" in result.output
    assert "git status" not in result.output


def test_history_show_reports_unknown_request_id() -> None:
    result = runner.invoke(app, ["history", "show", "unknown-id"])

    assert result.exit_code == 1
    assert "No history found for request ID: unknown-id" in result.output


def test_history_last_must_be_positive() -> None:
    result = runner.invoke(app, ["history", "--last", "0"])

    assert result.exit_code != 0


def test_natural_language_request_accepts_execution_flags(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    from cmdora.config import RepositoryConfigStore

    monkeypatch.chdir(tmp_path)
    RepositoryConfigStore(tmp_path).set_autonomy_mode("git")
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


def test_autonomy_consent_is_scoped_to_the_current_repository(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    trusted_repository = tmp_path / "trusted"
    untrusted_repository = tmp_path / "untrusted"
    trusted_repository.mkdir()
    untrusted_repository.mkdir()
    from cmdora.config import RepositoryConfigStore

    RepositoryConfigStore(trusted_repository).set_autonomy_mode("git")

    monkeypatch.chdir(trusted_repository)
    trusted_result = runner.invoke(
        app, ["list files modified today", "--dry-run", "--autonomy", "git"]
    )
    monkeypatch.chdir(untrusted_repository)
    untrusted_result = runner.invoke(
        app, ["list files modified today", "--dry-run", "--autonomy", "git"]
    )

    assert trusted_result.exit_code == 0
    assert "Autonomy: git mode enabled for repository trusted" in trusted_result.output
    assert untrusted_result.exit_code == 1
    assert "not enabled for this repository" in untrusted_result.output


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

    result = runner.invoke(app, ["make a file named test.js"], input="y\n")

    assert result.exit_code == 0
    assert "Request:" in result.output
    assert "Plan:" in result.output
    assert "Risk:\nCaution" in result.output
    assert "Proceed? [y/N]" in result.output
    assert (tmp_path / "test.js").is_file()


def test_cli_git_status_and_commit_requests_in_temporary_repository(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Cmdora Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "cmdora@example.test"], cwd=tmp_path, check=True)
    (tmp_path / "feature.txt").write_text("feature\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    status = runner.invoke(app, ["git status"])
    commit = runner.invoke(app, ["commit my latest changes with message add feature"], input="y\n")

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

    from cmdora.storage import AuditLog

    records = AuditLog(tmp_path / "app-data" / "CmdoraAI" / "history.db").recent()
    assert [record["executed_command"] for record in records] == [
        "git status --short --branch",
        "git commit -m add feature",
    ]
    assert all(record["policy_decision"] == "allowed" for record in records)
    assert [record["approval_status"] for record in records] == ["not_required", "approved_once"]
    assert all(record["user_request"] for record in records)
    assert all(record["workspace_identifier"] == str(tmp_path) for record in records)
    assert all(record["llm_plan"] is None for record in records)
    assert records[0]["request_id"] != records[1]["request_id"]


def test_create_file_approval_cancel_and_dry_run_do_not_create_a_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    cancelled = runner.invoke(app, ["make a file named cancelled.js"], input="n\n")
    dry_run = runner.invoke(app, ["make a file named preview.js"], input="d\n")

    assert cancelled.exit_code == 0
    assert "Cancelled — no command executed." in cancelled.output
    assert not (tmp_path / "cancelled.js").exists()
    assert dry_run.exit_code == 0
    assert "Dry run only — no command executed." in dry_run.output
    assert not (tmp_path / "preview.js").exists()


def test_approval_can_enable_repository_scoped_git_autonomy(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["make a file named consented.js"], input="a\ny\n")

    assert result.exit_code == 0
    assert "You are enabling Git automation for this repository." in result.output
    assert "constrained git push" in result.output
    assert "Continue? [y/N]" in result.output
    assert "Git autonomy enabled for this repository only" in result.output
    assert (tmp_path / "consented.js").is_file()
    config = tomllib.loads((tmp_path / ".Cmdora" / "config.toml").read_text(encoding="utf-8"))
    assert config["autonomy_mode"] == "git"


def test_declining_session_autonomy_confirmation_cancels_without_saving_consent(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["make a file named declined.js"], input="a\nn\n")

    assert result.exit_code == 0
    assert "Cancelled — no command executed." in result.output
    assert not (tmp_path / "declined.js").exists()
    assert not (tmp_path / ".Cmdora" / "config.toml").exists()


def test_ctrl_c_during_approval_records_cancellation(
    monkeypatch: MonkeyPatch, mocker: MockerFixture, tmp_path: Path
) -> None:
    from cmdora.storage import AuditLog

    monkeypatch.chdir(tmp_path)
    mocker.patch("cmdora.cli.main.prompt_for_approval", side_effect=KeyboardInterrupt)

    result = runner.invoke(app, ["make a file named interrupted.js"])

    assert result.exit_code == 130
    assert "Cancelled — no subsequent action was executed." in result.output
    assert not (tmp_path / "interrupted.js").exists()
    record = AuditLog(tmp_path / "app-data" / "CmdoraAI" / "history.db").recent()[0]
    assert record["final_status"] == "cancelled"
    assert record["approval_status"] == "cancelled"


def test_ctrl_c_during_autonomy_session_confirmation_does_not_save_consent(
    monkeypatch: MonkeyPatch, mocker: MockerFixture, tmp_path: Path
) -> None:
    from cmdora.storage import AuditLog

    monkeypatch.chdir(tmp_path)
    mocker.patch("cmdora.cli.main.confirm_git_autonomy", side_effect=KeyboardInterrupt)

    result = runner.invoke(app, ["make a file named session-interrupted.js"], input="a\n")

    assert result.exit_code == 130
    assert not (tmp_path / "session-interrupted.js").exists()
    assert not (tmp_path / ".Cmdora" / "config.toml").exists()
    assert AuditLog(tmp_path / "app-data" / "CmdoraAI" / "history.db").recent()[0][
        "final_status"
    ] == "cancelled"


def test_git_autonomy_runs_allowlisted_create_without_an_interactive_prompt(
    monkeypatch: MonkeyPatch, mocker: MockerFixture, tmp_path: Path
) -> None:
    from cmdora.config import RepositoryConfigStore
    from cmdora.storage import AuditLog

    monkeypatch.chdir(tmp_path)
    RepositoryConfigStore(tmp_path).set_autonomy_mode("git")
    prompt = mocker.patch("cmdora.cli.main.prompt_for_approval")

    result = runner.invoke(
        app,
        ["make a file named automated.js", "--autonomy", "git"],
    )

    assert result.exit_code == 0
    assert "Autonomy: git mode enabled for repository" in result.output
    assert (tmp_path / "automated.js").is_file()
    prompt.assert_not_called()
    assert AuditLog(tmp_path / "app-data" / "CmdoraAI" / "history.db").recent()[0][
        "approval_status"
    ] == "autonomous"


def test_invalid_autonomy_mode_is_rejected(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["list files modified today", "--autonomy", "all"])

    assert result.exit_code == 1
    assert "must be either 'off' or 'git'" in result.output


def test_cli_blocks_an_unstaged_commit_before_calling_git(
    monkeypatch: MonkeyPatch, mocker: MockerFixture, tmp_path: Path
) -> None:
    subprocess.run(["git", "init", "-b", "feature/safety"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Cmdora Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "cmdora@example.test"], cwd=tmp_path, check=True)
    (tmp_path / "unstaged.txt").write_text("not staged\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    commit = mocker.patch("cmdora.executor.operations.DeterministicOperations.git_commit")

    result = runner.invoke(app, ["commit my changes with message should not run"])

    assert result.exit_code == 1
    assert "Blocked:" in result.output
    assert "No files are staged" in result.output
    commit.assert_not_called()
