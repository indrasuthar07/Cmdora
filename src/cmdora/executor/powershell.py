"""Bounded, workspace-confined PowerShell process execution."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_CHARACTERS = 8_000
_TRUNCATION_MARKER = "\n...[output truncated]"


@dataclass(frozen=True)
class ExecutionResult:
    """The complete, bounded result of one PowerShell invocation."""

    command: str
    workspace_path: Path
    stdout: str
    stderr: str
    exit_code: int | None
    elapsed_seconds: float
    timed_out: bool = False
    cancelled: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def succeeded(self) -> bool:
        """Return whether PowerShell exited successfully."""
        return not self.timed_out and not self.cancelled and self.exit_code == 0


class PowerShellExecutor:
    """Run PowerShell commands from exactly one selected workspace directory.

    This class intentionally does not validate commands. The safety policy introduced in a
    later phase must make that decision before it calls the executor.
    """

    def __init__(
        self,
        workspace_path: Path | str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_characters: int = DEFAULT_MAX_OUTPUT_CHARACTERS,
    ) -> None:
        self.workspace_path = _resolve_workspace(workspace_path)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_output_characters < 0:
            raise ValueError("max_output_characters cannot be negative")

        self.timeout_seconds = timeout_seconds
        self.max_output_characters = max_output_characters

    def execute(self, command: str) -> ExecutionResult:
        """Run one command without loading a PowerShell profile or using another directory."""
        if not command.strip():
            raise ValueError("command cannot be empty")

        started_at = monotonic()
        try:
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            elapsed_seconds = monotonic() - started_at
            stdout, stdout_truncated = _truncate_output(
                _coerce_output(error.stdout), self.max_output_characters
            )
            stderr, stderr_truncated = _truncate_output(
                _coerce_output(error.stderr), self.max_output_characters
            )
            return ExecutionResult(
                command=command,
                workspace_path=self.workspace_path,
                stdout=stdout,
                stderr=stderr,
                exit_code=None,
                elapsed_seconds=elapsed_seconds,
                timed_out=True,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        except KeyboardInterrupt:
            # subprocess.run terminates and waits for its child before propagating Ctrl+C.
            return ExecutionResult(
                command=command,
                workspace_path=self.workspace_path,
                stdout="",
                stderr="",
                exit_code=None,
                elapsed_seconds=monotonic() - started_at,
                cancelled=True,
            )

        stdout, stdout_truncated = _truncate_output(completed.stdout, self.max_output_characters)
        stderr, stderr_truncated = _truncate_output(completed.stderr, self.max_output_characters)
        return ExecutionResult(
            command=command,
            workspace_path=self.workspace_path,
            stdout=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            elapsed_seconds=monotonic() - started_at,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )


def _resolve_workspace(workspace_path: Path | str) -> Path:
    """Resolve and validate the only directory from which commands may run."""
    workspace = Path(workspace_path).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(f"Workspace path is not a directory: {workspace}")
    return workspace


def _coerce_output(output: str | bytes | None) -> str:
    """Normalize timeout output, which can be bytes despite text mode."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output


def _truncate_output(output: str, maximum_characters: int) -> tuple[str, bool]:
    """Keep stored process output at or below the configured character limit."""
    if len(output) <= maximum_characters:
        return output, False
    if maximum_characters <= len(_TRUNCATION_MARKER):
        return output[:maximum_characters], True
    return output[: maximum_characters - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER, True
