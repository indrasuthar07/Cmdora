"""Read-only Git preflight checks used by the local safety policy."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitRepositoryState:
    """A small, read-only snapshot of the selected repository."""

    is_repository: bool
    has_changes: bool
    staged_files: tuple[str, ...]
    branch: str | None
    origin_url: str | None
    error: str | None = None


@dataclass(frozen=True)
class GitValidation:
    """The result of one Git safety preflight."""

    allowed: bool
    reasons: tuple[str, ...]
    state: GitRepositoryState


class GitSafetyValidator:
    """Inspect Git state without invoking a shell or changing repository data."""

    def __init__(self, workspace_path: Path | str, *, timeout_seconds: float = 5.0) -> None:
        self._workspace = Path(workspace_path).expanduser().resolve(strict=True)
        self._timeout_seconds = timeout_seconds

    def inspect(self) -> GitRepositoryState:
        """Return the repository facts required before commit or push."""
        inside = self._run("rev-parse", "--is-inside-work-tree")
        if inside is None:
            return _not_repository("Git was unavailable while checking the selected workspace.")
        if inside.returncode != 0 or inside.stdout.strip().casefold() != "true":
            return _not_repository("The selected workspace is not a Git repository.")

        status = self._run("status", "--porcelain")
        staged = self._run("diff", "--cached", "--name-only")
        branch = self._run("symbolic-ref", "--quiet", "--short", "HEAD")
        origin = self._run("remote", "get-url", "origin")
        if status is None or staged is None or branch is None or origin is None:
            return GitRepositoryState(
                is_repository=True,
                has_changes=False,
                staged_files=(),
                branch=None,
                origin_url=None,
                error="Git timed out while reading repository state.",
            )

        return GitRepositoryState(
            is_repository=True,
            has_changes=bool(status.stdout.strip()),
            staged_files=tuple(line for line in staged.stdout.splitlines() if line.strip()),
            branch=branch.stdout.strip() if branch.returncode == 0 else None,
            origin_url=origin.stdout.strip() if origin.returncode == 0 else None,
        )

    def validate_commit(self, message: str | None, *, autonomy_mode: str = "off") -> GitValidation:
        """Ensure a commit has repository changes, staged files, and a supplied message."""
        state = self.inspect()
        reasons: list[str] = []
        if not state.is_repository:
            reasons.append("The selected workspace is not a Git repository.")
        if state.error:
            reasons.append(state.error)
        if message is None or not message.strip():
            if autonomy_mode.casefold() != "off":
                reasons.append("Autonomous mode cannot fabricate a commit message.")
            else:
                reasons.append("Git commit requires a non-empty user-provided message.")
        if state.is_repository and not state.has_changes:
            reasons.append("There are no repository changes to commit.")
        if state.is_repository and not state.staged_files:
            reasons.append("No files are staged for commit.")
        if state.staged_files:
            reasons.append("Staged files: " + ", ".join(state.staged_files))
        return GitValidation(reasons_except_staged(reasons), tuple(reasons), state)

    def validate_push(
        self,
        *,
        explicit_push_request: bool,
        protected_branches: set[str],
        expected_origin_url: str | None = None,
        expected_branch: str | None = None,
    ) -> GitValidation:
        """Ensure a push targets an unchanged ``origin`` from a safe branch."""
        state = self.inspect()
        reasons: list[str] = []
        if not state.is_repository:
            reasons.append("The selected workspace is not a Git repository.")
        if state.error:
            reasons.append(state.error)
        if not explicit_push_request:
            reasons.append("A push requires an explicit user request mentioning push.")
        if state.is_repository and not state.origin_url:
            reasons.append("The repository has no configured 'origin' remote.")
        if expected_origin_url is not None and state.origin_url != expected_origin_url:
            reasons.append("The origin remote URL changed after planning; push is blocked.")
        if state.branch is None:
            reasons.append("Cannot push while HEAD is detached.")
        elif state.branch.casefold() in {branch.casefold() for branch in protected_branches}:
            reasons.append(f"Pushes to protected branch '{state.branch}' are blocked.")
        if expected_branch is not None and state.branch != expected_branch:
            reasons.append("The current branch changed after planning; push is blocked.")
        if reasons:
            return GitValidation(False, tuple(reasons), state)
        return GitValidation(
            True,
            ("origin, branch, and remote URL were verified for this push.",),
            state,
        )

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", *arguments],
                cwd=self._workspace,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None


def reasons_except_staged(reasons: list[str]) -> bool:
    """A staged-file listing is informative; every other commit reason is a denial."""
    return all(reason.startswith("Staged files:") for reason in reasons)


def _not_repository(error: str) -> GitRepositoryState:
    return GitRepositoryState(False, False, (), None, None, error)


__all__ = ["GitRepositoryState", "GitSafetyValidator", "GitValidation"]
