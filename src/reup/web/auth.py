"""Pure authentication functions for reup web UI password protection.

This module implements a simple HMAC-signed session token scheme without
external dependencies beyond hashlib and hmac. No FastAPI code here - just
pure functions for token creation, verification, and safe redirect validation.
"""

import hashlib
import hmac
from typing import Optional


# Module constants
COOKIE = "reup_session"
MAX_AGE = 30 * 24 * 3600  # 30 days in seconds


def secret_for(password: str, explicit: str = "") -> bytes:
    """Derive a signing secret from password or use an explicit one.

    Args:
        password: The password to derive secret from (if explicit is empty).
        explicit: If non-empty, this value is encoded and used as the secret.

    Returns:
        Secret as bytes suitable for HMAC operations.
    """
    if explicit:
        return explicit.encode()
    # Derive from password: sha256("reup-session:" + password)
    return hashlib.sha256(("reup-session:" + password).encode()).digest()


def make_token(secret: bytes, now: float) -> str:
    """Create a signed session token.

    Token format: "{expiry}.{hmac_hex}"
    - expiry: Unix timestamp when token expires (now + MAX_AGE)
    - hmac_hex: HMAC-SHA256 of expiry, hex-encoded

    Args:
        secret: Signing secret (bytes).
        now: Current Unix timestamp.

    Returns:
        Token string in format "expiry.hmac_hex".
    """
    expiry = int(now + MAX_AGE)
    expiry_str = str(expiry)

    # Sign the expiry timestamp
    hmac_obj = hmac.new(secret, expiry_str.encode(), hashlib.sha256)
    hmac_hex = hmac_obj.hexdigest()

    return f"{expiry_str}.{hmac_hex}"


def verify_token(secret: bytes, token: str, now: float) -> bool:
    """Verify a signed session token.

    Checks:
    1. Token format is valid (2 dot-separated parts)
    2. Expiry is numeric and in the future (now <= expiry)
    3. HMAC signature is valid (using timing-safe comparison)

    Returns False (never raises) on any error, including malformed input.

    Args:
        secret: Signing secret (bytes).
        token: Token string to verify.
        now: Current Unix timestamp.

    Returns:
        True if token is valid and not expired, False otherwise.
    """
    try:
        # Parse token
        parts = token.split(".")
        if len(parts) != 2:
            return False

        expiry_str, hmac_hex = parts

        # Parse expiry as integer
        try:
            expiry = int(expiry_str)
        except ValueError:
            return False

        # Check if token is still valid (not expired)
        if now > expiry:
            return False

        # Verify HMAC using timing-safe comparison
        expected_hmac = hmac.new(
            secret, expiry_str.encode(), hashlib.sha256
        ).hexdigest()

        # Use hmac.compare_digest for timing-safe comparison
        if not hmac.compare_digest(hmac_hex, expected_hmac):
            return False

        return True
    except Exception:
        # Catch any unexpected errors and return False
        return False


def safe_next(value: Optional[str]) -> str:
    """Validate a redirect path to prevent open redirects.

    Accepts only relative paths starting with / but not starting with //
    (which would be protocol-relative URLs). Rejects absolute URLs,
    None, empty strings, and paths containing backslashes or control
    characters that could be normalized by browsers into open redirects.

    Valid: "/jobs/x", "/", "/path?query=1"
    Invalid: "//evil.com", "https://evil.com", "/\\evil.com", None, "", "relative/path"

    Args:
        value: The redirect path to validate (can be None or empty string).

    Returns:
        The original value if valid, "/" otherwise.
    """
    # Reject None and empty strings
    if not value:
        return "/"

    # Must start with single / (not //)
    if not value.startswith("/") or value.startswith("//"):
        return "/"

    # Reject backslash-based open-redirect tricks
    # Browsers normalize \ to / for special schemes, turning /\evil.com into //evil.com
    if value.startswith("/\\"):
        return "/"

    # Reject any backslashes or control characters that could be smuggled
    if "\\" in value or "\r" in value or "\n" in value or "\t" in value:
        return "/"

    # Additional check: reject anything that looks like an absolute URL
    # (protocol-relative // or scheme://)
    if "://" in value:
        return "/"

    return value
