"""Tests for secret redaction."""
from codegraph_mcp.security.redaction import redact


def test_aws_key_redacted():
    text = "key = AKIAIOSFODNN7EXAMPLE1234"
    assert "[REDACTED_SECRET]" in redact(text)


def test_github_token_redacted():
    text = "token: ghp_abcdefghijklmnopqrstuvwxyz123456"
    assert "[REDACTED_SECRET]" in redact(text)


def test_api_key_redacted():
    text = 'API_KEY = "supersecretvalue123"'
    assert "[REDACTED_SECRET]" in redact(text)


def test_password_redacted():
    text = "password='hunter2'"
    assert "[REDACTED_SECRET]" in redact(text)


def test_bearer_token_redacted():
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
    assert "[REDACTED_SECRET]" in redact(text)


def test_private_key_redacted():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    assert "[REDACTED_SECRET]" in redact(text)


def test_clean_text_unchanged():
    text = "def foo():\n    return 42"
    assert redact(text) == text


def test_multiple_secrets_in_one_text():
    text = 'api_key="abc123"\npassword = "pw123"'
    result = redact(text)
    assert result.count("[REDACTED_SECRET]") >= 1
