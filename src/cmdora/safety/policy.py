"""Command categories and a conservative allowlist for proposed shell commands."""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cmdora.config import RepositoryConfig, RepositoryConfigStore, validate_repository_write_path
from cmdora.safety.block_rules import blocked_command_reason
from cmdora.safety.git_safety import GitSafetyValidator
from cmdora.safety.paths import UnsafePathError, validate_workspace_path
from cmdora.safety.redaction import redact_sensitive_text


class CommandCategory(StrEnum):
    """Local risk class assigned to a proposed command."""

    READ_ONLY = "READ_ONLY"
    WORKSPACE_CREATE = "WORKSPACE_CREATE"
    WORKSPACE_WRITE = "WORKSPACE_WRITE"
    GIT_STAGE = "GIT_STAGE"
    GIT_COMMIT = "GIT_COMMIT"
    GIT_PUSH = "GIT_PUSH"
    NETWORK = "NETWORK"
    DESTRUCTIVE = "DESTRUCTIVE"
    BLOCKED = "BLOCKED"


class PolicyDecision(BaseModel):
    """A serializable, user-facing result of a local policy evaluation."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    risk_level: Literal["safe", "caution", "high", "blocked"]
    requires_confirmation: bool
    reasons: list[str] = Field(min_length=1)
    sanitized_command: str | None

    @model_validator(mode="after")
    def _validate_consistency(self) -> PolicyDecision:
        if self.risk_level == "blocked" and self.allowed:
            raise ValueError("Blocked decisions cannot be allowed.")
        if not self.allowed and self.requires_confirmation:
            raise ValueError("Blocked decisions do not request confirmation.")
        if (
            self.allowed
            and self.risk_level in {"caution", "high"}
            and not self.requires_confirmation
        ):
            raise ValueError("Caution and high-risk actions require confirmation.")
        return self


@dataclass(frozen=True)
class CommandAssessment:
    """Classification and local allowlist result for one proposed command."""

    category: CommandCategory
    decision: PolicyDecision

    @property
    def allowed(self) -> bool:
        """Compatibility shortcut for the structured policy decision."""
        return self.decision.allowed

    @property
    def reason(self) -> str:
        """The primary policy reason, retained for existing callers."""
        return self.decision.reasons[0]

    @property
    def command(self) -> str:
        """The redacted command, or an empty value when unavailable."""
        return self.decision.sanitized_command or ""


_DESTRUCTIVE_COMMANDS = {
    "clear-disk",
    "clear-volume",
    "format-volume",
    "remove-item",
    "stop-computer",
    "stop-process",
}
_WRITE_COMMANDS = {"add-content", "clear-content", "copy-item", "move-item", "set-content"}
_NETWORK_COMMANDS = {
    "invoke-restmethod",
    "invoke-webrequest",
    "start-bitstransfer",
    "curl",
    "wget",
}
_READ_COMMANDS = {"get-childitem", "get-content", "get-item"}
_SECRET_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials",
    "id_rsa",
    "id_ed25519",
    "secrets",
    "token.json",
    "service-account.json",
}
_SECRET_SUFFIXES = {".key", ".pem", ".pfx", ".p12"}
_DESTRUCTIVE_GIT_COMMANDS = {"clean", "reset"}
_SHELL_METACHARACTERS = re.compile(r"[|;&<>`(){}]")
_DATE_FILTERED_CHILD_ITEM = re.compile(
    r"(?is)^\s*Get-ChildItem\s+-File\s+-Recurse\s+-Path\s+"
    r"(?P<path>\"[^\"$`]*\"|'[^'$`]*'|[^\s|;&<>`(){}]+)\s*\|\s*"
    r"Where-Object\s*\{\s*\$_\.LastWriteTime\s+-ge\s*"
    r"\(Get-Date\)\.Date\s*\}\s*$"
)


def assess_command(
    command: str,
    workspace_path: Path | str,
    *,
    approved_read_files: Iterable[Path | str] = (),
    approved_read_directories: Iterable[Path | str] = (),
    current_branch: str | None = None,
    protected_branches: Iterable[str] = ("main", "master"),
    explicit_push_request: bool = False,
    expected_origin_url: str | None = None,
    autonomy_mode: str = "off",
    repository_config: RepositoryConfig | None = None,
) -> CommandAssessment:
    """Classify a command and allow only supported, narrowly parsed operations.

    This function analyzes commands only; callers must still obtain the approval required by
    the relevant risk category before invoking an executor.
    """
    original = command
    normalized = command.strip()
    workspace = Path(workspace_path).expanduser().resolve(strict=True)
    if not normalized:
        return _assessment(
            CommandCategory.BLOCKED, False, "Empty commands are not allowed.", original
        )

    blocked_reason = blocked_command_reason(normalized)
    if blocked_reason is not None:
        return _assessment(CommandCategory.BLOCKED, False, blocked_reason, normalized)

    # This one fixed pipeline is supported because it is a read-only date filter. Arbitrary
    # PowerShell pipelines, script blocks, and statement chaining are not parsed as safe.
    date_filter = _DATE_FILTERED_CHILD_ITEM.fullmatch(normalized)
    if date_filter is not None:
        directory = _resolve_approved_read_directory(
            workspace, _unquote(date_filter.group("path")), approved_read_directories
        )
        if directory is None:
            return _assessment(
                CommandCategory.READ_ONLY,
                False,
                "Recursive date searches require an explicitly approved subdirectory.",
                normalized,
            )
        return _assessment(
            CommandCategory.READ_ONLY,
            True,
            "Lists workspace filenames modified since local midnight.",
            normalized,
        )

    try:
        tokens = _tokenize(normalized)
    except ValueError:
        return _assessment(
            CommandCategory.BLOCKED,
            False,
            "Command syntax is ambiguous or contains shell operators; manual review is required.",
            normalized,
        )
    if not tokens:
        return _assessment(CommandCategory.BLOCKED, False, "No command was provided.", normalized)

    executable = tokens[0].casefold()
    if executable == "git":
        return _assess_git(
            tokens[1:],
            workspace,
            current_branch,
            set(protected_branches),
            explicit_push_request,
            expected_origin_url,
            autonomy_mode,
        )

    if executable in _DESTRUCTIVE_COMMANDS:
        return _assessment(
            CommandCategory.DESTRUCTIVE,
            False,
            f"{tokens[0]} is a destructive operation and is never allowlisted.",
            normalized,
        )
    if executable in _NETWORK_COMMANDS:
        return _assessment(
            CommandCategory.NETWORK,
            False,
            "Network commands are not in the initial command allowlist.",
            normalized,
        )
    if executable in _WRITE_COMMANDS:
        return _assessment(
            CommandCategory.WORKSPACE_WRITE,
            False,
            "This workspace-writing command is not in the initial allowlist.",
            normalized,
        )
    if executable == "new-item":
        return _assess_new_file(tokens[1:], workspace, repository_config)
    if executable in _READ_COMMANDS:
        approved_files: set[Path] = set()
        for approved_file in approved_read_files:
            try:
                approved_files.add(_resolve_approval_path(workspace, approved_file))
            except OSError:
                continue
        approved_directories: set[Path] = set()
        for approved_directory in approved_read_directories:
            try:
                approved_directories.add(_resolve_approval_path(workspace, approved_directory))
            except OSError:
                continue
        return _assess_read(
            executable,
            tokens[1:],
            workspace,
            approved_files,
            approved_directories,
        )
    return _assessment(
        CommandCategory.BLOCKED,
        False,
        f"Unknown command '{tokens[0]}' is not in Cmdora's allowlist.",
        normalized,
    )


def _assess_read(
    executable: str,
    arguments: list[str],
    workspace: Path,
    approved_read_files: set[Path],
    approved_read_directories: set[Path],
) -> CommandAssessment:
    category = CommandCategory.READ_ONLY
    if executable == "get-content":
        requested_path, extra = _extract_path(arguments)
        if requested_path is None or extra:
            return _assessment(
                category,
                False,
                "Use Get-Content with one explicit file path only.",
                " ".join([executable, *arguments]),
            )
        try:
            resolved = validate_workspace_path(workspace, requested_path, must_exist=True)
        except (OSError, UnsafePathError):
            return _assessment(
                category,
                False,
                "The file is missing or outside the selected workspace.",
                " ".join([executable, *arguments]),
            )
        if not _is_approved_read_file(resolved, approved_read_files):
            return _assessment(
                category,
                False,
                "Get-Content requires an explicitly approved, non-secret workspace file.",
                " ".join([executable, *arguments]),
            )
        return _assessment(
            category,
            True,
            "Reads one explicitly approved workspace file.",
            " ".join([executable, *arguments]),
        )

    allowed_flags = (
        {"-path", "-filter", "-file", "-directory", "-recurse", "-name"}
        if executable == "get-childitem"
        else {"-path"}
    )
    recursive = any(argument.casefold() == "-recurse" for argument in arguments)
    requested_directory: Path | None = None
    if recursive:
        path_value: str | None = None
        for index, argument in enumerate(arguments[:-1]):
            if argument.casefold() == "-path":
                path_value = arguments[index + 1]
                break
        if path_value is None:
            return _assessment(
                category,
                False,
                "Recursive listings require an explicit approved subdirectory.",
                " ".join([executable, *arguments]),
            )
        try:
            requested_directory = validate_workspace_path(workspace, path_value, must_exist=True)
        except (OSError, UnsafePathError):
            return _assessment(
                category,
                False,
                "The recursive path is missing or outside the selected workspace.",
                " ".join([executable, *arguments]),
            )
        if (
            requested_directory == workspace
            or requested_directory not in approved_read_directories
            or not requested_directory.is_dir()
            or _has_secret_path_component(requested_directory)
        ):
            return _assessment(
                category,
                False,
                "Recursive listings require an explicitly approved, non-secret subdirectory.",
                " ".join([executable, *arguments]),
            )
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument.startswith("-"):
            parameter = argument.casefold()
            if parameter not in allowed_flags:
                return _assessment(
                    category,
                    False,
                    f"Unsupported {executable} option: {argument}.",
                    " ".join([executable, *arguments]),
                )
            if parameter in {"-path", "-filter"}:
                index += 1
                if index >= len(arguments):
                    return _assessment(
                        category,
                        False,
                        f"{argument} requires a value.",
                        " ".join([executable, *arguments]),
                    )
                value = arguments[index]
                if parameter == "-path":
                    try:
                        resolved_path = validate_workspace_path(workspace, value, must_exist=True)
                    except (OSError, UnsafePathError):
                        return _assessment(
                            category,
                            False,
                            "The path is missing or outside the selected workspace.",
                            " ".join([executable, *arguments]),
                        )
                    if _has_secret_path_component(resolved_path):
                        return _assessment(
                            category,
                            False,
                            "Secret and credential paths cannot be inspected.",
                            " ".join([executable, *arguments]),
                        )
        else:
            try:
                resolved_path = validate_workspace_path(workspace, argument, must_exist=True)
            except (OSError, UnsafePathError):
                return _assessment(
                    category,
                    False,
                    "The path is missing or outside the selected workspace.",
                    " ".join([executable, *arguments]),
                )
            if _has_secret_path_component(resolved_path):
                return _assessment(
                    category,
                    False,
                    "Secret and credential paths cannot be inspected.",
                    " ".join([executable, *arguments]),
                )
        index += 1
    return _assessment(
        category,
        True,
        "Read-only file metadata command is allowlisted.",
        " ".join([executable, *arguments]),
    )


def _assess_new_file(
    arguments: list[str], workspace: Path, repository_config: RepositoryConfig | None
) -> CommandAssessment:
    category = CommandCategory.WORKSPACE_CREATE
    item_type: str | None = None
    path: str | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument.casefold() in {"-itemtype", "-path"}:
            parameter = argument.casefold()
            index += 1
            if index >= len(arguments):
                return _assessment(category, False, f"{argument} requires a value.", "New-Item")
            if parameter == "-itemtype":
                item_type = arguments[index]
            else:
                path = arguments[index]
        else:
            return _assessment(
                category, False, f"Unsupported New-Item argument: {argument}.", "New-Item"
            )
        index += 1
    if item_type is None or item_type.casefold() != "file" or path is None:
        return _assessment(
            category, False, "Only New-Item -ItemType File -Path <path> is allowed.", "New-Item"
        )

    config = repository_config or RepositoryConfigStore(workspace).load()
    try:
        target = validate_repository_write_path(workspace, path, config)
    except (OSError, ValueError, PermissionError) as error:
        return _assessment(category, False, str(error), "New-Item")
    if target.exists() or target.is_symlink():
        return _assessment(
            category, False, "New-Item cannot overwrite an existing file.", "New-Item"
        )
    if _is_secret_path(target):
        return _assessment(
            category,
            False,
            "Secret and credential files cannot be created through commands.",
            "New-Item",
        )
    return _assessment(
        category, True, "Creates one new file under an allowed workspace directory.", "New-Item"
    )


def _assess_git(
    arguments: list[str],
    workspace: Path,
    current_branch: str | None,
    protected_branches: set[str],
    explicit_push_request: bool,
    expected_origin_url: str | None,
    autonomy_mode: str,
) -> CommandAssessment:
    if not arguments:
        return _assessment(CommandCategory.BLOCKED, False, "A Git subcommand is required.", "git")
    subcommand = arguments[0].casefold()
    operands = arguments[1:]
    if subcommand in _DESTRUCTIVE_GIT_COMMANDS:
        return _assessment(
            CommandCategory.DESTRUCTIVE,
            False,
            f"git {subcommand} can discard workspace or repository state and is blocked.",
            "git " + subcommand,
        )
    if subcommand == "status":
        allowed = {"--short", "--branch", "--porcelain"}
        if all(item.casefold() in allowed for item in operands):
            return _assessment(
                CommandCategory.READ_ONLY, True, "Git status is read-only.", "git status"
            )
        return _assessment(
            CommandCategory.READ_ONLY,
            False,
            "Only concise Git status options are allowed.",
            "git status",
        )
    if subcommand == "diff":
        if operands == ["--stat"]:
            return _assessment(
                CommandCategory.READ_ONLY,
                True,
                "Git diff statistics are read-only.",
                "git diff --stat",
            )
        return _assessment(
            CommandCategory.READ_ONLY, False, "Only git diff --stat is allowlisted.", "git diff"
        )
    if subcommand == "add":
        paths = operands[1:] if operands[:1] == ["--"] else operands
        if not paths or any(path.startswith("-") or path.startswith(":(") for path in paths):
            return _assessment(
                CommandCategory.GIT_STAGE,
                False,
                "Stage explicit workspace file paths only.",
                "git add",
            )
        for path in paths:
            try:
                resolved = validate_workspace_path(workspace, path, must_exist=True)
            except (OSError, UnsafePathError):
                return _assessment(
                    CommandCategory.GIT_STAGE,
                    False,
                    "Git add path is missing or outside the workspace.",
                    "git add",
                )
            if resolved == workspace or not resolved.is_file() or _is_secret_path(resolved):
                return _assessment(
                    CommandCategory.GIT_STAGE,
                    False,
                    "Git add requires explicit non-secret workspace paths.",
                    "git add",
                )
        return _assessment(
            CommandCategory.GIT_STAGE, True, "Stages only explicit workspace file paths.", "git add"
        )
    if subcommand == "commit":
        message = (
            _unquote(operands[1])
            if len(operands) == 2 and operands[0].casefold() in {"-m", "--message"}
            else None
        )
        validation = GitSafetyValidator(workspace).validate_commit(
            message, autonomy_mode=autonomy_mode
        )
        return _assessment(
            CommandCategory.GIT_COMMIT,
            validation.allowed,
            validation.reasons,
            "git commit",
        )
    if subcommand == "push":
        if operands != ["origin", "HEAD"]:
            return _assessment(
                CommandCategory.GIT_PUSH,
                False,
                "Only 'git push origin HEAD' is eligible for policy checks.",
                "git push",
            )
        validation = GitSafetyValidator(workspace).validate_push(
            explicit_push_request=explicit_push_request,
            protected_branches=protected_branches,
            expected_origin_url=expected_origin_url,
            expected_branch=current_branch,
        )
        return _assessment(
            CommandCategory.GIT_PUSH,
            validation.allowed,
            validation.reasons,
            "git push origin HEAD",
        )

    return _assessment(
        CommandCategory.BLOCKED,
        False,
        f"Git subcommand '{subcommand}' is not allowlisted.",
        "git " + subcommand,
    )


def _extract_path(arguments: list[str]) -> tuple[str | None, list[str]]:
    if len(arguments) == 1 and not arguments[0].startswith("-"):
        return arguments[0], []
    if len(arguments) == 2 and arguments[0].casefold() == "-path":
        return arguments[1], []
    return None, arguments


def _is_approved_read_file(path: Path, approved_read_files: set[Path]) -> bool:
    return path in approved_read_files and path.is_file() and not _is_secret_path(path)


def _is_secret_path(path: Path) -> bool:
    return _has_secret_path_component(path) or any(
        component.casefold() in _SECRET_NAMES
        or component.casefold().startswith((".env", "credentials."))
        or Path(component).suffix.casefold() in _SECRET_SUFFIXES
        for component in path.parts
    )


def _resolve_approved_read_directory(
    workspace: Path,
    requested_path: str,
    approved_read_directories: Iterable[Path | str],
) -> Path | None:
    try:
        directory = validate_workspace_path(workspace, requested_path, must_exist=True)
    except (OSError, UnsafePathError):
        return None
    if directory == workspace or not directory.is_dir() or _has_secret_path_component(directory):
        return None
    for approved_directory in approved_read_directories:
        try:
            approved = _resolve_approval_path(workspace, approved_directory)
        except OSError:
            continue
        if directory == approved:
            return directory
    return None


def _resolve_approval_path(workspace: Path, path: Path | str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    return candidate.resolve(strict=True)


def _has_secret_path_component(path: Path) -> bool:
    return any(
        component.casefold() in _SECRET_NAMES
        or component.casefold().startswith(".env")
        or component.casefold() in {".ssh", ".git", ".cmdora", "browser data"}
        for component in path.parts
    )


def _tokenize(command: str) -> list[str]:
    if _SHELL_METACHARACTERS.search(command) or "$" in command or "`" in command:
        raise ValueError("shell syntax is not supported")
    return [_unquote(token) for token in shlex.split(command, posix=False)]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        quoted = value[1:-1]
        return quoted.replace("''", "'") if value[0] == "'" else quoted
    return value


def _assessment(
    category: CommandCategory, allowed: bool, reason: str | tuple[str, ...], command: str
) -> CommandAssessment:
    reasons = [reason] if isinstance(reason, str) else list(reason)
    risk_level: Literal["safe", "caution", "high", "blocked"]
    if not allowed:
        risk_level = "blocked"
    elif category == CommandCategory.READ_ONLY:
        risk_level = "safe"
    elif category == CommandCategory.GIT_PUSH:
        risk_level = "high"
    else:
        risk_level = "caution"
    decision = PolicyDecision(
        allowed=allowed,
        risk_level=risk_level,
        requires_confirmation=allowed and risk_level in {"caution", "high"},
        reasons=reasons,
        sanitized_command=redact_sensitive_text(command),
    )
    return CommandAssessment(category, decision)


__all__ = ["CommandAssessment", "CommandCategory", "PolicyDecision", "assess_command"]
