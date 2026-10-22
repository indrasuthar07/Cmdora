"""Explicit deny rules for commands Cmdora must never allow."""

from __future__ import annotations

import re

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:remove-item|del|rd|rmdir)\b"), "File or directory deletion is blocked."),
    (
        re.compile(r"(?i)\b(?:format-volume|clear-disk)\b"),
        "Disk formatting or clearing is blocked.",
    ),
    (
        re.compile(r"(?i)\bset-executionpolicy\b"),
        "Changing PowerShell execution policy is blocked.",
    ),
    (re.compile(r"(?i)\binvoke-expression\b|\biex\b"), "Dynamic command evaluation is blocked."),
    (re.compile(r"(?i)\bstart-process\b"), "Starting another process is blocked."),
    (re.compile(r"(?i)\bcmd(?:\.exe)?\s+/c\b"), "cmd /c is blocked."),
    (
        re.compile(r"(?i)\bpowershell(?:\.exe)?\b[^\r\n]*\b-(?:encodedcommand|enc)\b"),
        "Encoded PowerShell commands are blocked.",
    ),
    (
        re.compile(
            r"(?i)\b(?:reg(?:\.exe)?|set-itemproperty|new-itemproperty|remove-itemproperty|"
            r"clear-itemproperty|rename-itemproperty)\b"
        ),
        "Registry modification is blocked.",
    ),
    (
        re.compile(r"(?i)\bnew-item\b[^\r\n]*\b(?:hklm:|hkcu:|registry::)"),
        "Registry modification is blocked.",
    ),
    (
        re.compile(
            r"(?i)\b(?:schtasks(?:\.exe)?|register-scheduledtask|new-scheduledtask|"
            r"set-scheduledtask|unregister-scheduledtask)\b"
        ),
        "Scheduled task changes are blocked.",
    ),
    (
        re.compile(r"(?i)\b(?:runas|start-process)\b[^\r\n]*\b(?:runas|administrator)\b"),
        "Administrator elevation is blocked.",
    ),
    (
        re.compile(
            r"(?i)\b(?:cmdkey|vaultcmd|get-credential|credentialmanager|"
            r"windowscredential|secretmanagement)\b"
        ),
        "Credential manager access is blocked.",
    ),
    (re.compile(r"(?i)(?:^|[\\/\s])\.ssh(?:[\\/\s]|$)"), "SSH key paths are blocked."),
    (re.compile(r"(?i)(?:^|[\\/\s])\.env(?:[.\\/\s]|$)"), "Environment files are blocked."),
    (
        re.compile(r"(?i)\bbrowser data\b|\\(?:chrome|edge|firefox)\\.*\b(?:cookies|login data)\b"),
        "Browser data access is blocked.",
    ),
    (re.compile(r"(?i)\bgit\s+reset\s+--hard\b"), "git reset --hard is blocked."),
    (re.compile(r"(?i)\bgit\s+clean\s+-[a-z]*f[a-z]*d[a-z]*\b"), "git clean -fd is blocked."),
    (re.compile(r"(?i)\bgit\s+push\b[^\r\n]*(?:--force\b|-f\b)"), "Force pushes are blocked."),
    (re.compile(r"(?i)\bgit\s+branch\s+-[dD]\b"), "Branch deletion is blocked."),
    (
        re.compile(r"(?i)\bgit\s+remote\s+(?:add|remove|rename|set-url)\b"),
        "Git remote changes are blocked.",
    ),
    (
        re.compile(
            r"(?i)\b(?:install-module|install-package)\b|"
            r"\b(?:winget|choco|scoop|npm|pnpm|yarn|pip|uv)\b"
            r"[^\r\n]*\b(?:install|add|upgrade|update)\b"
        ),
        "Package installation or upgrade is blocked.",
    ),
    (
        re.compile(
            r"(?i)\b(?:invoke-webrequest|invoke-restmethod|curl(?:\.exe)?|wget|iwr|irm)\b"
            r"|\b(?:downloadstring|downloadfile)\b"
        ),
        "Download-and-run patterns are blocked.",
    ),
)


def blocked_command_reason(command: str) -> str | None:
    """Return the first explicit reason why a command is prohibited."""
    for pattern, reason in _RULES:
        if pattern.search(command):
            return reason
    return None


__all__ = ["blocked_command_reason"]
