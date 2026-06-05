#!/usr/bin/env python3
"""Vilona Trade FX — License Manager.
Generate, list, revoke API keys via bridge admin endpoint.
Dipanggil dari vilona_tradefx_handler.py untuk command bot Telegram.

Flow:
  1. Admin: /genkey pro "Nama Customer" → generate key
  2. Admin: /listkeys → lihat semua key aktif
  3. Admin: /revokekey VT-PRO-XXXX → nonaktifkan key
  4. User:  /mykey → lihat license key & status
  5. Bridge: GET /signal?api_key=xxx → validate & serve tier

api_keys.json format:
{
  "keys": {
    "VT-xxx": {"tier": "pro", "label": "...", "active": true, ...}
  },
  "tiers": {...}
}
"""
import json
import os
import time
import secrets
import string
import urllib.request

BRIDGE_URL = "http://localhost:8765"
KEYS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api_keys.json")

ADMIN_CHAT_IDS = {
    "157228659": "Andik",
}

def is_admin(chat_id):
    """Check if chat_id is an admin."""
    return str(chat_id) in ADMIN_CHAT_IDS


def _load_keys():
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE) as f:
            return json.load(f)
    return {"keys": {}, "tiers": {}, "default_tier": "starter"}


def _save_keys(data):
    with open(KEYS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def generate_key(tier="pro", label="", rate_limit=None, expires="2026-12-31"):
    """Generate a new API key."""
    prefix = {"starter": "VT-FREE", "pro": "VT-PRO", "elite": "VT-ELITE"}.get(tier, "VT-FREE")
    suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    new_key = f"{prefix}-{suffix}"

    config = _load_keys()

    if rate_limit is None:
        rate_limit = {"starter": 3, "pro": 50, "elite": 200}.get(tier, 3)

    config["keys"][new_key] = {
        "tier": tier,
        "label": label or f"Customer {len(config['keys']) + 1}",
        "rate_limit": rate_limit,
        "rate_window_seconds": 86400,
        "expires": expires,
        "active": True,
        "features": config.get("tiers", {}).get(tier, {}).get("features", []),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    _save_keys(config)
    return new_key, config


def list_keys():
    """List all keys with status."""
    config = _load_keys()
    result = []
    for key, data in config.get("keys", {}).items():
        result.append({
            "key": key,
            "tier": data.get("tier", "?"),
            "label": data.get("label", ""),
            "active": data.get("active", True),
            "expires": data.get("expires", "?"),
            "rate_limit": data.get("rate_limit", "?"),
        })
    return result, config.get("tiers", {})


def revoke_key(api_key):
    """Deactivate a key (soft delete)."""
    config = _load_keys()
    if api_key in config["keys"]:
        config["keys"][api_key]["active"] = False
        _save_keys(config)
        return True, config["keys"][api_key].get("label", api_key)
    return False, None


def reactivate_key(api_key):
    """Reactivate a deactivated key."""
    config = _load_keys()
    if api_key in config["keys"]:
        config["keys"][api_key]["active"] = True
        _save_keys(config)
        return True, config["keys"][api_key].get("label", api_key)
    return False, None


def get_key_info(api_key):
    """Get info about a specific key."""
    config = _load_keys()
    if api_key in config["keys"]:
        return config["keys"][api_key]
    return None


def try_generate_via_bridge(tier, label):
    """Fallback: generate via bridge admin endpoint."""
    try:
        data = json.dumps({"tier": tier, "label": label}).encode()
        req = urllib.request.Request(f"{BRIDGE_URL}/admin/generate-key", data=data,
                                     headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=5)
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


# ── Bot command handlers ──

def cmd_genkey(chat_id, args):
    """Bot command: /genkey <tier> <label>
    Admin only. Generate new license key.
    """
    if not is_admin(chat_id):
        return "⛔ Admin only."

    tier = "pro"
    label = ""

    if args:
        parts = args.split(maxsplit=1)
        if parts[0].lower() in ("starter", "pro", "elite"):
            tier = parts[0].lower()
            label = parts[1] if len(parts) > 1 else ""
        else:
            label = args

    try:
        api_key, config = generate_key(tier=tier, label=label)
        tiers_info = config.get("tiers", {})
        tier_info = tiers_info.get(tier, {})
        max_layers = tier_info.get("max_layers", 1)
        cooldown = tier_info.get("signal_cooldown_minutes", 15)

        return (
            f"🔑 <b>License Key Generated</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<code>{api_key}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Tier: <b>{tier.upper()}</b>\n"
            f"📊 Layers: {max_layers}\n"
            f"⚡ Rate: {cooldown}min cooldown\n"
            f"🏷 Label: {label or 'N/A'}\n\n"
            f"<i>Copy key ini ke input API_Key di EA</i>"
        )
    except Exception as e:
        # Fallback to bridge
        result = try_generate_via_bridge(tier, label)
        if "api_key" in result:
            return (
                f"🔑 <b>License Key Generated (via bridge)</b>\n"
                f"<code>{result['api_key']}</code>\n"
                f"Tier: {result.get('tier', tier).upper()}"
            )
        return f"❌ Gagal generate key: {e}"


def cmd_listkeys(chat_id):
    """Bot command: /listkeys — List all license keys."""
    if not is_admin(chat_id):
        return "⛔ Admin only."

    keys, tiers = list_keys()
    if not keys:
        return "📭 Belum ada license key. Gunakan /genkey untuk membuat."

    lines = ["🔑 <b>License Keys</b>", "━━━━━━━━━━━━━━━━━━━━━"]
    for k in keys:
        status = "✅" if k["active"] else "❌"
        lines.append(f"{status} <code>{k['key']}</code>")
        lines.append(f"   {k['tier'].upper()} | {k['label']} | exp:{k['expires']}")

    lines.append(f"\n📊 Total: {len(keys)} keys")
    return "\n".join(lines)


def cmd_revokekey(chat_id, args):
    """Bot command: /revokekey <API_KEY> — Deactivate license."""
    if not is_admin(chat_id):
        return "⛔ Admin only."

    if not args:
        return "Usage: /revokekey VT-PRO-XXXX"

    api_key = args.strip()
    success, label = revoke_key(api_key)
    if success:
        return f"🔒 Key <code>{api_key}</code> ({label}) dinonaktifkan."
    return f"❌ Key tidak ditemukan: {api_key}"


def cmd_mykey(chat_id, user_key=None):
    """Bot command: /mykey — Show user's license info."""
    # This would look up the user's key from a user→key mapping
    # For now, user must provide the key
    if not user_key:
        return "Usage: /mykey VT-PRO-XXXX\n\nBelum punya? Upgrade via /subscribe"

    info = get_key_info(user_key)
    if not info:
        return f"❌ Key tidak valid: {user_key}"

    status = "✅ Active" if info.get("active") else "🔒 Revoked"
    return (
        f"🔑 <b>License Info</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Key: <code>{user_key}</code>\n"
        f"Status: {status}\n"
        f"Tier: {info.get('tier', '?').upper()}\n"
        f"Expires: {info.get('expires', '?')}\n"
        f"Rate: {info.get('rate_limit', '?')}/hari\n"
    )


# ── Test ──
if __name__ == "__main__":
    print("=== Generate Key ===")
    key, cfg = generate_key("pro", "Test Customer")
    print(f"  Key: {key}")
    print(f"  Tier: {cfg['keys'][key]['tier']}")

    print("\n=== List Keys ===")
    keys, _ = list_keys()
    for k in keys:
        print(f"  {k['key']} | {k['tier']} | active={k['active']}")
