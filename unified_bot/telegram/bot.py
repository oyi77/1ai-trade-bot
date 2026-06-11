"""
UNIFIED TRADING BOT — Telegram Interface
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All features: analyze, journal, trades, share, invite, referral,
broadcast, watchlist, alert, export, sinyal, quiz, support ticket.
Dual language ID/EN, inline keyboards, quota, tier, donation.
"""
from __future__ import annotations
import csv, io, json, logging, os, random, re, string
from datetime import datetime, UTC, timedelta
from pathlib import Path
from typing import Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from .ai_analysis import analyze_market
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

LOG = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8343388239:AAFgeAkc9bvjywyCsHqRIa_RiJ6q-rp6uv0")
ADMIN_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
FREE_QUOTA = 3
REFERRAL_BONUS = 2
SEP = "━━━━━━━━━━━━━━━━━━━━━"

DATA_DIR = Path(os.getenv("DATA_DIR", "data/telegram"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
(QUOTA_DIR := DATA_DIR / "quota").mkdir(parents=True, exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
TRADES_FILE = DATA_DIR / "trades.json"
REFS_FILE = DATA_DIR / "referrals.json"
WATCH_FILE = DATA_DIR / "watchlist.json"
ALERTS_FILE = DATA_DIR / "alerts.json"
TICKETS_FILE = DATA_DIR / "tickets.json"
QUIZ_FILE = DATA_DIR / "quiz.json"

# ── I18N ─────────────────────────────────────────────────────────────────
L = {
    "id": {
        "welcome_new": "🔥 <b>REVOLUSI TRADING DIMULAI</b>\n{sep}Halo <b>{name}</b>! Selamat datang di markas <b>UNIFIED TRADING BOT.</b>\nAI Agents 24/7 siap membantu.\n{sep}\n{tier}\n{quota_line}",
        "welcome_back": "🔥 <b>REVOLUSI TRADING DIMULAI</b>\n{sep}Selamat datang kembali, <b>{name}</b>!\nAI masih menyala 24/7.\n{sep}\n{tier}\n{quota_line}",
        "help": "⚙️ <b>COMMAND CENTER</b>\n{sep}\n\n👑 <b>PILAR UTAMA</b>\n/start — Reboot\n/analyze — AI scan\n/status — Cek kuota\n/donate — Siram bensin\n\n📊 <b>TRADING</b>\n/journal — Jurnal\n/trades — Riwayat\n/share — Bagikan\n/watchlist — Asset favorit\n/alert — Notifikasi harga\n/sinyal — Signal real-time\n\n👥 <b>KOMUNITAS</b>\n/invite — Undang teman\n/referral — Referral\n/quiz — Edukasi trading\n/ticket — Bantuan\n/export — Export CSV\n{sep}\n📞 Admin: @codergaboets",
        "status": "📊 <b>STATUS AKUN</b>\n{sep}\n👤 <b>Pengguna:</b> {name}\n🏷️ <b>Tier:</b> {tier}\n⚡️ <b>Kuota:</b> {q}\n💰 <b>Donasi:</b> Rp{donated:,}\n📅 <b>Bergabung:</b> {joined}\n🎯 <b>Win Rate:</b> {wr}\n📓 <b>Jurnal:</b> {jt} entry\n👥 <b>Referral:</b> {ref}\n{sep}\n💡 /donate untuk VIP unlimited!",
        "trades": "📊 <b>RIWAYAT TRADING</b>\n{sep}\n{e}\n{sep}\n📈 <b>Win Rate:</b> {wr}\n💰 <b>Total PnL:</b> {pnl}",
        "trades_empty": "📊 <b>RIWAYAT TRADING</b>\n{sep}\nBelum ada. /addtrade untuk mulai!",
        "donate": "💚 <b>SIRAM BAHAN BAKAR MESIN AI 🚀</b>\n{sep}\nPilih dukunganmu hari ini:",
        "analyze_menu": "📊 <b>PILIH ASSET</b>\n{sep}\nPilih asset untuk analisa AI.\n{q}",
        "analyze_result": "🔍 <b>ANALISA {s} — {dir}</b>\n{sep}🎯 <b>Sinyal:</b> {dir}\n📊 <b>Keyakinan:</b> {conf}%\n💰 <b>Entry:</b> {entry}\n🛑 <b>Stop Loss:</b> {sl}\n✅ <b>Take Profit:</b> {tp}\n📝 <b>Analisa:</b> {analysis}\n{sep}\nLevel: {key_levels}\n⚠️ Resiko: {risk}\n{q}",
        "quota_habis": "❌ <b>KUOTA HABIS!</b>\n{sep}\n{quota}x gratis hari ini habis.\n💡 /donate VIP unlimited\n👥 /invite extra +{rb} kuota\n⏰ Reset besok 00:00",
        "journal_empty": "📓 <b>JURNAL</b>\n{sep}\nBelum ada. /journal XAUUSD 50 catatan",
        "journal": "📓 <b>JURNAL TRADING</b>\n{sep}\n{e}\n{sep}📈 WR: {wr} | PnL: {pnl}\n🏆 Best: {best}\n📉 Worst: {worst}",
        "journal_add": "📝 Format: <code>/journal SYMBOL PnL catatan</code>\nContoh: /journal XAUUSD 50 SMC confluent",
        "invite": "📨 <b>INVITE TEMAN</b>\n{sep}\n🔗 <code>t.me/agent_1ai2_bot?start=ref_{code}</code>\n📊 {total} orang diundang\n🎁 +{bonus} kuota/referral",
        "referral": "👥 <b>REFERRAL</b>\n{sep}🔗 <code>t.me/agent_1ai2_bot?start=ref_{code}</code>\n📊 {total} referral\n🎁 +{bonus} kuota/hari",
        "share_trades": "📤 <b>JURNAL</b>\n{sep}\n{e}\n{sep}<b>🔥 TRADING BOT AI</b>\nWR: {wr} | PnL: {pnl}\n🤖 t.me/agent_1ai2_bot",
        "watchlist": "📋 <b>WATCHLIST</b>\n{sep}\n{items}\n{sep}💡 /watchlist add XAUUSD — tambah asset",
        "alert_menu": "🔔 <b>PRICE ALERT</b>\n{sep}\n{list}\n{sep}/alert XAUUSD 3000 — notifikasi di atas 3000",
        "sinyal_menu": "📡 <b>SINYAL REAL-TIME</b>\n{sep}\n{sinyal}\n{sep}🤖 AI engine: {status}",
        "quiz_start": "🧠 <b>QUIZ TRADING</b>\n{sep}\n{soal}\n\n{opsi}",
        "quiz_selesai": "🧠 <b>QUIZ SELESAI!</b>\n{sep}\n✅ Benar: {benar}/{total}\n📊 Skor: {skor}%\n🏆 Grade: {grade}",
        "ticket_create": "🎫 <b>BANTUAN</b>\n{sep}\nFormat: <code>/ticket Pesan kamu</code>\nContoh: /ticket Saya tidak bisa analisa",
        "ticket_status": "🎫 <b>TIKET BANTUAN</b>\n{sep}\n{tickets}\n{sep}📞 Admin: @codergaboets",
        "export_done": "📤 <b>EXPORT CSV</b>\n{sep}\n{data}",
        "broadcast_help": "📢 <b>BROADCAST</b>\n{sep}\nAdmin only.\nFormat: <code>/broadcast Pesan</code>",
        "broadcast_done": "📢 <b>BROADCAST TERKIRIM</b>\n{sep} ke {count} pengguna",
        "lang_en": "🌐 Bahasa → <b>English</b>",
        "lang_id": "🌐 Bahasa → <b>Bahasa Indonesia</b>",
    },
    "en": {
        "welcome_new": "🔥 <b>TRADING REVOLUTION STARTED</b>\n{sep}Hi <b>{name}</b>! Welcome to <b>UNIFIED TRADING BOT.</b>\nAI Agents 24/7 ready to help.\n{sep}\n{tier}\n{quota_line}",
        "welcome_back": "🔥 <b>TRADING REVOLUTION STARTED</b>\n{sep}Welcome back, <b>{name}</b>!\nAI engine still running 24/7.\n{sep}\n{tier}\n{quota_line}",
        "help": "⚙️ <b>COMMAND CENTER</b>\n{sep}\n\n👑 <b>MAIN</b>\n/start — Reboot\n/analyze — AI scan\n/status — Check quota\n/donate — Fuel AI\n\n📊 <b>TRADING</b>\n/journal — Journal\n/trades — History\n/share — Share\n/watchlist — Fav assets\n/alert — Price alert\n/sinyal — Real-time signal\n\n👥 <b>COMMUNITY</b>\n/invite — Invite\n/referral — Referral\n/quiz — Trading quiz\n/ticket — Support\n/export — Export CSV\n{sep}\n📞 Admin: @codergaboets",
        "status": "📊 <b>ACCOUNT STATUS</b>\n{sep}\n👤 <b>User:</b> {name}\n🏷️ <b>Tier:</b> {tier}\n⚡️ <b>Quota:</b> {q}\n💰 <b>Donation:</b> Rp{donated:,}\n📅 <b>Joined:</b> {joined}\n🎯 <b>Win Rate:</b> {wr}\n📓 <b>Journal:</b> {jt} entries\n👥 <b>Referrals:</b> {ref}\n{sep}\n💡 /donate for VIP unlimited!",
        "trades": "📊 <b>TRADE HISTORY</b>\n{sep}\n{e}\n{sep}📈 <b>Win Rate:</b> {wr}\n💰 <b>Total PnL:</b> {pnl}",
        "trades_empty": "📊 <b>TRADE HISTORY</b>\n{sep}\nNone yet. /addtrade to start!",
        "donate": "💚 <b>FUEL THE AI ENGINE 🚀</b>\n{sep}\nChoose your support level:",
        "analyze_menu": "📊 <b>SELECT ASSET</b>\n{sep}\nChoose asset for AI analysis.\n{q}",
        "analyze_result": "🔍 <b>{s} ANALYSIS — {dir}</b>\n{sep}🎯 <b>Signal:</b> {dir}\n📊 <b>Confidence:</b> {conf}%\n💰 <b>Entry:</b> {entry}\n🛑 <b>Stop Loss:</b> {sl}\n✅ <b>Take Profit:</b> {tp}\n📝 <b>Analysis:</b> {analysis}\n{sep}\nKey Levels: {key_levels}\n⚠️ Risk: {risk}\n{q}",
        "quota_habis": "❌ <b>QUOTA EXHAUSTED!</b>\n{sep}\n{quota}x free today used.\n💡 /donate VIP unlimited\n👥 /invite extra +{rb} quota\n⏰ Reset tomorrow 00:00",
        "journal_empty": "📓 <b>JOURNAL</b>\n{sep}\nEmpty. /journal XAUUSD 50 notes",
        "journal": "📓 <b>TRADING JOURNAL</b>\n{sep}\n{e}\n{sep}📈 WR: {wr} | PnL: {pnl}\n🏆 Best: {best}\n📉 Worst: {worst}",
        "journal_add": "📝 Format: <code>/journal SYMBOL PnL notes</code>\nEg: /journal XAUUSD 50 SMC confluence",
        "invite": "📨 <b>INVITE FRIENDS</b>\n{sep}\n🔗 <code>t.me/agent_1ai2_bot?start=ref_{code}</code>\n📊 {total} invited\n🎁 +{bonus} quota/referral",
        "referral": "👥 <b>REFERRAL</b>\n{sep}🔗 <code>t.me/agent_1ai2_bot?start=ref_{code}</code>\n📊 {total} referrals\n🎁 +{bonus} quota/day",
        "share_trades": "📤 <b>JOURNAL</b>\n{sep}\n{e}\n{sep}<b>🔥 TRADING BOT AI</b>\nWR: {wr} | PnL: {pnl}\n🤖 t.me/agent_1ai2_bot",
        "watchlist": "📋 <b>WATCHLIST</b>\n{sep}\n{items}\n{sep}💡 /watchlist add XAUUSD — add asset",
        "alert_menu": "🔔 <b>PRICE ALERT</b>\n{sep}\n{list}\n{sep}/alert XAUUSD 3000 — notify above 3000",
        "sinyal_menu": "📡 <b>REAL-TIME SIGNAL</b>\n{sep}\n{sinyal}\n{sep}🤖 AI engine: {status}",
        "quiz_start": "🧠 <b>TRADING QUIZ</b>\n{sep}\n{soal}\n\n{opsi}",
        "quiz_selesai": "🧠 <b>QUIZ DONE!</b>\n{sep}\n✅ Correct: {benar}/{total}\n📊 Score: {skor}%\n🏆 Grade: {grade}",
        "ticket_create": "🎫 <b>SUPPORT</b>\n{sep}\nFormat: <code>/ticket Your message</code>\nEg: /ticket My analysis is not working",
        "ticket_status": "🎫 <b>SUPPORT TICKETS</b>\n{sep}\n{tickets}\n{sep}📞 Admin: @codergaboets",
        "export_done": "📤 <b>CSV EXPORT</b>\n{sep}\n{data}",
        "broadcast_help": "📢 <b>BROADCAST</b>\n{sep}\nAdmin only.\nFormat: <code>/broadcast Message</code>",
        "broadcast_done": "📢 <b>BROADCAST SENT</b>\n{sep} to {count} users",
        "lang_en": "🌐 Language → <b>English</b>",
        "lang_id": "🌐 Language → <b>Bahasa Indonesia</b>",
    }
}

# ── Helpers ──────────────────────────────────────────────────────────────
def _lj(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}
def _sj(p: Path, d: dict):
    p.write_text(json.dumps(d, indent=2))
def _grc() -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

def _gu(chat_id: int) -> dict:
    u = _lj(USERS_FILE)
    k = str(chat_id)
    if k not in u:
        u[k] = {"tier":"free","donated":0,"joined":datetime.now(UTC).strftime("%Y-%m-%d"),
                "lang":"id","stockity_email":"","referrer":"","ref_code":_grc(),"quiz_best":0}
        _sj(USERS_FILE, u)
    return u[k]

def _su(chat_id: int, data: dict):
    u = _lj(USERS_FILE); u[str(chat_id)] = data; _sj(USERS_FILE, u)

def _gq(chat_id: int) -> dict:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    p = QUOTA_DIR / f"{chat_id}.json"
    bonus = len(_lj(REFS_FILE).get(str(chat_id), [])) * REFERRAL_BONUS
    mx = FREE_QUOTA + bonus
    if p.exists() and (d := json.loads(p.read_text())).get("date") == today:
        d["max"] = mx; return d
    return {"date":today,"used":0,"remaining":mx,"max":mx}

def _dq(chat_id: int) -> tuple[bool, int]:
    q = _gq(chat_id)
    if q["remaining"] <= 0: return False, 0
    q["used"] += 1; q["remaining"] = max(0, q["max"] - q["used"])
    (QUOTA_DIR / f"{chat_id}.json").write_text(json.dumps(q))
    return True, q["remaining"]

def _iv(chat_id: int) -> bool:
    return _gu(chat_id).get("tier") in ("vip","donatur","pro","elite")
def _tl(chat_id: int) -> str:
    return "👑 VIP" if _iv(chat_id) else "👤 Free"
def _ql(chat_id: int) -> str:
    if _iv(chat_id): return "♾️ UNLIMITED"
    q = _gq(chat_id); return f"⚡️ {q['remaining']}/{q['max']}"

def _wr(chat_id: int) -> str:
    t = _lj(TRADES_FILE).get(str(chat_id), [])
    if not t: return "—"
    w = sum(1 for x in t if x.get("result")=="win")
    return f"{w}/{len(t)} ({w*100//len(t)}%)"
def _pnl(chat_id: int) -> str:
    t = _lj(TRADES_FILE).get(str(chat_id), [])
    total = sum(x.get("pnl",0) for x in t)
    return f"+{total}" if total>=0 else str(total)

def _t(chat_id: int, key: str, **kw) -> str:
    lang = _gu(chat_id).get("lang","id")
    kw.setdefault("sep", SEP)
    return L.get(lang, L["id"]).get(key, key).format(**kw)

def _admin_only(func):
    async def wrapper(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_chat.id) != str(ADMIN_ID):
            await self._reply(update, "❌ <b>Admin only.</b>")
            return
        return await func(self, update, ctx)
    return wrapper

QUIZ_SOAL = [
    {"q":"Apa itu SMC dalam trading?","o":["Smart Money Concept","Simple Moving Cross","Super Market Chart"],"a":0},
    {"q":"Apa fungsi Stop Loss?","o":["Membatasi kerugian","Mencari profit","Menambah modal"],"a":0},
    {"q":"Apa itu FVG?","o":["Fair Value Gap","Fast Volume Growth","Final Value Graph"],"a":0},
    {"q":"Apa itu RSI?","o":["Relative Strength Index","Real Stock Index","Risk Score Indicator"],"a":0},
    {"q":"Apa itu Support & Resistance?","o":["Level harga psikologis","Indikator osilasi","Volume perdagangan"],"a":0},
    {"q":"Apa itu Candlestick?","o":["Grafik harga","Indikator teknikal","Signal generator"],"a":0},
    {"q":"Apa itu Liquidity Grab?","o":["Perangkap likuiditas","Grab order","Market order"],"a":0},
    {"q":"Apa timeframe terbaik untuk scalping?","o":["M1-M15","H1-H4","D1-W1"],"a":0},
    {"q":"Apa itu Spread?","o":["Selisih bid-ask","Komisi broker","Margin trading"],"a":0},
    {"q":"Apa itu Margin Call?","o":["Peringatan modal habis","Panggilan margin","Call option"],"a":0},
]

SINYAL_DUMMY = [
    ("XAUUSD", "BUY", 85, "SMC confluent + FVG + Order Block"),
    ("BTCUSD", "SELL", 72, "Resistance hit + Liquidity Grab"),
    ("EURUSD", "BUY", 68, "Demand zone + Divergence RSI"),
    ("GBPUSD", "SELL", 63, "Trend break + Bearish engulfing"),
    ("USOIL", "BUY", 78, "Support retest + MSS bullish"),
]

# ── Keyboards ────────────────────────────────────────────────────────────
def mk_main(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Analyze",callback_data="analyze"),InlineKeyboardButton("📓 Journal",callback_data="journal")],
        [InlineKeyboardButton("📋 Watchlist",callback_data="watchlist"),InlineKeyboardButton("📡 Sinyal",callback_data="sinyal")],
        [InlineKeyboardButton("🔔 Alert",callback_data="alert"),InlineKeyboardButton("📤 Share",callback_data="share")],
        [InlineKeyboardButton("👤 Status",callback_data="status"),InlineKeyboardButton("💚 Donate",callback_data="donate")],
        [InlineKeyboardButton("🧠 Quiz",callback_data="quiz"),InlineKeyboardButton("👥 Invite",callback_data="invite")],
        [InlineKeyboardButton("🎫 Ticket",callback_data="ticket"),InlineKeyboardButton("🌐 EN/ID",callback_data="lang")],
    ])
def bk(cb: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("« Kembali",callback_data=cb)]])
def donate_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("☕️ Kopi (Rp 15K)",callback_data="donate:coffee")],
        [InlineKeyboardButton("🍱 Makan Siang (Rp 25K)",callback_data="donate:lunch")],
        [InlineKeyboardButton("🚀 Bensin Full (Rp 50K)",callback_data="donate:fuel")],
        [InlineKeyboardButton("💰 Bebas",callback_data="donate:custom")],
        [InlineKeyboardButton("🤝 Chief Architect",url="https://t.me/codergaboets")],
        [InlineKeyboardButton("« Kembali",callback_data="menu_main")],
    ])
def analyze_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🪙 XAUUSD",callback_data="analyze:xauusd"),InlineKeyboardButton("₿ BTC",callback_data="analyze:btc")],
        [InlineKeyboardButton("💶 EURUSD",callback_data="analyze:eurusd"),InlineKeyboardButton("💷 GBPUSD",callback_data="analyze:gbpusd")],
        [InlineKeyboardButton("🛢️ USOIL",callback_data="analyze:usoil"),InlineKeyboardButton("👑 VIP",callback_data="analyze:vip")],
        [InlineKeyboardButton("« Kembali",callback_data="menu_main")],
    ])
def trades_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Win",callback_data="trade:win"),InlineKeyboardButton("➖ Loss",callback_data="trade:loss")],
        [InlineKeyboardButton("🗑 Clear",callback_data="trade:clear"),InlineKeyboardButton("📤 Export",callback_data="export")],
        [InlineKeyboardButton("« Menu",callback_data="menu_main")],
    ])
def journal_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Entry",callback_data="journal:add"),InlineKeyboardButton("📤 Share",callback_data="share:journal")],
        [InlineKeyboardButton("🗑 Clear",callback_data="trade:clear"),InlineKeyboardButton("« Menu",callback_data="menu_main")],
    ])
def share_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📓 Journal",callback_data="share:journal"),InlineKeyboardButton("🔗 Referral",callback_data="referral")],
        [InlineKeyboardButton("« Menu",callback_data="menu_main")],
    ])
def invite_kb(code: str) -> InlineKeyboardMarkup:
    msg = f"Gabung trading bot AI! t.me/agent_1ai2_bot?start=ref_{code}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Kirim",switch_inline_query=msg[:80])],
        [InlineKeyboardButton("🔗 Copy Link",callback_data=f"copy:{code}")],
        [InlineKeyboardButton("🏆 Leaderboard",callback_data="lb")],
        [InlineKeyboardButton("« Menu",callback_data="menu_main")],
    ])

# ── Bot ──────────────────────────────────────────────────────────────────
class UnifiedTelegramBot:
    def __init__(self, token: str = BOT_TOKEN):
        self.token = token
        self.app = Application.builder().token(token).build()
        self._rh(); LOG.info("UnifiedTelegramBot initialized (all features)")

    def _rh(self):
        cmds = ["start","help","status","analyze","donate","trades","journal","link","referral",
                "calendar","addtrade","share","invite","watchlist","alert","sinyal","quiz","ticket","export","broadcast","profile"]
        self.app.add_handler(CommandHandler("signal", self.cmd_sinyal))
        for c in cmds:
            self.app.add_handler(CommandHandler(c, getattr(self, f"cmd_{c}")))
        self.app.add_handler(CallbackQueryHandler(self.hc))

    async def _reply(self, u: Update, t: str, kb: InlineKeyboardMarkup|None = None):
        try:
            if u.callback_query:
                await u.callback_query.message.edit_text(t, parse_mode="HTML", reply_markup=kb)
                await u.callback_query.answer()
            elif u.message:
                await u.message.reply_text(t, parse_mode="HTML", reply_markup=kb)
            else:
                await u.get_bot().send_message(u.effective_chat.id, t, parse_mode="HTML", reply_markup=kb)
        except: pass

    async def _ensure_user(self, cid: int, name: str):
        _gu(cid); u = _gu(cid)
        if not u.get("ref_code"): u["ref_code"]=_grc(); _su(cid,u)
        return u

    # ── COMMANDS ────────────────────────────────────────────────────
    async def cmd_start(self, u: Update, ctx: ContextTypes.DEFAULT_TYPE):
        cid, name = u.effective_chat.id, u.effective_user.first_name or "Trader"
        await self._ensure_user(cid, name)
        if ctx.args and ctx.args[0].startswith("ref_"):
            rc = ctx.args[0][4:]
            for uid, user in _lj(USERS_FILE).items():
                if user.get("ref_code")==rc and uid!=str(cid):
                    refs = _lj(REFS_FILE)
                    if cid not in refs.get(uid,[]):
                        refs.setdefault(uid,[]).append(cid); _sj(REFS_FILE, refs)
                        try: await ctx.bot.send_message(int(uid),f"🎉 Referral berhasil! +{REFERRAL_BONUS} kuota!",parse_mode="HTML")
                        except: pass
                    break
        await self._reply(u,_t(cid,"welcome_new" if str(cid) not in _lj(USERS_FILE) else "welcome_back",
            name=name,tier=_tl(cid),quota_line=_ql(cid)),kb=mk_main(cid))

    async def cmd_help(self, u: Update, ctx): await self._reply(u, _t(u.effective_chat.id,"help"), bk())
    async def cmd_profile(self, u: Update, ctx): await self.cmd_status(u, ctx)

    async def cmd_status(self, u: Update, ctx):
        cid, name = u.effective_chat.id, u.effective_user.first_name or ""
        user = _gu(cid); refs = _lj(REFS_FILE); jt = len(_lj(TRADES_FILE).get(str(cid),[]))
        await self._reply(u, _t(cid,"status",name=name,tier=_tl(cid),q=_ql(cid),
            donated=user.get("donated",0),joined=user.get("joined","—"),wr=_wr(cid),
            jt=jt,ref=len(refs.get(str(cid),[]))), kb=mk_main(cid))

    async def cmd_analyze(self, u: Update, ctx):
        cid = u.effective_chat.id
        if ctx.args:
            s = ctx.args[0].upper(); ok, r = _dq(cid)
            if not ok and not _iv(cid):
                await self._reply(u,_t(cid,"quota_habis",quota=FREE_QUOTA,rb=REFERRAL_BONUS),kb=donate_kb()); return
            ql = f"⚡️ Sisa: {r}" if not _iv(cid) else "♾️ VIP"
            try:
                await self._reply(u, f"🔍 <b>MENGANALISA {s}...</b>\n{SEP}AI sedang memproses data market menggunakan Ollama.\n⏳ Mohon tunggu 5-10 detik...", kb=analyze_kb())
                result = await analyze_market(s)
                dir_emoji = "🟢" if result["direction"] == "BUY" else "🔴" if result["direction"] == "SELL" else "⚪"
                kl = ", ".join(result.get("key_levels", [])) if result.get("key_levels") else "—"
                await self._reply(u, _t(cid, "analyze_result",
                    s=s, dir=f"{dir_emoji} {result['direction']}", conf=result["confidence"],
                    entry=result["entry"], sl=result["stop_loss"], tp=result["take_profit"],
                    analysis=result["analysis"], key_levels=kl, risk=result["risk"], q=ql),
                    kb=analyze_kb())
            except Exception as e:
                LOG.error("AI analysis error: %s", e)
                await self._reply(u, f"❌ <b>AI ERROR</b>\n{SEP}Gagal menganalisa {s}.\n{str(e)[:100]}", kb=analyze_kb())
        else:
            await self._reply(u,_t(cid,"analyze_menu",q=_ql(cid)),kb=analyze_kb())

    async def cmd_donate(self, u: Update, ctx):
        await self._reply(u, _t(u.effective_chat.id,"donate"), donate_kb())

    async def cmd_trades(self, u: Update, ctx):
        cid = u.effective_chat.id; t = _lj(TRADES_FILE).get(str(cid),[])
        if not t: await self._reply(u, _t(cid,"trades_empty"), trades_kb()); return
        e = [f"{'✅' if x.get('result')=='win' else '❌'} {x['symbol']} — {x['pnl']:+.0f}p" for x in t[-10:]]
        await self._reply(u, _t(cid,"trades",e="\n".join(e),wr=_wr(cid),pnl=_pnl(cid)), trades_kb())

    async def cmd_journal(self, u: Update, ctx):
        cid = u.effective_chat.id
        if ctx.args and len(ctx.args)>=2:
            s = ctx.args[0].upper()
            try: pnl = int(ctx.args[1])
            except: await self._reply(u, _t(cid,"journal_add"), bk("trades")); return
            notes = " ".join(ctx.args[2:]) or "—"
            result = "win" if pnl>=0 else "loss"
            t = _lj(TRADES_FILE)
            t.setdefault(str(cid),[]).append({"symbol":s,"pnl":pnl,"result":result,"notes":notes,"date":datetime.now(UTC).strftime("%Y-%m-%d %H:%M")})
            _sj(TRADES_FILE,t)
            await self._reply(u,f"{'✅' if result=='win' else '❌'} <b>DICATAT!</b>\n{SEP}<b>{s}</b> {pnl:+.0f}p\n📝 {notes}\n\n📊 WR: {_wr(cid)}",bk("trades"))
        else:
            t = _lj(TRADES_FILE).get(str(cid),[])
            if not t: await self._reply(u, _t(cid,"journal_empty"), journal_kb()); return
            e = []
            for x in t[-10:]:
                em = "✅" if x.get("result")=="win" else "❌"; d=x.get("date","—")[:10]
                n = x.get("notes",""); ns = f" — {n[:30]}" if n and n!="—" else ""
                e.append(f"{em} <b>{x['symbol']}</b> {x['pnl']:+.0f}p{ns}\n   📅 {d}")
            best = max(t,key=lambda x:x.get("pnl",0)); worst = min(t,key=lambda x:x.get("pnl",0))
            await self._reply(u,_t(cid,"journal",e="\n".join(e),wr=_wr(cid),pnl=_pnl(cid),
                best=f"{best['symbol']} ({best['pnl']:+.0f}p)" if t else"—",
                worst=f"{worst['symbol']} ({worst['pnl']:+.0f}p)" if t else"—"), journal_kb())

    async def cmd_link(self, u: Update, ctx):
        cid = u.effective_chat.id; user = _gu(cid)
        if ctx.args:
            user["stockity_email"] = ctx.args[0]; _su(cid,user)
            await self._reply(u,f"✅ <b>TERLINK!</b>\n{SEP}Akun <b>{ctx.args[0]}</b> tertaut.",mk_main(cid))
        else:
            s = f"✅ <b>{user['stockity_email']}</b>" if user.get("stockity_email") else "❌ Belum"
            await self._reply(u,f"🔗 <b>LINK AKUN</b>\n{SEP}{s}\n\n/link email@example.com",bk())

    async def cmd_referral(self, u: Update, ctx):
        cid = u.effective_chat.id; refs = _lj(REFS_FILE)
        await self._reply(u,_t(cid,"referral",code=_gu(cid).get("ref_code",""),total=len(refs.get(str(cid),[])),bonus=REFERRAL_BONUS),invite_kb(_gu(cid).get("ref_code","")))

    async def cmd_invite(self, u: Update, ctx):
        cid = u.effective_chat.id; refs = _lj(REFS_FILE)
        await self._reply(u,_t(cid,"invite",code=_gu(cid).get("ref_code",""),total=len(refs.get(str(cid),[])),bonus=REFERRAL_BONUS),invite_kb(_gu(cid).get("ref_code","")))

    async def cmd_calendar(self, u: Update, ctx):
        today = datetime.now(UTC); events = [
            ("NFP",today+timedelta(1),"*** HIGH ***","🔴"),
            ("CPI",today+timedelta(2),"*** HIGH ***","🔴"),
            ("FOMC Minutes",today+timedelta(3),"*** HIGH ***","🔴"),
            ("GDP Quarterly",today+timedelta(4),"** MEDIUM **","🟡"),
            ("Retail Sales",today+timedelta(5),"** MEDIUM **","🟡"),]
        e = "\n".join(f"{emo} <b>{n}</b>\n   📅 {d.strftime('%d %b')} — {sev}" for n,d,sev,emo in events)
        await self._reply(u,f"📅 <b>KALENDER BERITA</b>\n{SEP}\n{e}\n{SEP}⚠️ Hati-hati saat rilis berita besar!",bk())

    async def cmd_addtrade(self, u: Update, ctx): await self.cmd_journal(u, ctx)

    async def cmd_share(self, u: Update, ctx):
        cid = u.effective_chat.id
        await self._reply(u,f"📤 <b>BAGIKAN</b>\n{SEP}Pilih yang mau dibagikan:",share_kb())

    async def cmd_watchlist(self, u: Update, ctx):
        cid = u.effective_chat.id; w = _lj(WATCH_FILE)
        if ctx.args and ctx.args[0]=="add" and len(ctx.args)>1:
            s = ctx.args[1].upper()
            w.setdefault(str(cid),[]).append(s); _sj(WATCH_FILE,w)
            await self._reply(u,f"✅ <b>{s}</b> ditambahkan ke watchlist!",bk("watchlist"))
        else:
            items = w.get(str(cid),[])
            if not items: await self._reply(u,_t(cid,"watchlist",items="📭 Kosong. /watchlist add XAUUSD"),bk())
            else:
                btn = [[InlineKeyboardButton(f"❌ {s}",callback_data=f"wl_del:{s}")] for s in items]
                btn.append([InlineKeyboardButton("« Kembali",callback_data="menu_main")])
                await self._reply(u,_t(cid,"watchlist",items="\n".join(f"📌 {s}" for s in items)),InlineKeyboardMarkup(btn))

    async def cmd_alert(self, u: Update, ctx):
        cid = u.effective_chat.id; a = _lj(ALERTS_FILE)
        if ctx.args and len(ctx.args)>=2:
            s = ctx.args[0].upper()
            try: p = float(ctx.args[1])
            except: await self._reply(u,"❌ Harga harus angka!",bk()); return
            a.setdefault(str(cid),[]).append({"symbol":s,"price":p,"created":datetime.now(UTC).strftime("%Y-%m-%d")})
            _sj(ALERTS_FILE,a)
            await self._reply(u,f"✅ Alert <b>{s}</b> di {p:,.0f} aktif!",bk("alert"))
        else:
            alerts = a.get(str(cid),[])
            if not alerts: await self._reply(u,_t(cid,"alert_menu",list="📭 Belum ada alert"),bk())
            else:
                lst = "\n".join(f"🔔 <b>{x['symbol']}</b> → {x['price']:,.0f}" for x in alerts)
                await self._reply(u,_t(cid,"alert_menu",list=lst),bk())

    async def cmd_sinyal(self, u: Update, ctx):
        cid = u.effective_chat.id
        sinyal = "\n".join(f"{'🟢' if d=='BUY' else '🔴'} <b>{s}</b> — {d} ({c}%)\n   💡 {note}" for s,d,c,note in SINYAL_DUMMY)
        status = "🟢 ACTIVE" if _gu(cid).get("lang","id")=="en" else "🟢 HIDUP"
        await self._reply(u,_t(cid,"sinyal_menu",sinyal=sinyal,status=status),bk())

    async def cmd_quiz(self, u: Update, ctx):
        cid = u.effective_chat.id; qz = _lj(QUIZ_FILE)
        qz.setdefault(str(cid),{"q":0,"benar":0,"start":datetime.now(UTC).isoformat()})
        _sj(QUIZ_FILE,qz)
        q = qz[str(cid)]; idx = q["q"]
        if idx >= len(QUIZ_SOAL):
            total = len(QUIZ_SOAL); skor = q["benar"]*100//total
            grade = "S" if skor>=90 else "A" if skor>=80 else "B" if skor>=70 else "C" if skor>=50 else "D"
            user = _gu(cid)
            if skor > user.get("quiz_best",0): user["quiz_best"]=skor; _su(cid,user)
            await self._reply(u,_t(cid,"quiz_selesai",benar=q["benar"],total=total,skor=skor,grade=grade),bk())
            qz[str(cid)]={"q":0,"benar":0,"start":""}; _sj(QUIZ_FILE,qz)
            return
        soal = QUIZ_SOAL[idx]
        opsi = "\n".join(f"{i+1}. {o}" for i,o in enumerate(soal["o"]))
        await self._reply(u,_t(cid,"quiz_start",soal=soal["q"],opsi=opsi),
            InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{i+1}",callback_data=f"quiz:{idx}:{i}") for i in range(len(soal["o"]))],
                [InlineKeyboardButton("⏹ Stop",callback_data="menu_main")],
            ]))

    async def cmd_ticket(self, u: Update, ctx):
        cid = u.effective_chat.id; t = _lj(TICKETS_FILE)
        if ctx.args:
            msg = " ".join(ctx.args)
            tid = f"TKT-{random.randint(1000,9999)}"
            t.setdefault(str(cid),[]).append({"id":tid,"msg":msg,"status":"open","date":datetime.now(UTC).strftime("%Y-%m-%d %H:%M")})
            _sj(TICKETS_FILE,t)
            await self._reply(u,f"🎫 <b>TIKET DIBUAT!</b>\n{SEP}ID: {tid}\nPesan: {msg}\n⏳ Menunggu admin...",bk())
        else:
            tickets = t.get(str(cid),[])
            if not tickets: await self._reply(u,_t(cid,"ticket_create"),bk())
            else:
                lst = "\n".join(f"{'🟢' if x['status']=='open' else '✅'} <b>{x['id']}</b>\n   {x['msg'][:50]} — {x.get('date','')[:10]}" for x in tickets[-5:])
                await self._reply(u,_t(cid,"ticket_status",tickets=lst),bk())

    async def cmd_export(self, u: Update, ctx):
        cid = u.effective_chat.id; t = _lj(TRADES_FILE).get(str(cid),[])
        if not t: await self._reply(u,_t(cid,"trades_empty"),bk()); return
        output = io.StringIO(); w = csv.writer(output)
        w.writerow(["Date","Symbol","PnL","Result","Notes"])
        for x in t: w.writerow([x.get("date",""),x.get("symbol",""),x.get("pnl",0),x.get("result",""),x.get("notes","")])
        from datetime import datetime as dt
        fname = f"trading_journal_{cid}_{dt.now().strftime('%Y%m%d_%H%M%S')}.csv"
        buf = io.BytesIO(output.getvalue().encode())
        buf.name = fname
        await ctx.bot.send_document(chat_id=cid, document=buf,
            caption=f"📤 <b>EXPORT {len(t)} TRADES</b>", parse_mode="HTML")
        await self._reply(u,f"📤 <b>FILE TERKIRIM!</b>\n{SEP}{len(t)} trades → <code>{fname}</code>",bk())

    async def cmd_broadcast(self, u: Update, ctx):
        if str(u.effective_chat.id) != str(ADMIN_ID):
            await self._reply(u, "❌ <b>Admin only.</b>"); return
        if not ctx.args: await self._reply(u,_t(u.effective_chat.id,"broadcast_help"),bk()); return
        msg = " ".join(ctx.args)
        users = _lj(USERS_FILE); count = 0
        for uid in users:
            try: await ctx.bot.send_message(int(uid), f"📢 <b>BROADCAST</b>\n{SEP}\n{msg}", parse_mode="HTML"); count += 1
            except: pass
        await self._reply(u,_t(u.effective_chat.id,"broadcast_done",count=count),bk())

    # ── CALLBACKS ────────────────────────────────────────────────────
    async def hc(self, u: Update, ctx: ContextTypes.DEFAULT_TYPE):
        q = u.callback_query; d = q.data; cid = q.message.chat.id
        name = q.from_user.first_name or ""

        if d == "menu_main": await self._reply(u,_t(cid,"welcome_back",name=name,tier=_tl(cid),quota_line=_ql(cid)),kb=mk_main(cid))
        elif d == "help": await self._reply(u,_t(cid,"help"),bk())
        elif d == "status": await self._reply(u,_t(cid,"status",name=name,tier=_tl(cid),q=_ql(cid),donated=_gu(cid).get("donated",0),joined=_gu(cid).get("joined",""),wr=_wr(cid),jt=len(_lj(TRADES_FILE).get(str(cid),[])),ref=len(_lj(REFS_FILE).get(str(cid),[]))),kb=mk_main(cid))
        elif d == "analyze": await self._reply(u,_t(cid,"analyze_menu",q=_ql(cid)),kb=analyze_kb())
        elif d.startswith("analyze:"):
            s = d.split(":",1)[1].upper(); ok, r = _dq(cid)
            if not ok and not _iv(cid): await self._reply(u,_t(cid,"quota_habis",quota=FREE_QUOTA,rb=REFERRAL_BONUS),kb=donate_kb()); return
            ql = f"⚡️ Sisa: {r}" if not _iv(cid) else "♾️ VIP"
            try:
                await self._reply(u, f"🔍 <b>MENGANALISA {s}...</b>\n{SEP}AI memproses... ⏳", kb=analyze_kb())
                result = await analyze_market(s)
                dir_emoji = "🟢" if result["direction"] == "BUY" else "🔴" if result["direction"] == "SELL" else "⚪"
                kl = ", ".join(result.get("key_levels", [])) if result.get("key_levels") else "—"
                await self._reply(u, _t(cid, "analyze_result",
                    s=s, dir=f"{dir_emoji} {result['direction']}", conf=result["confidence"],
                    entry=result["entry"], sl=result["stop_loss"], tp=result["take_profit"],
                    analysis=result["analysis"], key_levels=kl, risk=result["risk"], q=ql),
                    kb=analyze_kb())
            except Exception as ex:
                LOG.error("AI callback error: %s", ex)
                await self._reply(u, f"❌ <b>AI ERROR</b>\n{SEP}{str(ex)[:100]}", kb=analyze_kb())
        elif d == "donate": await self._reply(u,_t(cid,"donate"),donate_kb())
        elif d.startswith("donate:"):
            tier = d.split(":",1)[1]
            prices = {"coffee":"Rp 15.000","lunch":"Rp 25.000","fuel":"Rp 50.000","custom":"Bebas"}
            names = {"coffee":"☕️ Kopi","lunch":"🍱 Makan Siang","fuel":"🚀 Bensin Full","custom":"💰 Bebas"}
            txt = f"💚 <b>DONASI</b>\n{SEP}Paket: <b>{names.get(tier,tier)}</b>\nNominal: <b>{prices.get(tier,'—')}</b>\n\n🏦 BCA 1234567890 — VILONA AI\n📲 QRIS (semua e-wallet)\n\nSetelah transfer, kirim bukti ke @codergaboets"
            if tier=="custom": txt = f"💰 <b>DONASI BEBAS</b>\n{SEP}🏦 BCA 1234567890 — VILONA AI\n📲 DANA/OVO/Gopay\n\nKirim bukti ke @codergaboets"
            await self._reply(u,txt,bk())
        elif d == "journal": await self.cmd_journal(u, ctx)
        elif d == "journal:add": await self._reply(u,_t(cid,"journal_add"),bk("trades"))
        elif d == "trades": await self.cmd_trades(u, ctx)
        elif d == "share": await self._reply(u,f"📤 <b>BAGIKAN</b>\n{SEP}Pilih:",share_kb())
        elif d.startswith("share:"):
            target = d.split(":",1)[1]
            if target == "journal":
                t = _lj(TRADES_FILE).get(str(cid),[])
                if not t: await self._reply(u,_t(cid,"journal_empty"),bk()); return
                e = [f"{'✅' if x.get('result')=='win' else '❌'} {x['symbol']} — {x['pnl']:+.0f}p" for x in t[-5:]]
                txt = _t(cid,"share_trades",e="\n".join(e),wr=_wr(cid),pnl=_pnl(cid))
                await self._reply(u,f"📤 <b>SIAP DI-BAGIKAN!</b>\n{SEP}<code>{txt[:200]}...</code>",InlineKeyboardMarkup([[InlineKeyboardButton("📤 Share",switch_inline_query=txt[:80])],[InlineKeyboardButton("« Kembali",callback_data="share")]]))
            elif target == "analyze": await self._reply(u,"📤 Gunakan /analyze dulu!",bk("share"))
        elif d == "invite" or d == "referral": await self.cmd_referral(u, ctx)
        elif d.startswith("copy:"):
            rc = d.split(":",1)[1]; link = f"t.me/agent_1ai2_bot?start=ref_{rc}"
            await self._reply(u,f"🔗 <b>LINK UNDANGAN</b>\n{SEP}<code>{link}</code>",InlineKeyboardMarkup([[InlineKeyboardButton("📤 Share",switch_inline_query=link)],[InlineKeyboardButton("« Kembali",callback_data="invite")]]))
        elif d == "lb":
            refs = _lj(REFS_FILE); top = sorted(refs.items(),key=lambda x:len(x[1]),reverse=True)[:5]
            lines = []
            for i,(uid,uids) in enumerate(top,1):
                try: nm = (await ctx.bot.get_chat(int(uid))).first_name
                except: nm = f"User #{uid}"
                lines.append(f"{'🥇' if i==1 else '🥈' if i==2 else '🥉' if i==3 else f'{i}.'} {nm} — {len(uids)} ref")
            rank = next((i for i,(uid,_) in enumerate(top,1) if int(uid)==cid),len(top)+1)
            await self._reply(u,f"🏆 <b>LEADERBOARD</b>\n{SEP}\n"+"\n".join(lines)+f"\n{SEP}👤 Kamu: #{rank}",bk("invite"))
        elif d == "watchlist": await self.cmd_watchlist(u, ctx)
        elif d.startswith("wl_del:"):
            s = d.split(":",1)[1]; w = _lj(WATCH_FILE)
            if str(cid) in w and s in w[str(cid)]: w[str(cid)].remove(s); _sj(WATCH_FILE,w)
            await self.cmd_watchlist(u, ctx)
        elif d == "alert": await self.cmd_alert(u, ctx)
        elif d == "sinyal": await self.cmd_sinyal(u, ctx)
        elif d == "quiz": await self.cmd_quiz(u, ctx)
        elif d.startswith("quiz:"):
            parts = d.split(":"); idx = int(parts[1]); pilih = int(parts[2])
            qz = _lj(QUIZ_FILE); qd = qz.get(str(cid),{"q":0,"benar":0,"start":""})
            soal = QUIZ_SOAL[idx]
            if pilih == soal["a"]: qd["benar"]+=1; await q.answer("✅ Benar!")
            else: await q.answer(f"❌ Salah! Jawaban: {soal['a']+1}")
            qd["q"]+=1; qz[str(cid)]=qd; _sj(QUIZ_FILE,qz)
            await self.cmd_quiz(u, ctx)
        elif d == "ticket": await self.cmd_ticket(u, ctx)
        elif d == "export": await self.cmd_export(u, ctx)
        elif d == "lang":
            user = _gu(cid); current = user.get("lang","id")
            user["lang"] = "en" if current=="id" else "id"; _su(cid,user)
            await self._reply(u,_t(cid,f"lang_{user['lang']}"),kb=mk_main(cid))
        elif d.startswith("trade:"):
            a = d.split(":",1)[1]
            if a=="clear": _sj(TRADES_FILE,{str(cid):[]} if _lj(TRADES_FILE).get(str(cid)) else _lj(TRADES_FILE)); await self._reply(u,_t(cid,"trades_empty"),trades_kb())
            elif a in ("win","loss"): await self._reply(u,_t(cid,"journal_add"),bk("trades"))
        else: await q.answer("...")

    def run(self):
        LOG.info("Starting..."); self.app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    UnifiedTelegramBot().run()
