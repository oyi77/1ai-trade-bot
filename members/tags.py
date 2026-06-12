#!/usr/bin/env python3
"""Behavioral Tagging — Auto-segment users based on command usage.

Import:
    from members.tags import add_tag, get_tags, has_tag, tag_user

Usage:
    add_tag(chat_id, "gold_trader")     # after /analyze xauusd
    add_tag(chat_id, "crypto_trader")   # after /analyze btc
    add_tag(chat_id, "technical_geek")  # after /mtf
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("vtfx-tags")

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx"
DATA_DIR.mkdir(parents=True, exist_ok=True)
TAGS_FILE = DATA_DIR / "user_tags.json"


def _load_tags() -> dict[str, list[str]]:
    """Load all user tags from disk. Returns {chat_id: [tag1, tag2, ...]}."""
    if TAGS_FILE.exists():
        try:
            return json.loads(TAGS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_tags(data: dict):
    """Persist tags to disk."""
    try:
        TAGS_FILE.write_text(json.dumps(data, ensure_ascii=False))
    except Exception as e:
        logger.error("Failed to save tags: %s", e)


def add_tag(chat_id: str, tag: str) -> bool:
    """Add a tag to a user. Returns True if newly added, False if already exists."""
    chat_id = str(chat_id)
    tags = _load_tags()
    user_tags = tags.get(chat_id, [])
    if tag not in user_tags:
        user_tags.append(tag)
        tags[chat_id] = user_tags
        _save_tags(tags)
        logger.info("Tag added: %s → %s", chat_id, tag)
        return True
    return False


def get_tags(chat_id: str) -> list[str]:
    """Get all tags for a user."""
    tags = _load_tags()
    return tags.get(str(chat_id), [])


def has_tag(chat_id: str, tag: str) -> bool:
    """Check if user has a specific tag."""
    return tag in get_tags(chat_id)


def tag_user(chat_id: str, tags_list: list[str]):
    """Add multiple tags at once. Deduplicates automatically."""
    for tag in tags_list:
        add_tag(chat_id, tag)


def get_users_by_tag(tag: str) -> list[str]:
    """Get all chat_ids that have a specific tag. For future broadcast use."""
    tags = _load_tags()
    return [cid for cid, tlist in tags.items() if tag in tlist]
