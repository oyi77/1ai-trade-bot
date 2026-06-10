#!/usr/bin/env python3
"""
secret_sanitizer.py — Middleware keamanan untuk Hermes AI Agent.
Memantau dan menyaring kredensial yang dikirim user melalui Telegram
SEBELUM masuk ke LLM/MEMORY.md.
"""

import os
import re
import json
import stat
from pathlib import Path
from datetime import datetime, timezone

# ── Pola Deteksi Kredensial ──────────────────────────────────────────────────
# Pola ini menangani format umum exchange API key, secret key, dan crypto private key.
# Jangan terlalu longgar agar false positive minim.
PATTERNS = [
    # Pattern 1: API key / Secret key alfanumerik panjang (32-64 karakter)
    # Bisa berupa kata "API_KEY" diikuti "=" atau ":" + panjang 32-64 alfanumerik
    re.compile(
        r"(?i)(api_key|apikey|secret|private_key|access_key|token)\s*[:=]?\s*"
        r"([A-Za-z0-9]{32,64})\b"
    ),
    # Pattern 2: Private key crypto hex 64 karakter (tanpa 0x)
    # Contoh: 4a1b...c3f0 (64 hex chars)
    re.compile(
        r"\b([a-fA-F0-9]{64})\b"
    ),
    # Pattern 3: Private key crypto hex 66 karakter (dengan 0x)
    re.compile(
        r"\b(0x[a-fA-F0-9]{64})\b"
    ),
    # Pattern 4: Base64-like 44-char (AWS secret, generic 32-byte base64)
    re.compile(
        r"\b([A-Za-z0-9+/]{40,}={0,2})\b"
    ),
    # Pattern 5: SSH private key header (PEM block detector)
    re.compile(
        r"-----BEGIN ([A-Z ]+PRIVATE KEY)-----[A-Za-z0-9+/=\n\r -]+-----END \1-----"
    ),
    # Pattern 6: Generic long hex 128+ chars (potential seed phrase share)
    re.compile(
        r"\b([a-fA-F0-9]{128,})\b"
    ),
]

# Direktori penyimpanan kredensial yang di-intercept
SECRETS_DIR = Path.home() / ".openclaw" / "workspace" / "secrets"
SECRETS_FILE = SECRETS_DIR / "intercepted_keys.json"


def _ensure_secrets_dir():
    """Buat direktori secrets dengan permission 700 jika belum ada."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)  # 700


def _store_key(key_value: str) -> str:
    """
    Simpan key asli ke file terpisah dengan format:
    intercepted_key_<last4>.txt
    Return nama file yang dibuat.
    """
    _ensure_secrets_dir()

    last4 = key_value[-4:] if len(key_value) >= 4 else key_value
    # Sanitasi last4 agar valid sebagai nama file
    safe_last4 = re.sub(r"[^A-Za-z0-9]", "_", last4)
    key_file = SECRETS_DIR / f"intercepted_key_{safe_last4}.txt"

    # Tulis key asli ke file terpisah (atomic-ish)
    tmp_file = key_file.with_suffix(".tmp")
    tmp_file.write_text(key_value, encoding="utf-8")
    os.chmod(tmp_file, stat.S_IRUSR | stat.S_IWUSR)  # 600
    tmp_file.replace(key_file)

    # Juga catat di log JSON (tanpa menyimpan full key di sini untuk keamanan ganda)
    log_path = SECRETS_DIR / "intercepted_keys.json"
    existing = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []

    entry = {
        "file": str(key_file),
        "last4": safe_last4,
        "length": len(key_value),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "source": "telegram_input",
    }
    # HAPUS REDACTION: kita menyimpan full key sesuai permintaan user
    existing.append(entry)
    tmp_log = log_path.with_suffix(".tmp")
    tmp_log.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    os.chmod(tmp_log, stat.S_IRUSR | stat.S_IWUSR)  # 600
    tmp_log.replace(log_path)

    return str(key_file)


def _redact_match(m: re.Match) -> str:
    """
    Ganti match dengan placeholder aman.
    Prioritas: cek grup 2 (value setelah key=), fallback ke grup 1 (PEM block).
    """
    if m.lastindex is not None and m.lastindex >= 2 and m.group(2):
        key_value = m.group(2)
    else:
        key_value = m.group(1)
    key_value = key_value or ""
    if not key_value:
        return m.group(0)
    stored_path = _store_key(key_value)
    last4 = key_value[-4:] if len(key_value) >= 4 else key_value
    return f"[REDACTED_KEY_LAST_4: ****{last4}]"


def sanitize_telegram_input(text: str) -> str:
    """
    Intercept kredensial dari input Telegram user.
    - Deteksi via regex
    - Simpan key asli ke ~/.openclaw/workspace/secrets/intercepted_key_<last4>.txt
    - Ganti dengan placeholder aman
    Returns:
        string yang sudah dibersihkan (aman untuk LLM/MEMORY)
    """
    if not isinstance(text, str):
        return text or ""

    result = text
    for pattern in PATTERNS:
        result = pattern.sub(_redact_match, result)

    return result


# ── Debug helper (hanya untuk admin/testing, jangan dipanggil di production path) ─
def list_intercepted_keys(limit: int = 20):
    """List recent intercepted keys dari log JSON (metadata only)."""
    if not SECRETS_FILE.exists():
        return []
    try:
        data = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return data[-limit:]
    except Exception:
        return []
