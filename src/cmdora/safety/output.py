"""Readable terminal output for local policy decisions."""

from __future__ import annotations

from cmdora.safety.policy import PolicyDecision


def format_policy_decision(decision: PolicyDecision) -> str:
    """Render a policy result without exposing unredacted command content."""
    title = _title(decision)
    reasons = "\n".join(f"- {reason}" for reason in decision.reasons)
    lines = [title, "", "Why:", reasons]
    if not decision.allowed:
        lines.extend(("", "Suggested alternative:", _alternative(decision)))
    elif decision.requires_confirmation:
        lines.extend(("", "Confirmation required:", "Review the action before it is executed."))
    return "\n".join(lines)


def _title(decision: PolicyDecision) -> str:
    if not decision.allowed:
        return "Blocked: Cmdora will not execute this action."
    return f"Allowed ({decision.risk_level} risk): Cmdora can evaluate this action."


def _alternative(decision: PolicyDecision) -> str:
    explanation = " ".join(decision.reasons).casefold()
    if "deletion" in explanation or "destructive" in explanation:
        return "Use File Explorer or manually review and remove the target directory."
    if "outside the workspace" in explanation or "workspace path" in explanation:
        return "Choose a non-secret path inside the selected workspace."
    if "push" in explanation:
        return "Push a non-protected branch to origin after explicitly requesting a push."
    if "commit" in explanation or "staged" in explanation:
        return "Stage the intended files manually, then provide a non-empty commit message."
    if "unknown command" in explanation or "allowlist" in explanation:
        return "Use one of Cmdora's supported actions or review the command manually."
    return "Review the request manually and use a supported Cmdora action if appropriate."


__all__ = ["format_policy_decision"]
