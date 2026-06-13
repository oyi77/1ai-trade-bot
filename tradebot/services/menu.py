"""Unified menu system — categorized inline keyboard menus with role-based views."""

from __future__ import annotations

from typing import Any

# ── Menu Structure ──
MAIN_MENU: list[list[tuple[str, str]]] = [
    [("menu:signals", "🧠 SIGNAL SYSTEM"), ("menu:market", "📊 MARKET DATA")],
    [("menu:history", "📈 TRADE HISTORY"), ("menu:account", "👤 ACCOUNT")],
    [("menu:help", "❓ HELP")],
]

ADMIN_MENU: list[list[tuple[str, str]]] = [
    [("menu:signals", "🧠 SIGNAL SYSTEM"), ("menu:market", "📊 MARKET DATA")],
    [("menu:history", "📈 TRADE HISTORY"), ("menu:account", "👤 ACCOUNT")],
    [("menu:admin_panel", "⚙️ ADMIN"), ("menu:help", "❓ HELP")],
]

SIGNAL_MENU: list[list[tuple[str, str]]] = [
    [("cmd:signal", "🎯 Live Signal (Multi-Market)")],
    [("menu:analysis", "🔬 Technical Analysis Tools")],
    [("menu:stockity", "💰 STOCKITY INSIDER")],
    [("menu:main", "🔙 Back")],
]

ANALYSIS_MENU: list[list[tuple[str, str]]] = [
    [("cmd:mtf", "🧬 Matrix 5TF (MTF)"), ("cmd:engines", "🔧 Engine Consensus")],
    [("cmd:structure", "🏗 Market Structure"), ("cmd:pulse", "🔄 Market Pulse")],
    [("menu:signals", "🔙 Back")],
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
    [("cmd:earnings", "💰 Earnings"), ("cmd:buykey", "🔑 Buy EA Key")],
    [("cmd:analyze gold", "🔍 Analyze"), ("cmd:autosync", "🔄 Autosync")],
    [("cmd:myid", "🆔 My ID"), ("cmd:trailing", "🏃 Trailing Status")],
    [("cmd:settrailing", "⚡ Set Trailing"), ("cmd:settings", "⚙️ Settings")],
    [("menu:main", "🔙 Back")],
]

PLATFORMS_MENU: list[list[tuple[str, str]]] = [
    [("cmd:platforms", "🔗 Linked Platforms")],
    [("cmd:link", "➕ Link Platform")],
    [("cmd:unlink", "➖ Unlink Platform")],
    [("sub:signal_only:weekly", "📡 Signal Only Rp50K/mg")],
    [("sub:signal_execute:weekly", "🤖 Signal+Execute Rp75K/mg")],
    [("menu:subscriptions", "💳 All Plans")],
    [("menu:account", "🔙 Back")],
]

SUBSCRIPTIONS_MENU: list[list[tuple[str, str]]] = [
    [("sub:signal_only:weekly", "📡 Signal Only — Rp50K/mg")],
    [("sub:signal_only:monthly", "📡 Signal Only — Rp100K/bln")],
    [("sub:signal_only:lifetime", "📡 Signal Only — Rp300K")],
    [("sub:signal_execute:weekly", "🤖 Signal+Execute — Rp75K/mg")],
    [("sub:signal_execute:monthly", "🤖 Signal+Execute — Rp200K/bln")],
    [("sub:signal_execute:lifetime", "🤖 Signal+Execute — Rp750K")],
    [("menu:platforms", "🔙 Back")],
]

SUBSCRIBE_MENU: list[list[tuple[str, str]]] = [
    [("sub:pro", "⭐ PRO — Rp50K/bulan")],
    [("sub:elite", "👑 ELITE — Rp150K/bulan")],
    [("sub:lifetime", "💎 LIFETIME — Rp500K"), ("cancel_input", "❌ Batal")],
    [("menu:account", "🔙 Back")],
]

DONATE_MENU: list[list[tuple[str, str]]] = [
    [("pay:donate:15000", "☕ Kopi — Rp15.000")],
    [("pay:donate:50000", "🚀 Bensin — Rp50.000")],
    [("pay:donate:100000", "⚡ Server — Rp100.000")],
    [("cmd:donate", "💚 Nominal Bebas")],
    [("menu:account", "🔙 Back")],
]

STOCKITY_MENU: list[list[tuple[str, str] | tuple[str, str, str]]] = [
    [("__url__", "🚀 Daftar Stockity", "https://stockity-mr.com/auth?invite_code=7b8730c84b6450e3e0b02fd3fd864f69#SignUp")],
    [("cmd:status", "📊 Status Akun")],
    [("menu:signals", "🔙 Back")],
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

PORTFOLIO_MENU: list[list[tuple[str, str]]] = [
    [("portfolio:refresh", "🔄 Refresh"), ("portfolio:trade_best", "🔥 Trade Best")],
    [("menu:link", "🔗 Link Platform"), ("menu:autotrade", "🤖 Auto-Execute")],
    [("menu:main", "🔙 Back")],
]

LINK_MENU: list[list[tuple[str, str]]] = [
    [("link:stockity", "📈 Stockity")],
    [("link:deriv", "💹 Deriv")],
    [("link:ccxt", "🔄 CCXT Exchange")],
    [("link:mt5", "💻 MetaTrader 5")],
    [("menu:portfolio", "🔙 Back")],
]

AUTOTRADE_MENU: list[list[tuple[str, str]]] = [
    [("autotrade:on", "🟢 ON"), ("autotrade:off", "⚪ OFF")],
    [("menu:portfolio", "🔙 Back")],
]


def build_keyboard(
    menu: list[list[tuple[str, str] | tuple[str, str, str]]],
) -> list[list[dict[str, str]]]:
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
    _menus: dict[str, list[list[tuple[str, str] | tuple[str, str, str]]]] = {
        "main": MAIN_MENU,
        "admin": ADMIN_MENU,
        "signals": SIGNAL_MENU,
        "analysis": ANALYSIS_MENU,
        "market": MARKET_MENU,
        "history": HISTORY_MENU,
        "account": ACCOUNT_MENU,
        "platforms": PLATFORMS_MENU,
        "subscriptions": SUBSCRIPTIONS_MENU,
        "subscribe": SUBSCRIBE_MENU,
        "donate": DONATE_MENU,
        "stockity": STOCKITY_MENU,
        "admin_panel": ADMIN_PANEL_MENU,
        "help": HELP_MENU,
        "trade": TRADE_MENU,
        "portfolio": PORTFOLIO_MENU,
        "link": LINK_MENU,
        "autotrade": AUTOTRADE_MENU,
    }
    menu = _menus.get(menu_name, MAIN_MENU)
    return {"inline_keyboard": build_keyboard(menu)}


def get_menu_text(menu_name: str, user_info: dict[str, Any] | None = None) -> str:
    texts: dict[str, str] = {
        "main": "🔥 <b>VILONA AI — COMMAND CENTER</b>\n━━━━━━━━━━━━━━━━━━━━━\nPilih menu di bawah untuk memulai:",
        "signals": "🧠 <b>SIGNAL SYSTEM</b>\n━━━━━━━━━━━━━━━━━━━━━\nAI MTF Top-Down Analysis dengan 9 engines.",
        "market": "📊 <b>MARKET DATA</b>\n━━━━━━━━━━━━━━━━━━━━━\nHarga real-time, killzone, dan data pasar.",
        "history": "📈 <b>TRADE HISTORY</b>\n━━━━━━━━━━━━━━━━━━━━━\nWin rate, daily recap, dan mapping.",
        "account": "👤 <b>ACCOUNT</b>\n━━━━━━━━━━━━━━━━━━━━━\nStatus, subscribe, dan pengaturan.",
        "donate": "💚 <b>DONASI PUBLIK — VILONA AI</b>\n━━━━━━━━━━━━━━━━━━━━━\nDukungan Anda membantu server AI tetap aktif & cepat.",
        "subscribe": "⭐ <b>LANGGANAN PREMIUM</b>\n━━━━━━━━━━━━━━━━━━━━━\nPilih paket langganan Anda:",
        "analysis": "🔬 <b>TECHNICAL ANALYSIS TOOLS</b>\n━━━━━━━━━━━━━━━━━━━━━\nPilih tools analisis teknis di bawah ini:",
        "stockity": "💰 <b>STOCKITY INSIDER</b>\n━━━━━━━━━━━━━━━━━━━━━\nSistem bandar (insider) untuk akurasi sinyal maksimal.",
        "admin_panel": "⚙️ <b>ADMIN PANEL</b>\n━━━━━━━━━━━━━━━━━━━━━\nManajemen bot dan pengguna.",
        "help": "❓ <b>HELP</b>\n━━━━━━━━━━━━━━━━━━━━━\nGunakan menu atau ketik command langsung.",
        "platforms": "🔗 <b>PLATFORM MANAGEMENT</b>\n━━━━━━━━━━━━━━━━━━━━━\nKelola akun broker Anda di sini.",
        "subscriptions": "💳 <b>SUBSCRIPTION PLANS</b>\n━━━━━━━━━━━━━━━━━━━━━\nPilih paket langganan Anda:",
        "portfolio": "📊 <b>PORTFOLIO</b>\n━━━━━━━━━━━━━━━━━━━━━\nAset terbaik, P&L, dan status akun Anda.",
        "link": "🔗 <b>LINK PLATFORM</b>\n━━━━━━━━━━━━━━━━━━━━━\nPilih platform untuk ditautkan:",
        "autotrade": "🤖 <b>AUTO-EXECUTE</b>\n━━━━━━━━━━━━━━━━━━━━━\nAktifkan/nonaktifkan eksekusi trading otomatis.",
    }
    return texts.get(menu_name, texts["main"])


def is_admin(chat_id: str, admin_ids: list[str] | None = None) -> bool:
    if not admin_ids:
        return False
    return chat_id in admin_ids or str(chat_id) in admin_ids


__all__ = [
    "ADMIN_MENU", "MAIN_MENU", "SIGNAL_MENU", "MARKET_MENU",
    "HISTORY_MENU", "ACCOUNT_MENU", "SUBSCRIBE_MENU", "STOCKITY_MENU",
    "ADMIN_PANEL_MENU", "HELP_MENU", "TRADE_MENU",
    "PORTFOLIO_MENU", "LINK_MENU", "AUTOTRADE_MENU",
    "get_inline_keyboard", "get_menu_text", "is_admin", "build_keyboard",
]