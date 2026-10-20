"""Safe workspace context preparation for the model layer."""

from cmdora.safety import redact_sensitive_text


def sanitize_model_context(context: str) -> str:
    """Return context suitable for a model without secrets or local home paths."""
    return redact_sensitive_text(context)


__all__ = ["sanitize_model_context"]
