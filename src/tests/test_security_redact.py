"""Tests for secret redaction and sanitization."""

import os

from security.redact import get_safe_env, redact_secrets, sanitize_for_storage, strip_ansi


class TestRedactSecrets:
    def test_redacts_github_token(self):
        text = "token: ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
        result = redact_secrets(text)
        assert 'ghp_' not in result
        assert '[REDACTED]' in result

    def test_redacts_anthropic_key(self):
        text = "key: sk-ant-api03-1234567890abcdefghijklmno"
        result = redact_secrets(text)
        assert 'sk-ant-' not in result

    def test_redacts_gitlab_token(self):
        text = "glpat-xxxxxxxxxxxxxxxxxxxx1234"
        result = redact_secrets(text)
        assert 'glpat-' not in result

    def test_redacts_jwt(self):
        text = "token: eyJhbGciOiJSUzI1NiIsInR5.eyJpc3MiOiJrdWJlcm5ldGVzL.G4wiky5Lq-fyh4whBpu2rP"
        result = redact_secrets(text)
        assert 'eyJ' not in result

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer abc123def456ghi789jkl012mno"
        result = redact_secrets(text)
        assert 'abc123' not in result

    def test_preserves_normal_text(self):
        text = "Build failed: missing dependency numpy>=2.0"
        assert redact_secrets(text) == text

    def test_redacts_hardcoded_password(self):
        text = 'password = "super_secret_password_123"'
        result = redact_secrets(text)
        assert 'super_secret' not in result

    def test_handles_none(self):
        assert redact_secrets(None) is None
        assert redact_secrets('') == ''


class TestStripAnsi:
    def test_strips_color_codes(self):
        text = '\x1b[31mError\x1b[0m: failed'
        result = strip_ansi(text)
        assert result == 'Error: failed'

    def test_preserves_plain_text(self):
        text = 'normal text without escapes'
        assert strip_ansi(text) == text


class TestSanitizeForStorage:
    def test_combined_sanitization(self):
        text = '\x1b[31mtoken: ghp_1234567890abcdefghijklmnopqrstuvwxyz1234\x1b[0m'
        result = sanitize_for_storage(text)
        assert '\x1b' not in result
        assert 'ghp_' not in result
        assert '[REDACTED]' in result


class TestGetSafeEnv:
    def test_includes_path(self):
        env = get_safe_env()
        assert 'PATH' in env

    def test_excludes_undeclared_tokens(self):
        os.environ['SECRET_TEST_TOKEN'] = 'should-not-appear'
        try:
            env = get_safe_env()
            assert 'SECRET_TEST_TOKEN' not in env
        finally:
            del os.environ['SECRET_TEST_TOKEN']

    def test_includes_declared_vars(self):
        os.environ['MY_DECLARED_VAR'] = 'allowed'
        try:
            env = get_safe_env(declared_vars=['MY_DECLARED_VAR'])
            assert env.get('MY_DECLARED_VAR') == 'allowed'
        finally:
            del os.environ['MY_DECLARED_VAR']

    def test_declared_but_unset_is_fine(self):
        env = get_safe_env(declared_vars=['NONEXISTENT_VAR'])
        assert 'NONEXISTENT_VAR' not in env
