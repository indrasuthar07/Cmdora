"""SQLite-backed, redacted audit history in the user's data directory."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import uuid4

from cmdora.config.app_config import default_config_path

_SECRET_PATTERNS = (
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(
        r"(?i)(\b[A-Z0-9_]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
        r"client[_-]?secret|password|passwd|secret)\b\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+(@)"),
)


def redact_sensitive_text(value: str) -> str:
    """Redact common credentials before audit data is persisted."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(?i)(://"):
            redacted = pattern.sub(r"\1[REDACTED]\2", redacted)
        elif pattern.groups:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


@dataclass(frozen=True)
class AuditRecord:
    """A single request's policy, approval, execution, and result details."""

    policy_decision: str
    approval_status: str
    executed_command: str | None
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str
    final_status: str
    request_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    user_request: str = ""
    workspace_identifier: str = ""
    llm_plan: str | None = None

    def __post_init__(self) -> None:
        if not self.policy_decision.strip():
            raise ValueError("policy_decision must not be empty")
        if not self.approval_status.strip():
            raise ValueError("approval_status must not be empty")
        if not self.final_status.strip():
            raise ValueError("final_status must not be empty")
        if not isfinite(self.duration_seconds) or self.duration_seconds < 0:
            raise ValueError("duration_seconds must be finite and cannot be negative")
        if not self.request_id.strip():
            raise ValueError("request_id must not be empty")
        if self.executed_command is not None:
            object.__setattr__(
                self, "executed_command", redact_sensitive_text(self.executed_command)
            )
        object.__setattr__(self, "user_request", redact_sensitive_text(self.user_request))
        if self.llm_plan is not None:
            object.__setattr__(self, "llm_plan", redact_sensitive_text(self.llm_plan))
        object.__setattr__(self, "stdout", redact_sensitive_text(self.stdout))
        object.__setattr__(self, "stderr", redact_sensitive_text(self.stderr))


class AuditLogError(ValueError):
    """Raised when the audit database cannot be read or written safely."""


def default_audit_path() -> Path:
    """Return `%APPDATA%/CmdoraAI/history.db` (or the platform config equivalent)."""
    return default_config_path().parent / "history.db"


class AuditLog:
    """Persist and retrieve user-level command audit records in SQLite."""

    _COLUMNS = (
        "request_id",
        "timestamp",
        "user_request",
        "workspace_identifier",
        "llm_plan",
        "policy_decision",
        "approval_status",
        "executed_command",
        "exit_code",
        "duration_seconds",
        "stdout",
        "stderr",
        "final_status",
    )

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else default_audit_path()

    def append(self, record: AuditRecord) -> None:
        """Create the schema if needed, then transactionally insert one record."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5)) as connection:
                with connection:
                    self._create_schema(connection)
                    connection.execute(
                        """INSERT INTO audit_records (
                            request_id, timestamp, user_request, workspace_identifier,
                            llm_plan, policy_decision, approval_status, executed_command,
                            exit_code, duration_seconds, stdout, stderr, final_status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            record.request_id,
                            record.timestamp,
                            record.user_request,
                            record.workspace_identifier,
                            record.llm_plan,
                            record.policy_decision,
                            record.approval_status,
                            record.executed_command,
                            record.exit_code,
                            record.duration_seconds,
                            record.stdout,
                            record.stderr,
                            record.final_status,
                        ),
                    )
        except (OSError, sqlite3.Error) as error:
            raise AuditLogError(f"Could not write audit history at {self.path}: {error}") from error

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest records, preserving chronological display order."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if not self.path.exists():
            return []
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                self._create_schema(connection)
                rows = connection.execute(
                    f"SELECT {', '.join(self._COLUMNS)} FROM audit_records "
                    "ORDER BY rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in reversed(rows)]
        except (OSError, sqlite3.Error) as error:
            raise AuditLogError(f"Could not read audit history at {self.path}: {error}") from error

    def by_request_id(self, request_id: str) -> list[dict[str, Any]]:
        """Return every command record associated with one exact request ID."""
        if not request_id.strip():
            raise ValueError("request_id must not be empty")
        if not self.path.exists():
            return []
        try:
            with closing(sqlite3.connect(self.path, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    f"SELECT {', '.join(self._COLUMNS)} FROM audit_records "
                    "WHERE request_id = ? ORDER BY rowid ASC",
                    (request_id,),
                ).fetchall()
            return [dict(row) for row in rows]
        except (OSError, sqlite3.Error) as error:
            raise AuditLogError(
                f"Could not read audit history for request {request_id}: {error}"
            ) from error

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS audit_records (
                record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                user_request TEXT NOT NULL,
                workspace_identifier TEXT NOT NULL,
                llm_plan TEXT,
                policy_decision TEXT NOT NULL,
                approval_status TEXT NOT NULL,
                executed_command TEXT,
                exit_code INTEGER,
                duration_seconds REAL NOT NULL,
                stdout TEXT NOT NULL,
                stderr TEXT NOT NULL,
                final_status TEXT NOT NULL
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_records_timestamp ON audit_records(timestamp)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_records_request_id ON audit_records(request_id)"
        )
