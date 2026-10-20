"""Shared secret and privacy redaction for audit data and model context."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

_REPLACEMENT = "[REDACTED]"
_HOME_REPLACEMENT = "[HOME]"

_PATTERNS = (
    # PEM-style SSH and other private keys, including their body and delimiters.
    re.compile(
        r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\b(?:AKIA|ASIA|A3T[A-Z])[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?i)(--?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
        r"password|passwd|secret)\s+)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(
        r"(?im)^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*)"
        r"(?:\"[^\r\n]*\"|'[^\r\n]*'|[^\s#\r\n]+)"
    ),
    re.compile(
        r"(?i)(\b[A-Z0-9_]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
        r"client[_-]?secret|password|passwd|secret)\b\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+(@)"),
)


def redact_sensitive_text(
    value: str, *, home_directories: Iterable[Path | str] | None = None
) -> str:
    """Remove secrets, dotenv values, private keys, and absolute home paths from text."""
    redacted = value
    for pattern in _PATTERNS:
        if "PRIVATE KEY" in pattern.pattern:
            redacted = pattern.sub(_REPLACEMENT, redacted)
        elif pattern.pattern.startswith("(?i)(://"):
            redacted = pattern.sub(r"\1" + _REPLACEMENT + r"\2", redacted)
        elif pattern.groups:
            redacted = pattern.sub(r"\1" + _REPLACEMENT, redacted)
        else:
            redacted = pattern.sub(_REPLACEMENT, redacted)
    return _redact_home_directories(redacted, home_directories)


def _redact_home_directories(value: str, home_directories: Iterable[Path | str] | None) -> str:
    directories = home_directories if home_directories is not None else _default_home_directories()
    redacted = value
    for directory in directories:
        path = str(directory).strip().rstrip("\\/")
        if not path:
            continue
        variants = {path, path.replace("\\", "/"), path.replace("/", "\\")}
        for variant in sorted(variants, key=len, reverse=True):
            redacted = re.sub(re.escape(variant), _HOME_REPLACEMENT, redacted, flags=re.IGNORECASE)
    return redacted


def _default_home_directories() -> set[Path | str]:
    directories: set[Path | str] = {Path.home()}
    for variable in ("USERPROFILE", "HOME"):
        value = os.environ.get(variable)
        if value:
            directories.add(value)
    return directories
