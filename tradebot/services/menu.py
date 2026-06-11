"""Unified menu system — categorized inline keyboard menus with role-based views.

Provides menu layouts, inline keyboard builders, and navigation handlers
for the single unified Telegram bot.
"""

from __future__ import annotations

from typing import Any

# ── Menu Structure ────────────────────────────────────────────────────
# Each menu is a list of (callback_data, label) tuples.
# "rows" groups buttons by row.

MAIN_MENU: list[list[tuple[str, str]]] = [
    [("menu:signals", "🧠 SIGNAL SYSTEM"), ("menu:market", "📊 MARKET DATA")],
    [("menu:history", "📈 TRADE HISTORY"), ("menu:account", "👤 ACCOUNT")],
    [("menu:stockity", "💰 STOCKITY INSIDER")],
    [("menu:help", "❓ HELP")],
]

ADMIN_MENU: list[list[tuple[str, str]]] = [
    [("menu:signals", "🧠 SIGNAL SYSTEM"), ("menu:market", "📊 MARKET DATA")],
    [("menu:history", "📈 TRADE HISTORY"), ("menu:account", "👤 ACCOUNT")],
    [("menu:stockity", "💰 STOCKITY INSIDER")],
    [("menu:admin", "⚙️ ADMIN"), ("menu:help", "❓ HELP")],
]

SIGNAL_MENU: list[list[tuple[str, str]]] = [
    [("cmd:signal", "🎯 Signal MTF+9 Engines")],
    [("cmd:mtf", "🧬 Matrix 5TF")],
    [("cmd:engines", "🔧 Engine Consensus")],
    [("cmd:structure", "🏗 Market Structure")],
    [("cmd:pulse", "🔄 Market Pulse")],
    [("menu:main", "🔙 Back")],
]

MARKET_MENU: list[list[tuple[str, str]]] = [
    [("cmd:data", "📊 Market Data"), ("cmd:price gold", "🥇 Gold")],
    [("cmd:price btc", "₿ BTC"), ("cmd:price eth", "⟠ ETH")],
    [("cmd:killzone", "🎯 Killzone"), ("cmd:zones", "🧲 Liquidity Zones")],
    [("cmd:levels", "🏛 S&R Levels"), ("cmd:news", "📰 Market News")],
    [("cmd:session", "🕐 Session Levels")],
    [("menu:main", "🔙 Back")],
]

HISTORY_MENU: list[list[tuple[str, str]]] = [
    [("cmd:winrate", "📈 Win Rate"), ("cmd:recap", "📋 Daily Recap")],
    [("cmd:history", "📜 Trade History"), ("cmd:mapping", "🗺️ Mapping")],
    [("menu:main", "🔙 Back")],
]

ACCOUNT_MENU: list[list[tuple[str, str]]] = [
    [("cmd:status", "📊 Status"), ("cmd:subscribe", "⭐ Subscribe")],
    [("menu:donate", "💚 Donate"), ("cmd:mykey", "🔑 My Key")],
    [("cmd:analyze gold", "🔍 Analyze"), ("cmd:autosync", "🔄 Autosync")],
    [("cmd:myid", "🆔 My ID"), ("cmd:trailing", "🏃 Trailing Status")],
    [("cmd:settings", "⚙️ Settings")],
    [("menu:main", "🔙 Back")],
]

DONATE_MENU: list[list[tuple[str, str]]] = [
    [("sub:pro", "☕️ Kopi (Rp 15K)")],
    [("sub:pro", "🍱 Makan Siang (Rp 25K)")],
    [("sub:elite", "🚀 Bensin Full (Rp 50K)")],
    [("sub:lifetime", "💰 Nominal Bebas"), ("cancel_input", "❌ Batal")],
    [("menu:main", "🔙 Back")],
]

STOCKITY_MENU: list[list[tuple[str, str] | tuple[str, str, str]]] = [
    [("__url__", "🚀 Daftar Stockity", "https://stockity-mr.com/auth?invite_code=7b8730c84b6450e3e0b02fd3fd864f69#SignUp")],
    [("cmd:status", "📊 Status Akun")],
    [("menu:main", "🔙 Back")],
]

ADMIN_PANEL_MENU: list[list[tuple[str, str]]] = [
    [("cmd:dashboard", "📊 Dashboard"), ("cmd:bridge_status", "🌉 Bridge")],
    [("cmd:genkey", "🔑 Gen Key"), ("cmd:activate", "⭐ Activate")],
    [("cmd:restart_bot", "🔄 Restart Bot")],
    [("menu:main", "🔙 Back")],
]

HELP_MENU: list[list[tuple[str, str]]] = [
    [("cmd:start", "📖 Start"), ("cmd:help", "📚 All Commands")],
    [("cmd:symbols", "📋 Symbols"), ("cmd:ea", "📥 Download EA")],
    [("cmd:bridge_full_status", "🛡️ Bridge Status")],
    [("menu:main", "🔙 Back")],
]

TRADE_MENU: list[list[tuple[str, str]]] = [
    [("trade:yes", "✅ Trade"), ("trade:no", "⏭ Skip")],
]


def build_keyboard(
    menu: list[list[tuple[str, str] | tuple[str, str, str]]],
) -> list[list[dict[str, str]]]:
    """Convert menu tuples into Telegram InlineKeyboardMarkup format.

    Each row item can be:
      - (callback_data, label)           → callback button
      - ("__url__", label, url)          → URL button (opens in browser)
    """
    result: list[list[dict[str, str]]] = []
    for row in menu:
        buttons: list[dict[str, str]] = []
        for item in row:
            if len(item) == 3 and item[0] == "__url__":
                buttons.append({"text": item[1], "url": item[2]})
            else:
                buttons.append({"text": item[1], "callback_data": item[0]})
        result.append(buttons)
    return result


def get_inline_keyboard(menu_name: str) -> dict[str, list[list[dict[str, str]]]]:
    """Get an inline keyboard for a named menu."""
    menus: dict[str, list[list[tuple[str, str] | tuple[str, str, str]]]] = {
        "main": MAIN_MENU,
        "admin": ADMIN_MENU,
        "signals": SIGNAL_MENU,
        "market": MARKET_MENU,
        "history": HISTORY_MENU,
        "account": ACCOUNT_MENU,
        "donate": DONATE_MENU,
        "stockity": STOCKITY_MENU,
        "admin_panel": ADMIN_PANEL_MENU,
        "help": HELP_MENU,
        "trade": TRADE_MENU,
    }
    menu = menus.get(menu_name, MAIN_MENU)
    return {"inline_keyboard": build_keyboard(menu)}


def get_menu_text(menu_name: str, user_info: dict[str, Any] | None = None) -> str:
    """Get welcome text for a specific menu."""
    texts: dict[str, str] = {
        "main": (
            "🔥 <b>VILONA AI — COMMAND CENTER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Pilih menu di bawah untuk memulai:"
        ),
        "signals": (
            "🧠 <b>SIGNAL SYSTEM</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "AI MTF Top-Down Analysis dengan 9 engines."
        ),
        "market": (
            "📊 <b>MARKET DATA</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Harga real-time, killzone, dan data pasar."
        ),
        "history": (
            "📈 <b>TRADE HISTORY</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Win rate, daily recap, dan mapping."
        ),
        "account": (
            "👤 <b>ACCOUNT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Status, subscribe, donate, dan pengaturan."
        ),
        "donate": (
            "💚 <b>SUBSCRIBE — VILONA AI</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Dukung server AI tetap hidup!\n"
            "Pilih nominal di bawah:"
        ),
        "stockity": (
            "💰 <b>STOCKITY INSIDER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Kami menggunakan sistem bandar (insider) untuk\n"
            "meningkatkan akurasi sinyal. Pastikan untuk mendaftar\n"
            "menggunakan kode referral di bawah ini!"
        ),
        "admin_panel": (
            "⚙️ <b>ADMIN PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Manajemen bot dan pengguna."
        ),
        "help": (
            "❓ <b>HELP</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Gunakan menu atau ketik command langsung."
        ),
    }
    return texts.get(menu_name, texts["main"])


def is_admin(chat_id: str, admin_ids: list[str] | None = None) -> bool:
    """Check if a chat_id has admin privileges."""
    if not admin_ids:
        return False
    return chat_id in admin_ids or str(chat_id) in admin_ids


__all__ = [
    "ADMIN_MENU",
    "MAIN_MENU",
    "SIGNAL_MENU",
    "MARKET_MENU",
    "HISTORY_MENU",
    "ACCOUNT_MENU",
    "DONATE_MENU",
    "STOCKITY_MENU",
    "ADMIN_PANEL_MENU",
    "HELP_MENU",
    "TRADE_MENU",
    "get_inline_keyboard",
    "get_menu_text",
    "is_admin",
    "build_keyboard",
]
