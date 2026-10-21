"""Path resolution helpers for workspace-bound operations."""

from pathlib import Path

from cmdora.workspace_paths import validate_workspace_path


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
    """Resolve a non-protected target inside the selected workspace."""
    return validate_workspace_path(workspace_path, target_path, must_exist=must_exist)
