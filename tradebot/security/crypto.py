"""
AES-256 symmetric encryption for credentials at rest.

Uses Fernet (AES-128-CBC with HMAC-SHA256 authentication) from the
cryptography library. Master key is sourced from the VILONA_MASTER_KEY
environment variable — the system WILL NOT start without it.

Usage:
    encryptor = get_encryptor()
    cipher_text = encryptor.encrypt_string("plaintext")
    plain_text = encryptor.decrypt_string(cipher_text)
"""

from __future__ import annotations

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

LOG = logging.getLogger(__name__)

_ENV_KEY = "VILONA_MASTER_KEY"

# Fernet-encrypted strings always start with this version prefix
_FERNET_MAGIC = b"gAAAAA"

# Cache — one encryptor per process
_encryptor: FernetEncryptor | None = None


class CryptoError(Exception):
    """Raised on encryption/decryption failures."""


class FernetEncryptor:
    """Thin wrapper around cryptography.fernet.Fernet with strict env-key policy."""

    def __init__(self, key: bytes | str | None = None) -> None:
        if key is None:
            key = self._load_key_from_env()

        if isinstance(key, str):
            key = key.encode("utf-8")

        self._raw_key = key
        self._cipher = Fernet(self._ensure_urlsafe(key))
        LOG.info("FernetEncryptor initialized — credentials encryption active")

    # ── key management ──────────────────────────────────────────────

    @staticmethod
    def _load_key_from_env() -> bytes:
        raw = os.environ.get(_ENV_KEY)
        if not raw:
            raise CryptoError(
                f"Missing {_ENV_KEY} environment variable. "
                f"Generate with: python -c "
                f"\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        # Accept both raw Fernet key and base64-encoded forms
        stripped = raw.strip()
        try:
            # Validate by attempting decode
            base64.urlsafe_b64decode(stripped + "=" * (-len(stripped) % 4))
        except Exception as exc:
            raise CryptoError(
                f"{_ENV_KEY} is not valid base64. "
                f"Generate a proper key with Fernet.generate_key()."
            ) from exc
        return stripped.encode("utf-8")

    @staticmethod
    def _ensure_urlsafe(key: bytes) -> bytes:
        """Re-pad/convert key to urlsafe base64 if needed."""
        decoded = base64.urlsafe_b64decode(key + b"=" * (-len(key) % 4))

        # Fernet keys must be exactly 32 bytes of raw key material.
        if len(decoded) != 32:
            raise CryptoError(
                f"Fernet key must decode to 32 bytes, got {len(decoded)}. "
                f"Ensure you copied the full Fernet.generate_key() output."
            )

        return base64.urlsafe_b64encode(decoded)

    # ── core operations ─────────────────────────────────────────────

    def encrypt_string(self, text: str) -> str:
        """Encrypt a plaintext string. Returns Fernet token as str."""
        if not text:
            return ""
        try:
            token = self._cipher.encrypt(text.encode("utf-8"))
            return token.decode("utf-8")
        except Exception as exc:
            raise CryptoError(f"Encryption failed: {exc}") from exc

    def decrypt_string(self, cipher_text: str) -> str:
        """Decrypt a Fernet token back to plaintext.

        Returns the input unchanged if it's empty or already looks like plaintext
        (not a Fernet token). This lets us handle mixed encrypted/unencrypted
        data during migration.
        """
        if not cipher_text:
            return ""

        # If it doesn't look like a Fernet token, assume plaintext
        cbytes = cipher_text.encode("utf-8") if isinstance(cipher_text, str) else cipher_text
        if not cbytes.startswith(_FERNET_MAGIC):
            return cipher_text if isinstance(cipher_text, str) else cipher_text.decode("utf-8")

        try:
            plain = self._cipher.decrypt(cbytes)
            return plain.decode("utf-8")
        except InvalidToken:
            raise CryptoError(
                "Decryption failed — token is invalid. "
                "The ciphertext may be corrupt or encrypted with a different key."
            )
        except Exception as exc:
            raise CryptoError(f"Decryption failed: {exc}") from exc

    @property
    def raw_key(self) -> bytes:
        return self._raw_key

    @staticmethod
    def is_encrypted(value: str) -> bool:
        """Check if a string appears to be a Fernet token."""
        if not value:
            return False
        return value.encode("utf-8").startswith(_FERNET_MAGIC)

    # ── class factory ───────────────────────────────────────────────

    @classmethod
    def generate_key(cls) -> str:
        """Generate a new Fernet key. Use this once, then set VILONA_MASTER_KEY."""
        return Fernet.generate_key().decode("utf-8")


def get_encryptor() -> FernetEncryptor:
    """Get the process-wide encryptor singleton."""
    global _encryptor
    if _encryptor is None:
        _encryptor = FernetEncryptor()
    return _encryptor


def reset_encryptor() -> None:
    """Reset the cached encryptor (mainly for testing)."""
    global _encryptor
    _encryptor = None
