"""Tests for secret redaction shared by audit and model context paths."""

from pathlib import Path

from cmdora.context import sanitize_model_context
from cmdora.safety import redact_sensitive_text


def test_redaction_removes_required_secret_types_and_home_paths() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureValue"
    aws_key = "AKIAIOSFODNN7EXAMPLE"
    private_key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-content\n-----END OPENSSH PRIVATE KEY-----"
    )
    value = "\n".join(
        (
            "GEMINI_API_KEY=gemini-secret-value",
            "password=hunter2",
            "--password cli-password-secret",
            "Authorization: Bearer bearer-secret-value",
            f"jwt={jwt}",
            f"AWS_ACCESS_KEY_ID={aws_key}",
            private_key,
            "C:\\Users\\Indra\\project\\file.txt",
            "FROM_DOTENV=dotenv-secret",
        )
    )

    redacted = redact_sensitive_text(value, home_directories=[Path("C:/Users/Indra")])

    for secret in (
        "gemini-secret-value",
        "hunter2",
        "cli-password-secret",
        "bearer-secret-value",
        jwt,
        aws_key,
        "private-content",
        "dotenv-secret",
        "C:\\Users\\Indra",
    ):
        assert secret not in redacted
    assert "[REDACTED]" in redacted
    assert "[HOME]\\project\\file.txt" in redacted


def test_model_context_uses_the_shared_redactor() -> None:
    context = "GEMINI_API_KEY=do-not-share\npassword=also-secret"

    sanitized = sanitize_model_context(context)

    assert "do-not-share" not in sanitized
    assert "also-secret" not in sanitized
    assert sanitized.count("[REDACTED]") == 2
