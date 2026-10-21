"""Workspace path validation shared by configuration, execution, and safety layers."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


class UnsafePathError(ValueError):
    """A target violates Cmdora's workspace or protected-path rules."""


_PROTECTED_COMPONENTS = frozenset({".cmdora", ".git", ".ssh"})
_PROTECTED_FILENAMES = frozenset({".env"})
_PROTECTED_PREFIXES = (".env.",)


def validate_workspace_path(
    workspace_path: Path | str,
    target_path: Path | str,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve a target inside one workspace and reject traversal or protected paths."""
    workspace = Path(workspace_path).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(f"Workspace path is not a directory: {workspace}")

    original = str(target_path).strip()
    if not original:
        raise UnsafePathError("A non-empty workspace path is required")
    windows_path = PureWindowsPath(original)
    local_path = Path(original)
    if ".." in windows_path.parts or ".." in local_path.parts:
        raise UnsafePathError(f"Path traversal is not allowed: {target_path}")

    candidate = local_path.expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(workspace):
        raise UnsafePathError(f"Path is outside the selected workspace: {target_path}")

    relative_parts = tuple(part.casefold() for part in resolved.relative_to(workspace).parts)
    if any(part in _PROTECTED_COMPONENTS for part in relative_parts):
        raise UnsafePathError("Access to protected workspace metadata is not allowed")
    if relative_parts:
        filename = relative_parts[-1]
        if filename in _PROTECTED_FILENAMES or filename.startswith(_PROTECTED_PREFIXES):
            raise UnsafePathError(
                "Access to secret or protected configuration files is not allowed"
            )
    return resolved


__all__ = ["UnsafePathError", "validate_workspace_path"]
