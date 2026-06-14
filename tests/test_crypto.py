"""Tests for tradebot/security/crypto.py — Fernet encryption utilities."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from tradebot.security.crypto import (
    CryptoError,
    FernetEncryptor,
    get_encryptor,
    reset_encryptor,
)

# A valid 32-byte key for testing (generated once)
TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset_crypto_state():
    """Ensure clean encryptor state for each test."""
    reset_encryptor()
    # Ensure no residual env var
    old = os.environ.pop("VILONA_MASTER_KEY", None)
    yield
    reset_encryptor()
    if old:
        os.environ["VILONA_MASTER_KEY"] = old
    else:
        os.environ.pop("VILONA_MASTER_KEY", None)


# ═══════════════════════════════════════════════════════════════════
#  INITIALIZATION
# ═══════════════════════════════════════════════════════════════════


class TestFernetEncryptorInit:
    """FernetEncryptor initialization and key validation."""

    def test_init_with_explicit_key(self):
        """Can be initialized with an explicit Fernet key."""
        enc = FernetEncryptor(key=TEST_KEY)
        assert enc.raw_key == TEST_KEY.encode()

    def test_init_from_env(self):
        """Reads key from VILONA_MASTER_KEY env var."""
        os.environ["VILONA_MASTER_KEY"] = TEST_KEY
        enc = FernetEncryptor()
        assert enc.raw_key == TEST_KEY.encode()

    def test_init_missing_env_crashes(self):
        """Raises CryptoError if VILONA_MASTER_KEY is not set."""
        with pytest.raises(CryptoError, match="Missing VILONA_MASTER_KEY"):
            FernetEncryptor()

    def test_init_invalid_base64_crashes(self):
        """Raises CryptoError if key decodes to wrong length."""
        os.environ["VILONA_MASTER_KEY"] = "not-base64!!!"
        with pytest.raises(CryptoError, match="32 bytes"):
            FernetEncryptor()

    def test_init_wrong_length_key_crashes(self):
        """Raises CryptoError if decoded key is not 32 bytes."""
        # Short base64 that decodes to < 32 bytes
        short = "YWJj"  # decodes to b"abc" (3 bytes)
        os.environ["VILONA_MASTER_KEY"] = short
        with pytest.raises(CryptoError, match="32 bytes"):
            FernetEncryptor()

    def test_generate_key_produces_valid_key(self):
        """generate_key() creates a key accepted by init."""
        key = FernetEncryptor.generate_key()
        enc = FernetEncryptor(key=key)
        # Round-trip works
        enc.decrypt_string(enc.encrypt_string("hello"))


# ═══════════════════════════════════════════════════════════════════
#  ENCRYPT / DECRYPT
# ═══════════════════════════════════════════════════════════════════


class TestEncryptDecrypt:
    """Core encrypt/decrypt round-trip behavior."""

    @pytest.fixture
    def enc(self) -> FernetEncryptor:
        return FernetEncryptor(key=TEST_KEY)

    def test_roundtrip_simple(self, enc):
        """Encrypt then decrypt returns original."""
        plain = "my secret password"
        cipher = enc.encrypt_string(plain)
        assert cipher != plain
        assert cipher.startswith("gAAAAA")
        assert enc.decrypt_string(cipher) == plain

    def test_roundtrip_empty(self, enc):
        """Empty string passes through unchanged."""
        assert enc.encrypt_string("") == ""
        assert enc.decrypt_string("") == ""

    def test_roundtrip_json(self, enc):
        """JSON payloads encrypt and decrypt correctly."""
        import json
        data = json.dumps({"cookie": "authtoken=abc; userId=123", "secret": "xyz"})
        cipher = enc.encrypt_string(data)
        assert enc.decrypt_string(cipher) == data

    def test_roundtrip_unicode(self, enc):
        """Unicode strings survive encryption."""
        plain = "pässwörd_日本語_🔥"
        cipher = enc.encrypt_string(plain)
        assert enc.decrypt_string(cipher) == plain

    def test_roundtrip_long_string(self, enc):
        """Long strings (like full cookies) round-trip correctly."""
        plain = "x" * 4096
        cipher = enc.encrypt_string(plain)
        assert enc.decrypt_string(cipher) == plain

    def test_decrypt_plaintext_passthrough(self, enc):
        """Plain text (not a Fernet token) is returned as-is."""
        plain = "this is not encrypted"
        assert enc.decrypt_string(plain) == plain

    def test_decrypt_wrong_key_raises(self, enc):
        """Decrypting with a different key raises CryptoError."""
        other = FernetEncryptor(key=Fernet.generate_key().decode())
        cipher = other.encrypt_string("secret")
        with pytest.raises(CryptoError, match="token is invalid"):
            enc.decrypt_string(cipher)

    def test_decrypt_corrupt_token_raises(self, enc):
        """Corrupt ciphertext raises CryptoError."""
        with pytest.raises(CryptoError, match="token is invalid"):
            enc.decrypt_string("gAAAAAcorrupted")

    def test_is_encrypted_detects_fernett_token(self):
        """is_encrypted returns True only for Fernet tokens."""
        assert FernetEncryptor.is_encrypted("gAAAAA12345") is True
        assert FernetEncryptor.is_encrypted("plaintext") is False
        assert FernetEncryptor.is_encrypted("") is False
        assert FernetEncryptor.is_encrypted("{}") is False

    def test_encrypt_is_deterministically_random(self, enc):
        """Each encryption call produces a unique token (Fernet includes timestamp + IV)."""
        plain = "same text"
        c1 = enc.encrypt_string(plain)
        c2 = enc.encrypt_string(plain)
        assert c1 != c2
        # But both decrypt to the same plaintext
        assert enc.decrypt_string(c1) == plain
        assert enc.decrypt_string(c2) == plain


# ═══════════════════════════════════════════════════════════════════
#  SINGLETON
# ═══════════════════════════════════════════════════════════════════


class TestGetEncryptor:
    """get_encryptor singleton factory."""

    def test_get_encryptor_returns_same_instance(self):
        """get_encryptor returns the cached instance."""
        os.environ["VILONA_MASTER_KEY"] = TEST_KEY
        e1 = get_encryptor()
        e2 = get_encryptor()
        assert e1 is e2

    def test_reset_encryptor_creates_new_instance(self):
        """reset_encryptor clears the cache."""
        os.environ["VILONA_MASTER_KEY"] = TEST_KEY
        e1 = get_encryptor()
        reset_encryptor()
        e2 = get_encryptor()
        assert e1 is not e2

    def test_get_encryptor_crashes_without_key(self):
        """get_encryptor also crashes if no key is set."""
        with pytest.raises(CryptoError, match="Missing VILONA_MASTER_KEY"):
            get_encryptor()
