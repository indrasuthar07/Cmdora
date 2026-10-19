"""Path resolution helpers for workspace-bound operations."""

from pathlib import Path


def resolve_workspace(workspace_path: Path | str) -> Path:
    """Resolve and validate an existing workspace directory."""
    workspace = Path(workspace_path).expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise NotADirectoryError(f"Workspace path is not a directory: {workspace}")
    return workspace


def resolve_workspace_path(
    workspace_path: Path,
    target_path: Path | str,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve a target and reject it if it escapes the selected workspace."""
    target = Path(target_path).expanduser()
    if not target.is_absolute():
        target = workspace_path / target

    resolved = target.resolve(strict=must_exist)
    if not resolved.is_relative_to(workspace_path):
        raise ValueError(f"Path is outside the selected workspace: {target_path}")
    return resolved
