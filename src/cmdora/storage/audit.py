"""Append-only, redacted audit history stored in the user's data directory."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    """Redact common credentials from commands and process output."""
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
    """A single command or deterministic operation outcome."""

    policy_decision: str
    approval_status: str
    executed_command: str | None
    exit_code: int | None
    duration_seconds: float
    stdout: str
    stderr: str
    final_status: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if not self.policy_decision.strip():
            raise ValueError("policy_decision must not be empty")
        if not self.approval_status.strip():
            raise ValueError("approval_status must not be empty")
        if not self.final_status.strip():
            raise ValueError("final_status must not be empty")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds cannot be negative")
        if self.executed_command is not None:
            object.__setattr__(
                self, "executed_command", redact_sensitive_text(self.executed_command)
            )
        object.__setattr__(self, "stdout", redact_sensitive_text(self.stdout))
        object.__setattr__(self, "stderr", redact_sensitive_text(self.stderr))


class AuditLogError(ValueError):
    """Raised when the audit log cannot be read or written safely."""


def default_audit_path() -> Path:
    """Return the audit file path beside the user's application configuration."""
    return default_config_path().parent / "history.jsonl"


class AuditLog:
    """Persist and retrieve user-level command audit records."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path).expanduser() if path is not None else default_audit_path()

    def append(self, record: AuditRecord) -> None:
        """Append one already-redacted record as a JSON Lines entry."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as audit_file:
                audit_file.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
                audit_file.flush()
        except OSError as error:
            raise AuditLogError(f"Could not write audit history at {self.path}: {error}") from error

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest records, preserving chronological display order."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines if line.strip()]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise AuditLogError(f"Could not read audit history at {self.path}: {error}") from error
        return records[-limit:]
