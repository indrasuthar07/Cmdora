"""Safety-level re-export of shared workspace path validation."""

from cmdora.workspace_paths import UnsafePathError, validate_workspace_path

__all__ = ["UnsafePathError", "validate_workspace_path"]
