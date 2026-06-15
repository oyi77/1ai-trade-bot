"""Unified menu system — categorized inline keyboard menus with role-based views."""

from __future__ import annotations

from typing import Any

# ── Menu Structure ──

MAIN_MENU: list[list[tuple[str, str]]] = [
    [("menu:signals", "🧠 SIGNAL SYSTEM"), ("menu:market", "📊 MARKET DATA")],
    [("menu:history", "📈 TRADE HISTORY"), ("menu:account", "👤 ACCOUNT")],
    [("menu:whitelabel", "🏢 WHITELABEL"), ("menu:panduan", "📘 PANDUAN")],
    [("menu:help", "❓ HELP")],
]

HOME_MENU = MAIN_MENU

SIGNAL_MENU: list[list[tuple[str, str]]] = [
    [("cmd:signal", "🎯 Live Signal")],
    [("menu:market_filter", "🎯 Pilih Market"), ("cmd:whale", "🐋 Whale Scan")],
    [("menu:analysis", "🔬 Technical Analysis")],
    [("menu:main", "🔙 Back"), ("menu:home", "🏠 Home")],
]

MARKET_FILTER_MENU: list[list[tuple[str, str]]] = [
    [("cmd:price gold", "🥇 XAUUSD — FX"), ("cmd:price btc", "₿ BTC — Crypto")],
    [("cmd:price eth", "⟠ ETH — Crypto")],
    [("cmd:price", "📊 IDX — Stocks")],
    [("cmd:price", "🎲 Binary Options")],
    [("menu:signals", "🔙 Back"), ("menu:home", "🏠 Home")],
]

ANALYSIS_MENU: list[list[tuple[str, str]]] = [
    [("cmd:mtf", "🧬 Matrix 5TF (MTF)"), ("cmd:engines", "🔧 Engine Consensus")],
    [("cmd:structure", "🏗 Market Structure"), ("cmd:pulse", "🔄 Market Pulse")],
    [("cmd:signal", "🎯 Signal per Market")],
    [("menu:signals", "🔙 Back"), ("menu:home", "🏠 Home")],
]

MARKET_MENU: list[list[tuple[str, str]]] = [
    [("cmd:data", "📊 Market Data"), ("cmd:price gold", "🥇 Gold")],
    [("cmd:price btc", "₿ BTC"), ("cmd:price eth", "⟠ ETH")],
    [("cmd:killzone", "🎯 Killzone"), ("cmd:zones", "🧲 Liquidity Zones")],
    [("cmd:levels", "🏛 S&R Levels"), ("cmd:news", "📰 Market News")],
    [("cmd:session", "🕐 Session Levels")],
    [("menu:main", "🔙 Back"), ("menu:home", "🏠 Home")],
]

HISTORY_MENU: list[list[tuple[str, str]]] = [
    [("cmd:winrate", "📈 Win Rate"), ("cmd:recap", "📋 Daily Recap")],
    [("cmd:history", "📜 Trade History"), ("cmd:stats", "📊 Performance Stats")],
    [("menu:main", "🔙 Back"), ("menu:home", "🏠 Home")],
]

ACCOUNT_MENU: list[list[tuple[str, str]]] = [
    [("cmd:status", "📊 My Status"), ("cmd:subscribe", "⭐ Subscribe")],
    [("cmd:mykey", "🔑 My Key"), ("cmd:trailing", "🏃 Trailing Status")],
    [("cmd:myid", "🆔 My ID"), ("menu:donate", "💚 Donate")],
    [("menu:main", "🔙 Back"), ("menu:home", "🏠 Home")],
]

SUBSCRIBE_MENU: list[list[tuple[str, str]]] = [
    [("sub:basic", "📡 BASIC — Rp99K/bulan")],
    [("sub:pro", "⭐ PRO — Rp199K/bulan")],
    [("sub:enterprise", "🏢 ENTERPRISE — Rp499K/bulan")],
    [("cancel_input", "❌ Batal")],
    [("menu:account", "🔙 Back"), ("menu:home", "🏠 Home")],
]

DONATE_MENU: list[list[tuple[str, str]]] = [
    [("pay:donate:15000", "☕ Kopi — Rp15.000")],
    [("pay:donate:50000", "🚀 Bensin — Rp50.000")],
    [("pay:donate:100000", "⚡ Server — Rp100.000")],
    [("cmd:donate", "💚 Nominal Bebas")],
    [("menu:account", "🔙 Back"), ("menu:home", "🏠 Home")],
]

# ── Whitelabel / MLM ──
WHITELABEL_MENU: list[list[tuple[str, str]]] = [
    [("__url__", "🏪 Daftar Reseller", "https://jasahub.id/p/vilona-omni")],
    [("cmd:wl_status", "📊 Status Whitelabel"), ("cmd:wl_earnings", "💰 Komisi")],
    [("cmd:wl_referral", "🔗 Link Referral"), ("cmd:wl_members", "👥 Anggota")],
    [("menu:main", "🔙 Back"), ("menu:home", "🏠 Home")],
]

STOCKITY_MENU: list[list[tuple[str, str] | tuple[str, str, str]]] = [
    [("__url__", "🚀 Daftar Stockity", "https://stockity-mr.com/auth?invite_code=7b8730c84b6450e3e0b02fd3fd864f69#SignUp")],
    [("cmd:status", "📊 Status Akun")],
    [("menu:signals", "🔙 Back"), ("menu:home", "🏠 Home")],
]

ADMIN_PANEL_MENU: list[list[tuple[str, str]]] = [
    [("cmd:dashboard", "📊 Dashboard"), ("cmd:bridge_status", "🌉 Bridge")],
    [("cmd:genkey", "🔑 Gen Key"), ("cmd:activate", "⭐ Activate")],
    [("cmd:wl_config", "🏢 Whitelabel Config"), ("cmd:wl_commission", "💰 Commission Log")],
    [("cmd:restart_bot", "🔄 Restart Bot")],
    [("menu:main", "🔙 Back"), ("menu:home", "🏠 Home")],
]

HELP_MENU: list[list[tuple[str, str]]] = [
    [("cmd:start", "📖 Start"), ("cmd:help", "📚 All Commands")],
    [("cmd:panduan", "📘 Panduan Lengkap"), ("cmd:ea", "📥 Download EA")],
    [("cmd:symbols", "📋 Symbols"), ("cmd:bridge_full_status", "🛡️ Bridge Status")],
    [("menu:main", "🔙 Back"), ("menu:home", "🏠 Home")],
]

PANDUAN_MENU: list[list[tuple[str, str]]] = [
    [("cmd:cara_analisa", "🔍 Cara Analisa"), ("cmd:cara_baca", "📖 Cara Baca Sinyal")],
    [("cmd:cara_pasang", "🚀 Cara Pasang Posisi"), ("cmd:cara_ea", "🤖 Cara Pasang EA")],
    [("cmd:cara_trailing", "🏃 Cara Trailing Stop"), ("cmd:alasan_sinyal", "🧠 Kenapa Sinyal Keluar?")],
    [("menu:help", "🔙 Back"), ("menu:home", "🏠 Home")],
]

TRADE_MENU: list[list[tuple[str, str]]] = [
    [("trade:yes", "✅ Trade"), ("trade:no", "⏭ Skip")],
]

PORTFOLIO_MENU: list[list[tuple[str, str]]] = [
    [("portfolio:refresh", "🔄 Refresh"), ("portfolio:trade_best", "🔥 Trade Best")],
    [("menu:main", "🔙 Back"), ("menu:home", "🏠 Home")],
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
        "home": HOME_MENU,
        "admin": ADMIN_PANEL_MENU,
        "signals": SIGNAL_MENU,
        "market_filter": MARKET_FILTER_MENU,
        "analysis": ANALYSIS_MENU,
        "market": MARKET_MENU,
        "history": HISTORY_MENU,
        "account": ACCOUNT_MENU,
        "subscribe": SUBSCRIBE_MENU,
        "donate": DONATE_MENU,
        "whitelabel": WHITELABEL_MENU,
        "stockity": STOCKITY_MENU,
        "admin_panel": ADMIN_PANEL_MENU,
        "help": HELP_MENU,
        "trade": TRADE_MENU,
        "portfolio": PORTFOLIO_MENU,
        "panduan": PANDUAN_MENU,
    }
    menu = _menus.get(menu_name, MAIN_MENU)
    return {"inline_keyboard": build_keyboard(menu)}


def get_menu_text(menu_name: str, user_info: dict[str, Any] | None = None) -> str:
    texts: dict[str, str] = {
        "main": (
            "🔥 <b>VILONA OMNI — AI COMMAND CENTER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Sistem AI trading multi-market 24/7.\n"
            "Pilih menu di bawah untuk memulai:"
        ),
        "signals": (
            "🧠 <b>SIGNAL SYSTEM</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "AI MTF Top-Down Analysis dengan 11 engines + Sequoia-X Quant.\n"
            "Dapatkan sinyal terbaik dari setiap market."
        ),
        "market_filter": (
            "🎯 <b>PILIH MARKET</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Pilih market untuk mendapatkan sinyal spesifik:\n"
            "🥇 FX (XAUUSD, GBPUSD, EURUSD)\n"
            "₿ Crypto (BTC, ETH, SOL)\n"
            "📊 Stocks (IDX)\n"
            "🎲 Binary Options (Deriv)"
        ),
        "analysis": (
            "🔬 <b>TECHNICAL ANALYSIS TOOLS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Pilih tools analisis teknis di bawah ini:"
        ),
        "market": (
            "📊 <b>MARKET DATA</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Harga real-time, killzone, dan data pasar."
        ),
        "history": (
            "📈 <b>TRADE HISTORY</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Win rate, daily recap, dan performance stats."
        ),
        "account": (
            "👤 <b>ACCOUNT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Status langganan, key, dan pengaturan akun."
        ),
        "donate": (
            "💚 <b>DONASI PUBLIK — VILONA OMNI</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Dukungan Anda membantu server AI tetap aktif & cepat."
        ),
        "subscribe": (
            "⭐ <b>VILONA OMNI — PAKET LANGGANAN</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Pilih paket yang sesuai dengan kebutuhan trading Anda:\n\n"
            "📡 <b>BASIC — Rp99K/bln</b>\n"
            "5 sinyal/hari, 2 market, daily report\n\n"
            "⭐ <b>PRO — Rp199K/bln</b>\n"
            "Unlimited sinyal, semua market, EA auto-execute\n\n"
            "🏢 <b>ENTERPRISE — Rp499K/bln</b>\n"
            "Semua fitur PRO + whitelabel reseller + MLM + API"
        ),
        "whitelabel": (
            "🏢 <b>VILONA OMNI — WHITELABEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Jadi reseller dan dapatkan komisi 20% dari setiap referral!\n"
            "Bisa whitelabel keseluruhan fitur atau per market saja.\n\n"
            "💎 ENTERPRISE: Rp499K/bln — all-in + reseller rights"
        ),
        "stockity": (
            "💰 <b>STOCKITY INSIDER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Sistem bandar (insider) untuk akurasi sinyal maksimal."
        ),
        "admin_panel": (
            "⚙️ <b>ADMIN PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Manajemen bot, pengguna, dan whitelabel."
        ),
        "help": (
            "❓ <b>HELP</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Gunakan menu atau ketik command langsung."
        ),
        "panduan": (
            "📘 <b>PANDUAN LENGKAP</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Pilih topik panduan yang ingin kamu pelajari:"
        ),
        "portfolio": (
            "📊 <b>PORTFOLIO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Aset terbaik, P&L, dan status akun Anda."
        ),
    }
    return texts.get(menu_name, texts["main"])


def is_admin(chat_id: str, admin_ids: list[str] | None = None) -> bool:
    if not admin_ids:
        return False
    return chat_id in admin_ids or str(chat_id) in admin_ids


__all__ = [
    "MAIN_MENU", "SIGNAL_MENU", "MARKET_FILTER_MENU", "ANALYSIS_MENU",
    "MARKET_MENU", "HISTORY_MENU", "ACCOUNT_MENU", "SUBSCRIBE_MENU",
    "DONATE_MENU", "WHITELABEL_MENU", "STOCKITY_MENU",
    "ADMIN_PANEL_MENU", "HELP_MENU", "TRADE_MENU",
    "PORTFOLIO_MENU", "PANDUAN_MENU",
    "get_inline_keyboard", "get_menu_text", "is_admin", "build_keyboard",
]
