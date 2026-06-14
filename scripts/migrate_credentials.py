#!/usr/bin/env python3
"""
One-time migration — encrypt plaintext credentials in user_platforms.

Reads every row in the user_platforms table and encrypts the email,
password, and credentials columns using the VILONA_MASTER_KEY from
the environment.

Safety features:
    - Automatic backup of tradebot.db BEFORE any writes (shutil.copy)
    - Skips already-encrypted rows (detected by Fernet gAAAAA prefix)
    - Dry-run mode (--dry-run) to preview changes without writing
    - Per-row error handling — one corrupt row does not abort migration

Usage:
    # Set the key, then run:
    export VILONA_MASTER_KEY="your-base64-fernett-key-here"
    python scripts/migrate_credentials.py

    # Preview what would change:
    python scripts/migrate_credentials.py --dry-run

    # Custom DB path:
    python scripts/migrate_credentials.py --db /path/to/tradebot.db
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WIB = timezone(timedelta(hours=7))

# The Fernet magic prefix
_FERNET_MAGIC = b"gAAAAA"

# Columns to encrypt
_ENCRYPTED_COLS = ("email", "password", "credentials")


def _is_encrypted(value: str | None) -> bool:
    """Check if a value is already a Fernet token."""
    if not value:
        return False
    return value.encode("utf-8").startswith(_FERNET_MAGIC)


def _all_encrypted(row: dict) -> bool:
    """Check if all sensitive columns in a row are already encrypted."""
    return all(
        not row.get(col) or _is_encrypted(row[col])
        for col in _ENCRYPTED_COLS
    )


def _backup_db(db_path: Path) -> Path:
    """Create a timestamped backup of the database."""
    ts = datetime.now(WIB).strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".backup_{ts}.db")
    shutil.copy2(db_path, backup_path)
    return backup_path


def migrate(db_path: Path, dry_run: bool = False) -> int:
    """Encrypt plaintext email/password/credentials in user_platforms.

    Returns:
        Number of rows migrated.
    """
    from tradebot.security.crypto import FernetEncryptor

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        return 0

    encryptor = FernetEncryptor()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Check if table exists
    table_check = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='user_platforms'"
    ).fetchone()
    if not table_check:
        print("Table 'user_platforms' does not exist — nothing to migrate.")
        conn.close()
        return 0

    rows = conn.execute("SELECT * FROM user_platforms").fetchall()
    conn.close()

    if not rows:
        print("Table 'user_platforms' is empty — nothing to migrate.")
        return 0

    to_migrate = []
    already_encrypted = 0
    for row in rows:
        d = dict(row)
        if _all_encrypted(d):
            already_encrypted += 1
        else:
            to_migrate.append(d)

    print(f"Found {len(rows)} total rows.")
    print(f"  Already encrypted: {already_encrypted}")
    print(f"  Pending migration: {len(to_migrate)}")

    if not to_migrate:
        print("Nothing to migrate.")
        return 0

    if dry_run:
        print("\n[DRY RUN] Would encrypt these rows:")
        for row in to_migrate:
            uid = row.get("user_id", "?")
            plat = row.get("platform", "?")
            label = row.get("label", "?")
            has_email = bool(row.get("email"))
            has_pass = bool(row.get("password"))
            has_creds = bool(row.get("credentials"))
            print(
                f"  user={uid} platform={plat} label={label} "
                f"email={'PLAINTEXT' if has_email and not _is_encrypted(row.get('email','')) else '—'} "
                f"password={'PLAINTEXT' if has_pass and not _is_encrypted(row.get('password','')) else '—'} "
                f"credentials={'PLAINTEXT' if has_creds and not _is_encrypted(row.get('credentials','')) else '—'}"
            )
        print(f"\nRun without --dry-run to apply migration.")
        return 0

    # Backup
    backup_path = _backup_db(db_path)
    print(f"\nBackup created: {backup_path}")

    # Migrate
    migrated = 0
    conn = sqlite3.connect(str(db_path))
    failures = 0

    for row in to_migrate:
        row_id = row["id"]
        try:
            enc_email = encryptor.encrypt_string(row.get("email", "")) if row.get("email") else ""
            enc_pass = encryptor.encrypt_string(row.get("password", "")) if row.get("password") else ""
            enc_creds = encryptor.encrypt_string(row.get("credentials", "{}")) if row.get("credentials") else "{}"

            conn.execute(
                """UPDATE user_platforms
                   SET email=?, password=?, credentials=?
                   WHERE id=?""",
                (enc_email, enc_pass, enc_creds, row_id),
            )
            migrated += 1
        except Exception as exc:
            failures += 1
            print(f"  FAILED row id={row_id}: {exc}")

    conn.commit()
    conn.close()

    print(f"\nMigration complete: {migrated} rows encrypted, {failures} failures.")
    if failures:
        print("WARNING: Some rows failed. Check the output above.")

    # Verify
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    post_rows = conn.execute("SELECT * FROM user_platforms").fetchall()
    in_the_clear = 0
    for r in post_rows:
        d = dict(r)
        if d.get("email") and not _is_encrypted(d["email"]):
            in_the_clear += 1
        elif d.get("password") and not _is_encrypted(d["password"]):
            in_the_clear += 1
        elif d.get("credentials") and d["credentials"] != "{}" and not _is_encrypted(d["credentials"]):
            in_the_clear += 1
    conn.close()

    if in_the_clear > 0:
        print(f"WARNING: {in_the_clear} field(s) still in plaintext after migration!")
    else:
        print("Verification passed — all sensitive fields are now encrypted.")

    return migrated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Encrypt plaintext credentials in user_platforms table."
    )
    parser.add_argument(
        "--db",
        type=str,
        default="",
        help="Path to tradebot.db (default: data/tradebot.db under project root)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying the database",
    )
    args = parser.parse_args()

    # Resolve DB path
    project_root = Path(__file__).resolve().parent.parent
    db_path = Path(args.db) if args.db else project_root / "data" / "tradebot.db"

    print(f"Database: {db_path}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    count = migrate(db_path, dry_run=args.dry_run)
    if count > 0 and not args.dry_run:
        print(f"\nMigration successful. Backup is in the same directory as the database.")
    elif count == 0 and not args.dry_run and not args.db:
        # All rows already encrypted — clean exit
        pass


if __name__ == "__main__":
    main()
