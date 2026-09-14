"""Tests for reup.web.auth module - pure authentication functions."""

import time
import hmac
import hashlib
import pytest

from reup.web.auth import (
    COOKIE,
    MAX_AGE,
    secret_for,
    make_token,
    verify_token,
    safe_next,
)


class TestSecretFor:
    """Test secret derivation."""

    def test_secret_for_with_explicit(self):
        """When explicit secret is provided, use it (encoded as bytes)."""
        explicit = "my-explicit-secret"
        result = secret_for("some-password", explicit=explicit)
        assert result == explicit.encode()
        assert isinstance(result, bytes)

    def test_secret_for_from_password(self):
        """When no explicit secret, derive from password via SHA256."""
        password = "test-password"
        result = secret_for(password)
        # Should be sha256("reup-session:" + password).digest()
        expected = hashlib.sha256(("reup-session:" + password).encode()).digest()
        assert result == expected
        assert isinstance(result, bytes)
        assert len(result) == 32  # SHA256 produces 32 bytes

    def test_secret_for_different_passwords(self):
        """Different passwords produce different secrets."""
        secret1 = secret_for("password1")
        secret2 = secret_for("password2")
        assert secret1 != secret2

    def test_secret_for_empty_explicit(self):
        """Empty explicit string means derive from password."""
        password = "password"
        result1 = secret_for(password, explicit="")
        result2 = secret_for(password)
        assert result1 == result2


class TestMakeToken:
    """Test token creation."""

    def test_make_token_format(self):
        """Token has format 'expiry.hmac_hex'."""
        secret = b"test-secret"
        now = 1000.0
        token = make_token(secret, now)

        parts = token.split(".")
        assert len(parts) == 2, f"Token should have 2 parts separated by '.', got: {token}"

        expiry_str, hmac_hex = parts
        assert expiry_str == str(int(now + MAX_AGE))
        assert len(hmac_hex) == 64  # SHA256 hex digest is 64 chars
        assert all(c in "0123456789abcdef" for c in hmac_hex)

    def test_make_token_expiry_calculation(self):
        """Expiry should be now + MAX_AGE."""
        secret = b"test-secret"
        now = 1000.0
        token = make_token(secret, now)

        expiry_str, _ = token.split(".")
        expiry = int(expiry_str)
        assert expiry == int(now + MAX_AGE)

    def test_make_token_hmac_valid(self):
        """HMAC should be correctly computed."""
        secret = b"test-secret"
        now = 1000.0
        token = make_token(secret, now)

        expiry_str, hmac_hex = token.split(".")
        expected_hmac = hmac.new(
            secret, expiry_str.encode(), hashlib.sha256
        ).hexdigest()
        assert hmac_hex == expected_hmac

    def test_make_token_different_secrets(self):
        """Different secrets produce different tokens."""
        now = 1000.0
        token1 = make_token(b"secret1", now)
        token2 = make_token(b"secret2", now)
        assert token1 != token2


class TestVerifyToken:
    """Test token verification."""

    def test_verify_token_fresh_token(self):
        """A freshly created token should be valid."""
        secret = b"test-secret"
        now = 1000.0
        token = make_token(secret, now)

        assert verify_token(secret, token, now) is True

    def test_verify_token_not_yet_expired(self):
        """Token slightly in the future is still valid."""
        secret = b"test-secret"
        now = 1000.0
        token = make_token(secret, now)

        # Check token shortly after creation (before expiry)
        assert verify_token(secret, token, now + 1000.0) is True

    def test_verify_token_expired(self):
        """Expired token should be invalid."""
        secret = b"test-secret"
        now = 1000.0
        token = make_token(secret, now)

        # Check token after it has expired
        future_now = now + MAX_AGE + 1  # Beyond expiry
        assert verify_token(secret, token, future_now) is False

    def test_verify_token_wrong_secret(self):
        """Token signed with different secret should be invalid."""
        secret1 = b"secret1"
        secret2 = b"secret2"
        now = 1000.0
        token = make_token(secret1, now)

        assert verify_token(secret2, token, now) is False

    def test_verify_token_tampered_hmac(self):
        """Token with altered HMAC should be invalid."""
        secret = b"test-secret"
        now = 1000.0
        token = make_token(secret, now)

        # Flip one character in the HMAC part
        expiry_str, hmac_hex = token.split(".")
        tampered_hmac = (
            hmac_hex[:-1] + ("0" if hmac_hex[-1] != "0" else "1")
        )  # Flip last char
        tampered_token = f"{expiry_str}.{tampered_hmac}"

        assert verify_token(secret, tampered_token, now) is False

    def test_verify_token_tampered_expiry(self):
        """Token with altered expiry should be invalid."""
        secret = b"test-secret"
        now = 1000.0
        token = make_token(secret, now)

        # Alter the expiry part
        expiry_str, hmac_hex = token.split(".")
        old_expiry = int(expiry_str)
        new_expiry = old_expiry + 1000  # Add 1000 seconds
        tampered_token = f"{new_expiry}.{hmac_hex}"

        assert verify_token(secret, tampered_token, now) is False

    def test_verify_token_empty_string(self):
        """Empty token should return False, not raise."""
        secret = b"test-secret"
        now = 1000.0

        assert verify_token(secret, "", now) is False

    def test_verify_token_garbage_no_dot(self):
        """Token without dot separator should return False, not raise."""
        secret = b"test-secret"
        now = 1000.0

        assert verify_token(secret, "abc", now) is False

    def test_verify_token_garbage_too_many_parts(self):
        """Token with too many parts should return False, not raise."""
        secret = b"test-secret"
        now = 1000.0

        assert verify_token(secret, "1.2.3", now) is False

    def test_verify_token_non_numeric_expiry(self):
        """Token with non-numeric expiry should return False, not raise."""
        secret = b"test-secret"
        now = 1000.0

        assert verify_token(secret, "abc.123def456", now) is False

    def test_verify_token_garbage_input_variations(self):
        """Various malformed inputs should never raise."""
        secret = b"test-secret"
        now = 1000.0

        garbage_inputs = [
            "",
            ".",
            "..",
            "1",
            "1.",
            ".abc",
            "abc.def",
            "1.2.3.4",
            "not-a-number.deadbeef",
        ]

        for garbage in garbage_inputs:
            result = verify_token(secret, garbage, now)
            assert result is False, f"Should reject {garbage!r}"


class TestSafeNext:
    """Test safe redirect path validation."""

    def test_safe_next_relative_path(self):
        """Valid relative paths starting with / are kept."""
        assert safe_next("/jobs/x") == "/jobs/x"
        assert safe_next("/") == "/"
        assert safe_next("/path/to/resource") == "/path/to/resource"
        assert safe_next("/with-dashes") == "/with-dashes"
        assert safe_next("/with_underscores") == "/with_underscores"
        assert safe_next("/with.dots") == "/with.dots"

    def test_safe_next_double_slash_protocol_relative(self):
        """Protocol-relative URLs starting with // are rejected."""
        assert safe_next("//evil.com") == "/"
        assert safe_next("//evil.com/path") == "/"
        assert safe_next("//localhost:8000") == "/"

    def test_safe_next_absolute_url_https(self):
        """Absolute URLs with schemes are rejected."""
        assert safe_next("https://evil.com") == "/"
        assert safe_next("http://evil.com") == "/"
        assert safe_next("https://evil.com/path") == "/"

    def test_safe_next_none(self):
        """None returns /."""
        assert safe_next(None) == "/"

    def test_safe_next_empty_string(self):
        """Empty string returns /."""
        assert safe_next("") == "/"

    def test_safe_next_no_leading_slash(self):
        """Paths without leading / are rejected."""
        assert safe_next("jobs/x") == "/"
        assert safe_next("relative/path") == "/"

    def test_safe_next_query_string(self):
        """Relative paths with query strings are kept."""
        assert safe_next("/path?query=value") == "/path?query=value"

    def test_safe_next_fragment(self):
        """Relative paths with fragments are kept."""
        assert safe_next("/path#fragment") == "/path#fragment"

    def test_safe_next_spaces_and_special_chars(self):
        """Paths with encoded special characters are kept if relative."""
        assert safe_next("/path%20with%20spaces") == "/path%20with%20spaces"
        assert safe_next("/path?q=a&b=c") == "/path?q=a&b=c"


class TestModuleConstants:
    """Test module-level constants."""

    def test_cookie_name(self):
        """COOKIE should be 'reup_session'."""
        assert COOKIE == "reup_session"

    def test_max_age_value(self):
        """MAX_AGE should be 30 days in seconds."""
        assert MAX_AGE == 30 * 24 * 3600
        assert MAX_AGE == 2592000
