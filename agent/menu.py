"""
Menu system — 9 categorized inline keyboards.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from telethon import Button

STOCKITY_LINK = "https://stockity-mr.com/auth?invite_code=7b8730c84b6450e3e0b02fd3fd864f69#SignUp"

MAIN_MENU = [
    [Button.inline("🧠 SIGNAL SYSTEM", "menu:signals"), Button.inline("📊 MARKET DATA", "menu:market")],
    [Button.inline("📈 TRADE HISTORY", "menu:history"), Button.inline("👤 ACCOUNT", "menu:account")],
    [Button.inline("❓ HELP", "menu:help")],
]

ADMIN_MENU = [
    [Button.inline("🧠 SIGNAL SYSTEM", "menu:signals"), Button.inline("📊 MARKET DATA", "menu:market")],
    [Button.inline("📈 TRADE HISTORY", "menu:history"), Button.inline("👤 ACCOUNT", "menu:account")],
    [Button.inline("⚙️ ADMIN", "menu:admin_panel"), Button.inline("❓ HELP", "menu:help")],
]

SIGNAL_MENU = [
    [Button.inline("🎯 Signal MTF+9 Engines", "cmd:signal")],
    [Button.inline("🏗 Market Structure", "cmd:structure")],
    [Button.inline("💰 Stockity Insider", "menu:stockity")],
    [Button.inline("🔙 Back", "menu:main")],
]

MARKET_MENU = [
    [Button.inline("📊 Market Data", "cmd:data"), Button.inline("🥇 Gold", "cmd:price gold")],
    [Button.inline("₿ BTC", "cmd:price btc"), Button.inline("⟠ ETH", "cmd:price eth")],
    [Button.inline("🎯 Killzone", "cmd:killzone"), Button.inline("🧲 Liquidity Zones", "cmd:zones")],
    [Button.inline("🏛 S&R Levels", "cmd:levels"), Button.inline("📰 Market News", "cmd:news")],
    [Button.inline("🕐 Session Levels", "cmd:session")],
    [Button.inline("🔙 Back", "menu:main")],
]

HISTORY_MENU = [
    [Button.inline("📈 Win Rate", "cmd:winrate"), Button.inline("📋 Daily Recap", "cmd:recap")],
    [Button.inline("📜 Trade History", "cmd:history"), Button.inline("🗺️ Mapping", "cmd:mapping")],
    [Button.inline("🔙 Back", "menu:main")],
]

ACCOUNT_MENU = [
    [Button.inline("📊 Status", "cmd:status"), Button.inline("🔑 My Key", "cmd:mykey")],
    [Button.inline("💚 Donate", "menu:donate")],
    [Button.inline("🔙 Back", "menu:main")],
]

DONATE_MENU = [
    [Button.inline("☕️ Kopi (Rp 15K)", "sub:pro")],
    [Button.inline("🚀 Bensin Full (Rp 50K)", "sub:elite")],
    [Button.inline("💰 Nominal Bebas", "sub:lifetime")],
    [Button.inline("🔙 Back", "menu:account")],
]

STOCKITY_MENU = [
    [Button.url("🚀 Daftar Stockity", STOCKITY_LINK)],
    [Button.inline("📊 Status Akun", "cmd:status")],
    [Button.inline("🔙 Back", "menu:signals")],
]

ADMIN_PANEL = [
    [Button.inline("📊 Dashboard", "cmd:dashboard"), Button.inline("🌉 Bridge", "cmd:bridge")],
    [Button.inline("🔑 Gen Key", "cmd:genkey")],
    [Button.inline("🔙 Back", "menu:main")],
]

HELP_MENU = [
    [Button.inline("📖 Start", "cmd:start"), Button.inline("📚 Help", "cmd:help")],
    [Button.inline("📋 ID Saya", "cmd:myid")],
    [Button.inline("🔙 Back", "menu:main")],
]

MENUS = {
    "main": MAIN_MENU,
    "admin": ADMIN_MENU,
    "signals": SIGNAL_MENU,
    "market": MARKET_MENU,
    "history": HISTORY_MENU,
    "account": ACCOUNT_MENU,
    "donate": DONATE_MENU,
    "stockity": STOCKITY_MENU,
    "admin_panel": ADMIN_PANEL,
    "help": HELP_MENU,
}

MENU_TEXTS = {
    "main": "🔥 <b>1AI TRADING AGENT — AI POWERED</b>\n━━━━━━━━━━━━━━━━━━━━━\nPilih menu di bawah:",
    "signals": "🧠 <b>SIGNAL SYSTEM</b>\n━━━━━━━━━━━━━━━━━━━━━\nAI MTF Top-Down Analysis dengan 9 engines.",
    "market": "📊 <b>MARKET DATA</b>\n━━━━━━━━━━━━━━━━━━━━━\nHarga real-time, killzone, level S&R.",
    "history": "📈 <b>TRADE HISTORY</b>\n━━━━━━━━━━━━━━━━━━━━━\nWin rate, daily recap, mapping.",
    "account": "👤 <b>ACCOUNT</b>\n━━━━━━━━━━━━━━━━━━━━━\nStatus, donate, dan pengaturan.",
    "donate": "💚 <b>SUBSCRIBE — 1AI Agent</b>\n━━━━━━━━━━━━━━━━━━━━━\nDukung server AI tetap hidup!\nPilih nominal di bawah:",
    "stockity": "💰 <b>STOCKITY INSIDER</b>\n━━━━━━━━━━━━━━━━━━━━━\nKami menggunakan sistem bandar (insider).",
    "admin_panel": "⚙️ <b>ADMIN PANEL</b>\n━━━━━━━━━━━━━━━━━━━━━\nManajemen bot.",
    "help": "⚙️ <b>1AI Agent — COMMAND CENTER</b>\n━━━━━━━━━━━━━━━━━━━━━\nGunakan menu atau command.",
}


def get_menu_kb(menu_name: str):
    return MENUS.get(menu_name, MAIN_MENU)


def get_menu_text(menu_name: str) -> str:
    return MENU_TEXTS.get(menu_name, MENU_TEXTS["main"])


def build_kb(menu_name: str):
    return get_menu_kb(menu_name)
