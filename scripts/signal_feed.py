"""
signal_feed.py — Unified Signal Feed Database + Unified Formatter

Central hub for ALL signals across the ecosystem:
- Channel auto-analyze signals
- User-generated /analyze signals
- TP/SL outcomes

All consumers (channel, bot, website dashboard) read from this single source.
"""

from __future__ import annotations
import json, time, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

WIB = timezone(timedelta(hours=7))
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "vilona_tradefx"
FEED_FILE = DATA_DIR / "signal_feed.json"
FEED_FILE.parent.mkdir(parents=True, exist_ok=True)

MAX_FEED_ENTRIES = 500  # Keep last 500 signals in memory (older → archive)


# ═══════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════

def _load_feed() -> dict:
    try:
        if FEED_FILE.exists():
            return json.loads(FEED_FILE.read_text())
    except Exception:
        pass
    return {"signals": [], "stats": {"total": 0, "tp": 0, "sl": 0, "pending": 0}}


def _save_feed(data: dict) -> None:
    try:
        # Trim old entries
        if len(data.get("signals", [])) > MAX_FEED_ENTRIES:
            data["signals"] = data["signals"][-MAX_FEED_ENTRIES:]
        FEED_FILE.write_text(json.dumps(data, indent=2, default=str))
    except Exception:
        pass


def add_signal(
    symbol: str,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    confidence: float = 0,
    rr_ratio: str | float = "?",
    engines: dict | None = None,
    source: str = "channel-auto",
    source_user: str = "",
    price: float = 0,
    grade: str = "",
    **kwargs
) -> str:
    """
    Record a signal to the unified feed.
    
    Returns signal_id (hash).
    """
    now = datetime.now(WIB)
    signal_id = hashlib.md5(
        f"{symbol}|{direction}|{entry}|{now.isoformat()}".encode()
    ).hexdigest()[:12]
    
    entry_data = {
        "id": signal_id,
        "symbol": symbol.upper(),
        "direction": direction,
        "entry": round(float(entry), 2),
        "sl": round(float(sl), 2),
        "tp": round(float(tp), 2),
        "confidence": round(float(confidence), 2),
        "rr_ratio": str(rr_ratio),
        "grade": grade,
        "engines": engines or {},
        "source": source,           # "channel-auto" | "user-generate"
        "source_user": source_user, # "@username" for user-generated
        "price_at_signal": round(float(price), 2),
        "timestamp": now.isoformat(),
        "status": "pending",        # pending → tp → sl
        "outcome_pips": 0,
        "outcome_time": None,
        **kwargs
    }
    
    feed = _load_feed()
    feed["signals"].append(entry_data)
    feed["stats"]["total"] = feed["stats"].get("total", 0) + 1
    feed["stats"]["pending"] = feed["stats"].get("pending", 0) + 1
    
    # Trim if needed
    if len(feed["signals"]) > MAX_FEED_ENTRIES:
        feed["signals"] = feed["signals"][-MAX_FEED_ENTRIES:]
    
    _save_feed(feed)
    return signal_id


def update_outcome(symbol: str, entry_price: float, result: str, pips: float):
    """Mark a signal as TP or SL."""
    feed = _load_feed()
    updated = False
    # Asset-aware threshold: XAU=5.0, crypto=200, forex=0.005, USOIL=0.3
    sym = symbol.upper()
    if sym in ("XAUUSD", "GOLD"):
        threshold = 5.0
    elif sym in ("BTCUSD", "ETHUSD"):
        threshold = 200.0
    elif sym in ("USOIL", "OIL"):
        threshold = 0.3
    elif sym.endswith("JPY"):
        threshold = 0.5
    else:
        threshold = 0.005  # forex 5-digit
    for sig in feed["signals"]:
        if (sig["symbol"] == symbol.upper() and 
            abs(sig["entry"] - entry_price) < threshold and
            sig["status"] == "pending"):
            sig["status"] = result.lower()
            sig["outcome_pips"] = round(float(pips), 1)
            sig["outcome_time"] = datetime.now(WIB).isoformat()
            if result.upper() == "TP":
                feed["stats"]["tp"] = feed["stats"].get("tp", 0) + 1
            else:
                feed["stats"]["sl"] = feed["stats"].get("sl", 0) + 1
            feed["stats"]["pending"] = max(0, feed["stats"].get("pending", 1) - 1)
            updated = True
            break
    if updated:
        _save_feed(feed)


def get_recent_signals(limit: int = 20) -> list[dict]:
    """Get most recent signals for dashboard display."""
    feed = _load_feed()
    return feed["signals"][-limit:]


def get_user_signals(limit: int = 20) -> list[dict]:
    """Get user-generated signals only."""
    feed = _load_feed()
    user_sigs = [s for s in feed["signals"] if s.get("source") == "user-generate"]
    return user_sigs[-limit:]


def get_stats() -> dict:
    """Get feed statistics."""
    feed = _load_feed()
    return feed.get("stats", {})


# ═══════════════════════════════════════════
#  UNIFIED FORMATTER
# ═══════════════════════════════════════════

def fmt_signal_unified(
    sig: dict,
    price: float = 0,
    display: str = "XAUUSD",
    currency: str = "$",
    dxy: float | None = None,
    h: int = 0,
    is_channel: bool = True,
    source_user: str = ""
) -> str:
    """
    Unified signal format — used EVERYWHERE (channel, bot, website).
    
    Sections:
    1. Header — direction, symbol, timestamp
    2. Entry/SL/TP — price levels with pip distances
    3. Engine Analysis — WHY this signal (detailed reasoning)
    4. Stats — RR, confidence, grade
    5. DYOR disclaimer
    6. Donation CTA
    7. Source tag (channel-auto or @username)
    """
    action = sig.get("action", "HOLD")
    emoji = {"BUY": "\U0001f7e2", "SELL": "\U0001f534", "HOLD": "\u26aa\ufe0f"}.get(action, "\u26aa\ufe0f")
    grade = sig.get("grade", "")
    conf = sig.get("confidence", 0)
    if isinstance(conf, (int, float)) and conf > 10:
        conf = conf / 100
    
    rr = sig.get("rr_ratio", "?")
    if isinstance(rr, str) and rr.startswith("1:"):
        rr = rr[2:]
    
    entry = sig.get("entry") or price or 0
    sl = sig.get("sl") or 0
    tp = sig.get("tp") or 0
    
    now_wib = datetime.now(WIB)
    session_map = {7: "Asia", 8: "Asia", 9: "Asia", 10: "Asia", 11: "Asia",
                   12: "Pre-London", 13: "Pre-London",
                   14: "London \U0001f1ec\U0001f1e7", 15: "London \U0001f1ec\U0001f1e7",
                   16: "London \U0001f1ec\U0001f1e7", 17: "London \U0001f1ec\U0001f1e7",
                   18: "Pre-NY", 19: "NY \U0001f1fa\U0001f1f8", 20: "NY \U0001f1fa\U0001f1f8",
                   21: "NY \U0001f1fa\U0001f1f8", 22: "NY \U0001f1fa\U0001f1f8"}
    session = session_map.get(h, "Outside Killzone")
    
    # Pip helper
    def _pips(dist):
        if display in ("XAUUSD", "GOLD"):
            return f"{dist:.0f} pt"
        elif display in ("EURUSD", "GBPUSD", "USDJPY"):
            return f"{dist:.1f} pip"
        elif display == "BTCUSD":
            return f"${dist:.0f}"
        else:
            return f"{dist:.0f} pt"
    
    def _fmt(v):
        if display in ("BBCA", "BBRI", "IHSG"):
            return f"Rp{v:,.0f}"
        return f"{currency}{v:.2f}" if currency else f"{v:.2f}"
    
    # ═══ SECTION 1: HEADER ═══
    lines = [
        f"{emoji} <b>SINYAL {action} — {display}</b>",
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
        f"\U0001f550 {now_wib.strftime('%Y.%m.%d %H:%M')} WIB | Session: {session}",
    ]
    
    # ═══ SECTION 2: PRICE LEVELS ═══
    lines.append("")
    lines.append(f"\U0001f4cd <b>Entry:</b> {_fmt(entry)}")
    if sl:
        sl_dist = _pips(abs(sl - entry))
        lines.append(f"\U0001f534 <b>SL:</b> {_fmt(sl)} (-{sl_dist})")
    if tp:
        tp_dist = _pips(abs(tp - entry))
        lines.append(f"\U0001f7e2 <b>TP:</b> {_fmt(tp)} (+{tp_dist})")
    if rr and rr != "?":
        lines.append(f"\U0001f4d0 <b>RR Ratio:</b> 1:{rr}")
    
    # ═══ SECTION 3: ENGINE ANALYSIS ═══
    engines = sig.get("engines", {})
    source_name = sig.get("source", "")
    models = sig.get("_models", "")
    
    if engines:
        lines.append("")
        lines.append(f"\U0001f9e0 <b>ENGINE ANALYSIS:</b>")
        for eng_name, eng_detail in engines.items():
            if isinstance(eng_detail, dict):
                conf_pct = eng_detail.get("confidence", 0)
                reason = eng_detail.get("reason", eng_detail.get("details", ""))
                if conf_pct > 0:
                    lines.append(f"\u2022 <b>{eng_name}:</b> {reason} ({conf_pct:.0%})")
                elif reason:
                    lines.append(f"\u2022 <b>{eng_name}:</b> {reason}")
            elif isinstance(eng_detail, str):
                lines.append(f"\u2022 <b>{eng_name}:</b> {eng_detail}")
    elif source_name:
        lines.append("")
        lines.append(f"\U0001f9e0 <b>Source:</b> {source_name}")
    
    if models:
        voters = sig.get("voters", sig.get("ensemble", "?"))
        lines.append(f"\U0001f916 <b>AI Models:</b> {models} ({voters} model)" if voters != "?" else f"\U0001f916 <b>AI:</b> {models}")
    
    # ═══ SECTION 4: STATS ═══
    lines.append("")
    lines.append(f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    stat_parts = []
    if conf:
        stat_parts.append(f"Confidence: {conf:.0%}")
    if grade:
        stat_parts.append(f"Grade: {grade}")
    if rr and rr != "?":
        stat_parts.append(f"RR 1:{rr}")
    if dxy:
        stat_parts.append(f"DXY: {dxy:.1f}")
    if stat_parts:
        lines.append(f"\U0001f4ca {' | '.join(stat_parts)}")
    
    # ═══ SECTION 5: DYOR DISCLAIMER ═══
    lines.append(f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
    lines.append(f"\u26a0\ufe0f <b>DYOR — Do Your Own Research</b>")
    lines.append(f"Ini hasil deteksi otomatis AI & mesin analisa teknikal.")
    lines.append(f"Bukan ajakan trading. Keputusan & risiko sepenuhnya di tangan Anda.")
    lines.append(f"Selalu pakai manajemen risiko yang ketat.")
    
    # ═══ SECTION 6: DONATION CTA ═══
    lines.append("")
    lines.append(f"\U0001f49a <b>Kalau sinyal ini cuan, isi bensin AI!</b>")
    lines.append(f"Server analisa 24/7 butuh biaya API & GPU.")
    lines.append(f"\U0001f449 /donate — dukung seikhlasnya, AKTIF PERMANEN")
    
    # ═══ SECTION 7: SOURCE TAG ═══
    lines.append("")
    if source_user:
        lines.append(f"\U0001f4e1 <i>Sinyal digenerate oleh @{source_user}</i>")
    else:
        lines.append(f"\U0001f4e1 <i>Sinyal dari Vilona AI Auto-Scanner</i>")
    
    return "\n".join(lines)


def fmt_signal_mini(sig: dict, price: float = 0, display: str = "XAUUSD", currency: str = "$") -> str:
    """Compact format for dashboard feed cards."""
    action = sig.get("action", "HOLD")
    emoji = {"BUY": "\U0001f7e2", "SELL": "\U0001f534"}.get(action, "\u26aa\ufe0f")
    entry = sig.get("entry") or price or 0
    sl = sig.get("sl") or 0
    tp = sig.get("tp") or 0
    conf = sig.get("confidence", 0)
    if isinstance(conf, (int, float)) and conf > 10:
        conf = conf / 100
    rr = sig.get("rr_ratio", "?")
    
    lines = [
        f"{emoji} {action} {display} @ {entry}",
        f"SL: {sl} | TP: {tp} | RR 1:{rr} | {conf:.0%}",
    ]
    return "\n".join(lines)


def fmt_user_activity(signals: list[dict]) -> str:
    """Format user-generated signals feed for dashboard."""
    if not signals:
        return "<i>Belum ada user yang generate sinyal hari ini.</i>"
    
    lines = []
    for sig in reversed(signals[-10:]):
        user = sig.get("source_user", "anon")
        action = sig.get("direction", "HOLD")
        symbol = sig.get("symbol", "?")
        emoji = {"BUY": "\U0001f7e2", "SELL": "\U0001f534"}.get(action, "\u26aa\ufe0f")
        ts = sig.get("timestamp", "")[:16]
        lines.append(
            f"\u2022 @{user} \u2192 {emoji} <b>{action} {symbol}</b> "
            f"<i>({ts})</i>"
        )
    return "\n".join(lines)
