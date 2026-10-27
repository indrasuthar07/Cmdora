"""Deterministic file and Git operations scoped to one workspace."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from time import monotonic

from cmdora.config import RepositoryConfigStore, validate_repository_write_path
from cmdora.executor.powershell import (
    DEFAULT_MAX_OUTPUT_CHARACTERS,
    DEFAULT_TIMEOUT_SECONDS,
    coerce_output,
    truncate_output,
)
from cmdora.executor.workspace import resolve_workspace, resolve_workspace_path


@dataclass(frozen=True)
class CommandResult:
    """Captured result from a deterministic Git command."""

    command: tuple[str, ...]
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
        """Whether the process completed successfully."""
        return not self.timed_out and not self.cancelled and self.exit_code == 0


@dataclass(frozen=True)
class ModifiedFile:
    """A workspace file modified since the start of the local day."""

    path: Path
    modified_at: datetime
    size_bytes: int


class DeterministicOperations:
    """Provide filesystem and Git operations rooted at a selected workspace."""

    _IGNORED_DIRECTORIES = frozenset(
        {
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "venv",
            "env",
            "node_modules",
            "__pycache__",
        }
    )

    def __init__(
        self,
        workspace_path: Path | str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_characters: int = DEFAULT_MAX_OUTPUT_CHARACTERS,
        ignored_directories: Collection[str] = (),
    ) -> None:
        self.workspace_path = resolve_workspace(workspace_path)
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_output_characters < 0:
            raise ValueError("max_output_characters cannot be negative")

        self.timeout_seconds = timeout_seconds
        self.max_output_characters = max_output_characters
        self.ignored_directories = frozenset(
            {
                *self._IGNORED_DIRECTORIES,
                *(directory.casefold() for directory in ignored_directories),
            }
        )

    def create_file(self, path: Path | str) -> Path:
        """Create an empty file within the workspace without overwriting anything."""
        repository_config = RepositoryConfigStore(self.workspace_path).load()
        target = validate_repository_write_path(self.workspace_path, path, repository_config)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"File already exists: {target}")

        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation makes an existing file or a concurrent creation fail safely.
        with target.open("x", encoding="utf-8"):
            pass
        return target

    def list_modified_today(self) -> list[ModifiedFile]:
        """List workspace files modified since local midnight, newest first."""
        now = datetime.now().astimezone()
        start_of_today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        start_of_tomorrow = start_of_today + timedelta(days=1)
        modified_files: list[ModifiedFile] = []

        for current_directory, directory_names, file_names in os.walk(
            self.workspace_path, topdown=True, followlinks=False
        ):
            current_path = Path(current_directory)
            directory_names[:] = [
                name
                for name in directory_names
                if name.casefold() not in self.ignored_directories
                and not (current_path / name).is_symlink()
            ]

            for name in file_names:
                candidate = current_path / name
                if candidate.is_symlink():
                    continue

                try:
                    stat = candidate.stat()
                except OSError:
                    # A file can disappear or become unreadable during a scan.
                    continue

                modified_at = datetime.fromtimestamp(stat.st_mtime).astimezone()
                if start_of_today <= modified_at < start_of_tomorrow:
                    modified_files.append(
                        ModifiedFile(
                            path=candidate.relative_to(self.workspace_path),
                            modified_at=modified_at,
                            size_bytes=stat.st_size,
                        )
                    )

        return sorted(modified_files, key=lambda item: item.modified_at, reverse=True)

    def find_files(self, extension_or_pattern: str = "*") -> list[Path]:
        """List workspace files matching a pattern or extension, respecting ignored directories."""
        pattern = extension_or_pattern.strip()
        if not pattern.startswith("*"):
            if pattern.startswith("."):
                pattern = f"*{pattern}"
            else:
                pattern = f"*.{pattern}"

        matches: list[Path] = []
        for current_directory, directory_names, file_names in os.walk(
            self.workspace_path, topdown=True, followlinks=False
        ):
            current_path = Path(current_directory)
            directory_names[:] = [
                name
                for name in directory_names
                if name.casefold() not in self.ignored_directories
                and not (current_path / name).is_symlink()
            ]

            for name in file_names:
                candidate = current_path / name
                if candidate.is_symlink():
                    continue
                relative = candidate.relative_to(self.workspace_path)
                ext = pattern.removeprefix("*").casefold()
                if candidate.match(pattern) or name.casefold().endswith(ext):
                    matches.append(relative)

        return sorted(matches)

    def git_status(self) -> CommandResult:
        """Return the current branch and concise working-tree status."""
        return self._run_git(("status", "--short", "--branch"))

    def git_status_short(self) -> CommandResult:
        """Return only porcelain-style changed-file entries for a workflow preflight."""
        return self._run_git(("status", "--short"))

    def git_add(self, files: Sequence[Path | str]) -> CommandResult:
        """Stage only the explicitly named workspace paths."""
        if not files:
            raise ValueError("At least one file path is required")

        pathspecs: list[str] = []
        for file_path in files:
            resolved = resolve_workspace_path(self.workspace_path, file_path)
            if resolved == self.workspace_path:
                raise ValueError("Cannot stage the workspace root; provide explicit file paths")
            relative_path = resolved.relative_to(self.workspace_path).as_posix()
            # Literal pathspecs prevent Git's wildcard and pathspec-magic interpretation.
            pathspecs.append(f":(literal){relative_path}")

        return self._run_git(("add", "--", *pathspecs))

    def git_add_all(self) -> CommandResult:
        """Stage all non-ignored changes under the selected workspace."""
        return self._run_git(("add", "--all", "--", "."))

    def git_commit(self, message: str) -> CommandResult:
        """Commit the currently staged changes with the supplied message."""
        if not message.strip():
            raise ValueError("Commit message cannot be empty")
        return self._run_git(("commit", "-m", message))

    def git_head_short(self) -> CommandResult:
        """Return the newly created commit's abbreviated object ID."""
        return self._run_git(("rev-parse", "--short", "HEAD"))

    def git_push(self) -> CommandResult:
        """Push the current branch to the origin remote."""
        return self._run_git(
            ("push", "origin", "HEAD"),
            disable_interactive_prompts=True,
        )

    def _run_git(
        self,
        arguments: tuple[str, ...],
        *,
        disable_interactive_prompts: bool = False,
    ) -> CommandResult:
        command = ("git", *arguments)
        environment = None
        if disable_interactive_prompts:
            environment = os.environ.copy()
            environment["GIT_TERMINAL_PROMPT"] = "0"
            environment["GCM_INTERACTIVE"] = "Never"

        started_at = monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                stdin=subprocess.DEVNULL,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            stdout, stdout_truncated = truncate_output(
                coerce_output(error.stdout), self.max_output_characters
            )
            stderr, stderr_truncated = truncate_output(
                coerce_output(error.stderr), self.max_output_characters
            )
            return CommandResult(
                command=command,
                workspace_path=self.workspace_path,
                stdout=stdout,
                stderr=stderr,
                exit_code=None,
                elapsed_seconds=monotonic() - started_at,
                timed_out=True,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )
        except KeyboardInterrupt:
            return CommandResult(
                command=command,
                workspace_path=self.workspace_path,
                stdout="",
                stderr="",
                exit_code=None,
                elapsed_seconds=monotonic() - started_at,
                cancelled=True,
            )

        stdout, stdout_truncated = truncate_output(completed.stdout, self.max_output_characters)
        stderr, stderr_truncated = truncate_output(completed.stderr, self.max_output_characters)
        return CommandResult(
            command=command,
            workspace_path=self.workspace_path,
            stdout=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            elapsed_seconds=monotonic() - started_at,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
