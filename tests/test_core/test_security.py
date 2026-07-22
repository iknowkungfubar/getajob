"""Tests for the core.security module.

Covers AES-256-GCM encryption round-trips, PBKDF2 key derivation, PII
tokenization, and the test-key heuristics used to flag development keys.
"""

from __future__ import annotations as _annotations

import base64
import hashlib
import hmac

import pytest

from core.exceptions import SecurityError
from core.security import (
    _AES_KEY_LENGTH,
    _NONCE_LENGTH,
    _TAG_LENGTH,
    _TOKEN_HASH_ALGORITHM,
    _is_likely_test_key,
    decrypt_value,
    derive_key,
    detokenize_pii,
    encrypt_value,
    generate_key,
    tokenize_pii,
)


class _DictStore:
    """Minimal KV store matching the _TokenMapping protocol (set/get)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._data.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value


class TestGenerateKey:
    """Tests for generate_key -- random 256-bit key generation."""

    def test_returns_32_bytes(self) -> None:
        key = generate_key()
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_produces_different_keys(self) -> None:
        assert generate_key() != generate_key()


class TestDeriveKey:
    """Tests for derive_key -- PBKDF2-HMAC-SHA256 derivation."""

    def test_returns_key_and_salt(self) -> None:
        key, salt = derive_key("correct-horse-battery-staple")
        assert isinstance(key, bytes)
        assert len(key) == 32
        assert isinstance(salt, bytes)
        assert len(salt) == 16

    def test_random_salt_when_omitted(self) -> None:
        k1, s1 = derive_key("password")
        k2, s2 = derive_key("password")
        # Different salts produce different keys.
        assert s1 != s2
        assert k1 != k2

    def test_deterministic_with_same_salt(self) -> None:
        salt = b"0123456789abcdef"
        k1, _ = derive_key("password", salt=salt)
        k2, _ = derive_key("password", salt=salt)
        assert k1 == k2

    def test_different_password_different_key(self) -> None:
        salt = b"0123456789abcdef"
        k1, _ = derive_key("password-a", salt=salt)
        k2, _ = derive_key("password-b", salt=salt)
        assert k1 != k2

    def test_custom_iterations(self) -> None:
        # Low iteration count for test speed.
        key, salt = derive_key("test", iterations=2)
        assert len(key) == 32
        assert len(salt) == 16


class TestEncryptDecrypt:
    """Round-trip encryption / decryption with AES-256-GCM."""

    def test_round_trip(self) -> None:
        key = generate_key()
        plaintext = "hello-secret-world"
        ct = encrypt_value(plaintext, key)
        assert ct != plaintext  # Ciphertext is opaque base64.
        pt = decrypt_value(ct, key)
        assert pt == plaintext

    def test_round_trip_unicode(self) -> None:
        key = generate_key()
        plaintext = "Jalape\u00f1o\u2014\u263a\U0001f600"
        ct = encrypt_value(plaintext, key)
        pt = decrypt_value(ct, key)
        assert pt == plaintext

    def test_wrong_key_fails(self) -> None:
        k1 = generate_key()
        k2 = generate_key()
        ct = encrypt_value("secret-data", k1)
        with pytest.raises(SecurityError, match="authentication tag mismatch"):
            decrypt_value(ct, k2)

    def test_short_key_raises(self) -> None:
        with pytest.raises(SecurityError, match="key must be exactly 32 bytes"):
            encrypt_value("data", b"too-short")

    def test_short_key_decrypt_raises(self) -> None:
        with pytest.raises(SecurityError, match="key must be exactly 32 bytes"):
            decrypt_value("AAAA", b"too-short")

    def test_tampered_ciphertext_raises(self) -> None:
        key = generate_key()
        ct = encrypt_value("important", key)
        # Flip a byte in the middle.
        tampered = ct[:10] + ("X" if ct[10] != "X" else "Y") + ct[11:]
        with pytest.raises(SecurityError):
            decrypt_value(tampered, key)

    def test_truncated_ciphertext_raises(self) -> None:
        key = generate_key()
        with pytest.raises(SecurityError, match="too short"):
            decrypt_value("AAAA", key)

    def test_invalid_base64_raises(self) -> None:
        key = generate_key()
        with pytest.raises(SecurityError, match="not valid base64"):
            decrypt_value("!!!not-base64!!!", key)

    def test_encrypt_with_test_key_logs_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Encrypting with a known test key logs a warning but still succeeds."""
        # structlog's default config writes to stdout, not through logging.
        test_key = b"test_32_byte_key_for_the_test_!!"
        assert len(test_key) == 32
        pt = encrypt_value("hello", test_key)
        assert decrypt_value(pt, test_key) == "hello"
        captured = capsys.readouterr()
        assert "test or default value" in captured.out


class TestEncryptDecryptDeterministicProperties:
    """Non-determinism and output format."""

    def test_nonce_different_each_time(self) -> None:
        """Each encryption call produces a different ciphertext for the same plaintext."""
        key = generate_key()
        plaintext = "same-input"
        ct1 = encrypt_value(plaintext, key)
        ct2 = encrypt_value(plaintext, key)
        assert ct1 != ct2

    def test_output_format(self) -> None:
        """Ciphertext is URL-safe base64 without padding."""
        key = generate_key()
        ct = encrypt_value("format-test", key)
        # Should not have trailing '=' padding.
        assert not ct.endswith("=")
        # Should be valid base64.
        decoded = base64.urlsafe_b64decode(ct + "==")
        # Should contain nonce (12) + ciphertext (>= len(plaintext)) + tag (16).
        assert len(decoded) >= _NONCE_LENGTH + _TAG_LENGTH


class TestIsLikelyTestKey:
    """Heuristics for detecting test / insecure encryption keys."""

    def test_known_test_key(self) -> None:
        assert _is_likely_test_key(b"test-key-not-for-prod-32bytes!") is True

    def test_all_same_byte(self) -> None:
        assert _is_likely_test_key(b"a" * 32) is True

    def test_zeros(self) -> None:
        assert _is_likely_test_key(b"\x00" * 32) is True

    def test_placeholder_marker(self) -> None:
        # Must be exactly 32 bytes and contain "changeme".
        assert _is_likely_test_key(b"changeme_32_byte_test_key_here!!") is True

    def test_real_random_key_not_flagged(self) -> None:
        assert _is_likely_test_key(generate_key()) is False

    def test_short_key_not_flagged(self) -> None:
        """Length-check returns early (False) for non-32-byte inputs."""
        assert _is_likely_test_key(b"short") is False

    def test_non_ascii_key_not_flagged(self) -> None:
        """Binary key that can't be decoded as ASCII is not flagged."""
        assert _is_likely_test_key(b"\xff\xfe\xfd\xfc" * 8) is False


class TestTokenizePII:
    """HMAC-based PII tokenization."""

    def test_token_is_base64_string(self) -> None:
        token = tokenize_pii("user1", "aabb" * 8)
        assert isinstance(token, str)
        assert len(token) > 0
        assert not token.endswith("=")

    def test_deterministic_with_same_salt(self) -> None:
        salt = "deadbeef" * 4
        t1 = tokenize_pii("user1", salt)
        t2 = tokenize_pii("user1", salt)
        assert t1 == t2

    def test_different_salt_different_token(self) -> None:
        t1 = tokenize_pii("user1", "aabb" * 8)
        t2 = tokenize_pii("user1", "ccdd" * 8)
        assert t1 != t2

    def test_different_value_different_token(self) -> None:
        salt = "deadbeef" * 4
        t1 = tokenize_pii("alice", salt)
        t2 = tokenize_pii("bob", salt)
        assert t1 != t2

    def test_token_length(self) -> None:
        """Token is HMAC-SHA256 truncated to 16 bytes, base64 encoded."""
        token = tokenize_pii("testuser", "aabb" * 8)
        # 16 bytes base64 without padding = ceil(16*4/3) with padding stripped.
        expected_digest = hmac.digest(
            bytes.fromhex("aabb" * 8),
            b"testuser",
            _TOKEN_HASH_ALGORITHM,
        )[:_TAG_LENGTH]
        expected_token = base64.urlsafe_b64encode(expected_digest).decode().rstrip("=")
        assert token == expected_token

    def test_with_store(self) -> None:
        """When a store is passed, the mapping is saved."""
        store = _DictStore()
        token = tokenize_pii("alice", "aabb" * 8, store=store)  # type: ignore[arg-type]
        assert store.get(f"pii:{token}") == "alice"


class TestDetokenizePII:
    """Reverse tokenization."""

    def test_detokenize_with_value(self) -> None:
        salt = "deadbeef" * 4
        token = tokenize_pii("user1", salt)
        result = detokenize_pii(token, salt, "user1")
        assert result == "user1"

    def test_wrong_value_raises(self) -> None:
        salt = "deadbeef" * 4
        token = tokenize_pii("user1", salt)
        with pytest.raises(SecurityError, match="does not match"):
            detokenize_pii(token, salt, "wrong-user")

    def test_with_store(self) -> None:
        """Store lookup is preferred over re-computation."""
        salt = "deadbeef" * 4
        token = tokenize_pii("user1", salt)
        store = _DictStore()
        store.set(f"pii:{token}", "stored-value")
        result = detokenize_pii(token, salt, "anything", store=store)  # type: ignore[arg-type]
        assert result == "stored-value"
