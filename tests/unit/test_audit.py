"""Tests for redacted audit history storage."""

import sqlite3
from pathlib import Path

import pytest

from cmdora.storage import AuditLog, AuditRecord, redact_sensitive_text


def test_audit_record_redacts_secrets_from_command_and_output() -> None:
    record = AuditRecord(
        policy_decision="allowed",
        approval_status="not_required",
        executed_command='tool --api-key="secret-value"',
        exit_code=0,
        duration_seconds=0.125,
        stdout="Authorization: Bearer abc.def.ghi password=hunter2",
        stderr="https://user:secret@example.com",
        final_status="succeeded",
        user_request="run with api_key=hidden",
        llm_plan='{"command": "use password=hidden"}',
    )

    assert "secret-value" not in (record.executed_command or "")
    assert "abc.def.ghi" not in record.stdout
    assert "hunter2" not in record.stdout
    assert "user:secret" not in record.stderr
    assert "hidden" not in record.user_request
    assert "hidden" not in (record.llm_plan or "")


def test_audit_log_persists_all_required_fields_in_sqlite_and_returns_recent_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "CmdoraAI" / "history.db"
    log = AuditLog(path)
    record = AuditRecord(
        policy_decision="allowed",
        approval_status="not_required",
        executed_command="git status --short --branch",
        exit_code=0,
        duration_seconds=0.25,
        stdout="## main",
        stderr="",
        final_status="succeeded",
        user_request="show git status",
        workspace_identifier=str(tmp_path),
        llm_plan=None,
    )

    log.append(record)

    assert path.is_file()
    with sqlite3.connect(path) as connection:
        columns = [
            column[1]
            for column in connection.execute("PRAGMA table_info(audit_records)")
            if column[1] != "record_id"
        ]
        stored = dict(
            zip(
                columns,
                connection.execute(
                    "SELECT " + ", ".join(columns) + " FROM audit_records"
                ).fetchone(),
                strict=True,
            )
        )
    assert stored["request_id"] == record.request_id
    assert stored["timestamp"] == record.timestamp
    assert stored["user_request"] == "show git status"
    assert stored["workspace_identifier"] == str(tmp_path)
    assert stored["llm_plan"] is None
    assert stored["policy_decision"] == "allowed"
    assert stored["approval_status"] == "not_required"
    assert stored["executed_command"] == "git status --short --branch"
    assert stored["exit_code"] == 0
    assert stored["duration_seconds"] == 0.25
    assert stored["stdout"] == "## main"
    assert stored["stderr"] == ""
    assert stored["final_status"] == "succeeded"
    assert log.recent() == [stored]


def test_audit_log_recent_limit_returns_newest_in_chronological_order(
    tmp_path: Path,
) -> None:
    log = AuditLog(tmp_path / "history.db")
    for request in ("first", "second", "third"):
        log.append(
            AuditRecord(
                policy_decision="allowed",
                approval_status="not_required",
                executed_command=None,
                exit_code=None,
                duration_seconds=0,
                stdout="",
                stderr="",
                final_status="dry_run",
                user_request=request,
            )
        )

    assert [record["user_request"] for record in log.recent(limit=2)] == ["second", "third"]


def test_redactor_masks_common_provider_tokens() -> None:
    secret = "ghp_" + "a" * 32

    assert redact_sensitive_text(f"token={secret}") == "token=[REDACTED]"


def test_audit_record_rejects_negative_duration() -> None:
    with pytest.raises(ValueError, match="duration_seconds"):
        AuditRecord(
            policy_decision="allowed",
            approval_status="not_required",
            executed_command=None,
            exit_code=None,
            duration_seconds=-0.1,
            stdout="",
            stderr="",
            final_status="dry_run",
        )
