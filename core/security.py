"""AES-256-GCM encryption and PII tokenization for the GetAJob platform.

All Personally Identifiable Information (PII) written to the database is
encrypted at rest using AES-256 in GCM mode with a random nonce per value.
A separate key-derivation function (PBKDF2) converts a user-supplied password
into a 256-bit key.

Usage::

    key = derive_key("my-password", salt_bytes)
    ct = encrypt_value("turin@example.com", key)
    pt = decrypt_value(ct, key)
"""

from __future__ import annotations as _annotations

import base64
import hashlib
import hmac
import os
from typing import Protocol

import structlog
from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm

from core.exceptions import SecurityError

__all__: list[str] = [
    "decrypt_value",
    "derive_key",
    "detokenize_pii",
    "encrypt_value",
    "generate_key",
    "tokenize_pii",
]

logger = structlog.get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────────

_AES_KEY_LENGTH = 32  # 256 bits
_NONCE_LENGTH = 12  # 96 bits (recommended for GCM)
_TAG_LENGTH = 16  # 128-bit authentication tag
_PBKDF2_ITERATIONS = 600_000  # OWASP 2023 recommendation
_TOKEN_HASH_ALGORITHM = "sha256"
_ENCODING = "utf-8"

# Known test/insecure key values that should never reach production.
# Matched by exact comparison and also by substring heuristics.
_DEFAULT_OR_TEST_KEYS: list[bytes] = [
    b"test-key-not-for-prod-32bytes!",
    b"this-is-not-a-secure-key-!!!!!!!!",
    b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    b"00000000000000000000000000000000",
]


# ── Helpers ────────────────────────────────────────────────────────────────────────


def _is_likely_test_key(key: bytes) -> bool:
    """Check whether *key* looks like a test or default value rather than a real key.

    Heuristics include exact matches against known-insecure values, and
    ASCII-decodable keys whose text contains common test markers or are
    composed of a single repeated character (low entropy).
    """
    if key in _DEFAULT_OR_TEST_KEYS:
        return True
    if len(key) != _AES_KEY_LENGTH:
        return False  # length validation happens elsewhere
    # Keys that are all the same byte or all printable ASCII with test markers
    # are almost certainly placeholders, not production-grade keys.
    if len(set(key)) == 1:
        return True
    try:
        text = key.decode("ascii")
        markers = ("test", "not-for-prod", "default", "placeholder", "changeme", "example")
        if any(marker in text.lower() for marker in markers):
            return True
    except (UnicodeDecodeError, ValueError):
        pass
    return False


# ── Encryption / Decryption ──────────────────────────────────────────────────────


def encrypt_value(plaintext: str, key: bytes) -> str:
    """Encrypt *plaintext* with AES-256-GCM and return a base64-encoded ciphertext.

    The output format is ``base64(nonce || ciphertext || tag)``, which embeds
    the nonce alongside the ciphertext so that no additional IV tracking is
    needed.

    Args:
        plaintext: UTF-8 string to encrypt.
        key: 32-byte AES-256 key.

    Returns:
        Base64-encoded ciphertext string (URL-safe, no padding).

    Raises:
        SecurityError: If *key* is not 32 bytes or the underlying crypto fails.
    """
    if len(key) != _AES_KEY_LENGTH:
        msg = f"key must be exactly {_AES_KEY_LENGTH} bytes, got {len(key)}"
        raise SecurityError(msg)

    if _is_likely_test_key(key):
        logger.warning(
            "Encryption key appears to be a test or default value — PII at rest is NOT "
            "secure. Generate a proper key with generate_key() for production use.",
            key_preview=base64.b64encode(key[:_TAG_LENGTH]).decode(),
        )

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        msg = "cryptography package is required for encryption"
        raise SecurityError(msg) from exc

    try:
        data = plaintext.encode(_ENCODING)
        nonce = os.urandom(_NONCE_LENGTH)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, data, None)  # (nonce || ct || tag)
        payload = nonce + ciphertext
        return base64.urlsafe_b64encode(payload).decode(_ENCODING).rstrip("=")
    except (InvalidTag, UnsupportedAlgorithm, AttributeError, ValueError, TypeError) as exc:
        msg = "Encryption failed"
        raise SecurityError(msg, details={"error": str(exc)}) from exc


def decrypt_value(ciphertext_b64: str, key: bytes) -> str:
    """Decrypt a base64-encoded ciphertext produced by :func:`encrypt_value`.

    Args:
        ciphertext_b64: Base64-encoded (nonce || ciphertext || tag).
        key: 32-byte AES-256 key (must match the encryption key).

    Returns:
        Original plaintext UTF-8 string.

    Raises:
        SecurityError: If the key is wrong, the tag is invalid, or the
            ciphertext is malformed.
    """
    if len(key) != _AES_KEY_LENGTH:
        msg = f"key must be exactly {_AES_KEY_LENGTH} bytes, got {len(key)}"
        raise SecurityError(msg)

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        msg = "cryptography package is required for decryption"
        raise SecurityError(msg) from exc

    try:
        # Restore padding before decoding.
        padding = 4 - (len(ciphertext_b64) % 4)
        if padding != 4:
            ciphertext_b64 += "=" * padding
        payload = base64.urlsafe_b64decode(ciphertext_b64)
    except Exception as exc:
        msg = "Ciphertext is not valid base64"
        raise SecurityError(msg, details={"error": str(exc)}) from exc

    if len(payload) < _NONCE_LENGTH + _TAG_LENGTH:
        msg = "Ciphertext is too short (missing nonce or tag)"
        raise SecurityError(msg)

    try:
        nonce = payload[:_NONCE_LENGTH]
        ct = payload[_NONCE_LENGTH:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ct, None)
        return plaintext.decode(_ENCODING)
    except InvalidTag as exc:
        msg = "Decryption failed — authentication tag mismatch (key is wrong or data corrupted)"
        raise SecurityError(msg, details={"error": str(exc)}) from exc
    except Exception as exc:
        msg = "Decryption failed — key may be wrong or data corrupted"
        raise SecurityError(msg, details={"error": str(exc)}) from exc


# ── Key Derivation ───────────────────────────────────────────────────────────────


def derive_key(
    password: str, salt: bytes | None = None, *, iterations: int = _PBKDF2_ITERATIONS
) -> tuple[bytes, bytes]:
    """Derive a 256-bit AES key from *password* using PBKDF2-HMAC-SHA256.

    Args:
        password: User-supplied passphrase.
        salt: Optional 16-byte salt. A random salt is generated if omitted.
        iterations: PBKDF2 iteration count (default 600 000).

    Returns:
        Tuple of ``(key, salt)`` where *key* is 32 bytes and *salt* is 16 bytes.
        Persist the salt alongside the ciphertext to enable re-derivation.
    """
    if salt is None:
        salt = os.urandom(16)

    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(_ENCODING), salt, iterations, dklen=_AES_KEY_LENGTH
    )
    return dk, salt


def generate_key() -> bytes:
    """Generate a secure random 256-bit AES key."""
    return os.urandom(_AES_KEY_LENGTH)


# ── PII Tokenization ─────────────────────────────────────────────────────────────


class _TokenMapping(Protocol):
    """Minimal KV store interface for token ↔ PII mapping."""

    def get(self, key: str, default: str | None = None) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...


def tokenize_pii(value: str, salt: str, *, store: _TokenMapping | None = None) -> str:
    """Deterministically hash *value* into a reversible token.

    The token is an HMAC-SHA256 digest (truncated to 16 bytes, base64-encoded)
    that can be looked up in an external KV store to recover the original
    plaintext — or reversed via :func:`detokenize_pii` if the same salt is
    known.

    Args:
        value: The PII value to tokenize (e.g. an email address).
        salt: Hex-encoded HMAC key.
        store: Optional KV store for reverse lookup. If provided, the
            token → value mapping is stored automatically.

    Returns:
        A URL-safe base64 token string (no padding).
    """
    raw_salt = bytes.fromhex(salt) if isinstance(salt, str) else salt
    digest = hmac.new(raw_salt, value.encode(_ENCODING), _TOKEN_HASH_ALGORITHM).digest()
    token = base64.urlsafe_b64encode(digest[:_TAG_LENGTH]).decode(_ENCODING).rstrip("=")

    if store is not None:
        store.set(f"pii:{token}", value)

    return token


def detokenize_pii(token: str, salt: str, value: str, *, store: _TokenMapping | None = None) -> str:
    """Recover the original PII value from a token produced by :func:`tokenize_pii`.

    If a *store* is provided, the token is looked up there first. Otherwise the
    function re-computes the HMAC and compares it to the given *token* — this
    only works when you know (or can guess) the original *value*.

    Args:
        token: The base64 token to reverse.
        salt: Hex-encoded HMAC salt (must match the tokenization salt).
        value: Candidate plaintext to verify.
        store: Optional KV store for O(1) reverse lookup.

    Returns:
        The original plaintext string.

    Raises:
        SecurityError: If the token cannot be verified.
    """
    if store is not None:
        stored = store.get(f"pii:{token}")
        if stored is not None:
            return stored

    # Deterministic re-compute: check whether candidate value matches token.
    observed = tokenize_pii(value, salt)
    if not hmac.compare_digest(observed, token):
        msg = "Token does not match the provided value"
        raise SecurityError(msg)

    return value
