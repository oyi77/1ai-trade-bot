"""
trade_tracker.py — Trade Outcome Tracker & Win Rate Calculator

Monitors open positions, detects TP/SL hits, records outcomes.
Calculates pips, profit/loss (USD + Rp), and win rate.

Commands provided:
  /winrate  — Win rate summary
  /history  — Recent trade history
"""
from __future__ import annotations
import json, logging, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "trade_history.json"
# Fallback: also check the direct project-root path
_DATA_FALLBACK = Path(__file__).resolve().parent.parent / "data" / "trade_history.json"
if not DATA_FILE.exists() and _DATA_FALLBACK.exists():
    DATA_FILE = _DATA_FALLBACK

# USD → IDR rate (update periodically — last: Jun 2026)
USD_IDR = 16350


def _load() -> dict:
    try:
        if DATA_FILE.exists():
            text = DATA_FILE.read_text().strip()
            if not text:
                raise ValueError("empty")
            payload = json.loads(text)
            if isinstance(payload, list):
                payload = {"trades": payload, "stats": {"total": 0, "wins": 0, "losses": 0, "breakeven": 0, "total_pips": 0.0, "total_profit_usd": 0.0, "best_win_pips": 0.0, "worst_loss_pips": 0.0}}
            elif not isinstance(payload, dict):
                raise ValueError("invalid")
            return payload
    except Exception:
        pass
    return {"trades": [], "stats": {"total": 0, "wins": 0, "losses": 0, "breakeven": 0,
            "total_pips": 0.0, "total_profit_usd": 0.0, "best_win_pips": 0.0, "worst_loss_pips": 0.0}}


def _save(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2, default=str))


def _pip_value(symbol: str) -> float:
    """Pip value in USD for 1 standard lot."""
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"): return 1.0    # $1 per 0.1 pip = $10 per pip for 1 lot
    if s in ("BTCUSD", "BTC"): return 1.0
    if s in ("ETHUSD", "ETH"): return 0.01     # $0.01 per 0.01 pip = $1 per pip
    if s.endswith("JPY"): return 9.0
    return 10.0  # standard forex


def _pip_size(symbol: str) -> float:
    s = symbol.upper()
    if s in ("XAUUSD", "GOLD"): return 0.1
    if s in ("BTCUSD", "BTC"): return 1.0
    if s in ("ETHUSD", "ETH"): return 0.01
    if s.endswith("JPY"): return 0.01
    if s in ("USOIL", "OIL", "CL"): return 0.01
    return 0.0001


def _to_pips(price_diff: float, symbol: str) -> float:
    ps = _pip_size(symbol)
    return price_diff / ps if ps > 0 else 0.0


def open_trade(signal: dict, entry_price: float, symbol: str = "XAUUSD",
               source: str = "ai", chat_id: str = "",
               telegram_message_id: int | None = None) -> str | None:
    """
    Record a new trade when signal is sent to EA.
    Returns trade_id or None.
    """
    if not signal or signal.get("action") not in ("BUY", "SELL"):
        return None

    # ── Price sanity: reject entries >10% off typical range for the symbol ──
    entry_price = float(entry_price) if entry_price else 0
    if symbol.upper() in ("XAUUSD", "GOLD") and (entry_price < 2000 or entry_price > 6000):
        logger.warning(f"open_trade REJECTED [{symbol}]: entry={entry_price} outside valid range (2000-6000)")
        return None
    if symbol.upper() in ("BTCUSD", "BTC") and (entry_price < 10000 or entry_price > 200000):
        logger.warning(f"open_trade REJECTED [{symbol}]: entry={entry_price} outside valid range")
        return None
    if symbol.upper() in ("ETHUSD", "ETH") and (entry_price < 500 or entry_price > 10000):
        logger.warning(f"open_trade REJECTED [{symbol}]: entry={entry_price} outside valid range")
        return None

    data = _load()
    trade_id = f"tr_{int(time.time() * 1000)}"

    sl = signal.get("sl", 0)
    tp = signal.get("tp", 0)
    tp1 = signal.get("tp1", 0)
    tp2 = signal.get("tp2", 0)

    trade = {
        "id": trade_id,
        "symbol": symbol,
        "action": signal["action"],
        "entry": entry_price,
        "sl": sl,
        "tp": tp,
        "tp1": tp1 if tp1 else None,
        "tp2": tp2 if tp2 else None,
        "open_time": datetime.now(WIB).isoformat(),
        "close_time": None,
        "close_price": None,
        "outcome": "OPEN",        # OPEN, TP_HIT, SL_HIT, MANUAL, BREAKEVEN
        "pips": 0.0,
        "profit_usd": 0.0,
        "profit_idr": 0,
        "source": source,
        "confidence": signal.get("confidence", 0),
        "grade": signal.get("grade", "?"),
        "chat_id": str(chat_id),
        "telegram_message_id": telegram_message_id,  # reply chain
    }
    data["trades"].append(trade)
    _save(data)
    logger.info(f"📝 Trade opened: {trade_id} {signal['action']} {symbol} @ {entry_price}")
    return trade_id


def check_outcomes(current_prices: dict[str, float] | None = None) -> list[dict]:
    """
    Check all open trades against current prices.
    Close any that hit TP or SL.

    Args:
        current_prices: dict of symbol→price (e.g. {"XAUUSD": 4350.5})

    Returns:
        List of trades that were just closed
    """
    if not current_prices:
        return []

    data = _load()
    closed = []
    dirty = False

    for trade in data["trades"]:
        if trade.get("outcome") != "OPEN":
            continue

        symbol = trade.get("symbol", "XAUUSD")
        # Normalize symbol for lookup
        lookup = symbol.upper()
        price = current_prices.get(lookup, current_prices.get(symbol, 0))
        if not price or price <= 0:
            continue

        action = trade.get("action", "BUY")
        sl = trade.get("sl", 0)
        tp = trade.get("tp", 0)
        try:
            entry = float(trade.get("entry", price) or 0)
        except Exception:
            entry = 0.0
        if entry <= 0:
            trade["outcome"] = "IGNORED_INVALID_ENTRY"
            trade["close_time"] = datetime.now(WIB).isoformat()
            logger.warning("Trade ignored: invalid entry=%s id=%s symbol=%s", entry, trade.get("id"), symbol)
            dirty = True
            continue
        if not trade.get("telegram_message_id"):
            trade["outcome"] = "IGNORED_NO_SIGNAL_REPLY"
            trade["close_time"] = datetime.now(WIB).isoformat()
            logger.warning("Trade ignored: missing telegram_message_id id=%s symbol=%s", trade.get("id"), symbol)
            dirty = True
            continue

        hit = None
        close_price = price

        # Check SL hit
        if sl > 0:
            if action == "BUY" and price <= sl:
                hit = "SL_HIT"
                close_price = sl
            elif action == "SELL" and price >= sl:
                hit = "SL_HIT"
                close_price = sl

        # Check TP hit
        if not hit and tp > 0:
            if action == "BUY" and price >= tp:
                hit = "TP_HIT"
                close_price = tp
            elif action == "SELL" and price <= tp:
                hit = "TP_HIT"
                close_price = tp

        if not hit:
            continue

        # Calculate outcome
        if action == "BUY":
            pip_diff = _to_pips(close_price - entry, symbol)
        else:
            pip_diff = _to_pips(entry - close_price, symbol)

        is_win = hit == "TP_HIT"
        pip_val = _pip_value(symbol)
        # pip_diff is now signed: positive=profit, negative=loss
        profit_loss = pip_diff * pip_val

        trade["outcome"] = hit
        trade["close_time"] = datetime.now(WIB).isoformat()
        trade["close_price"] = close_price
        trade["pips"] = round(pip_diff, 1)
        trade["profit_usd"] = round(profit_loss, 2)
        trade["profit_idr"] = round(profit_loss * USD_IDR)

        # Update stats
        s = data["stats"]
        s["total"] += 1
        s["total_pips"] += pip_diff  # signed: wins add, losses subtract

        if is_win:
            s["wins"] += 1
            s["total_profit_usd"] += profit_loss
            if pip_diff > s["best_win_pips"]:
                s["best_win_pips"] = round(pip_diff, 1)
        else:
            s["losses"] += 1
            s["total_profit_usd"] += profit_loss  # profit_loss already negative
            loss_pips = abs(pip_diff)
            if loss_pips > s["worst_loss_pips"]:
                s["worst_loss_pips"] = round(loss_pips, 1)

        closed.append(trade)
        emoji = "✅" if is_win else "❌"
        logger.info(f"{emoji} Trade closed: {trade['id']} {hit} | {pip_diff:.1f} pips | ${profit_loss:+.2f}")

    if closed or dirty:
        _save(data)

    return closed


def close_trade_manually(trade_id: str, close_price: float, symbol: str = "XAUUSD") -> dict | None:
    """Manually close a trade (for bridge/webhook)."""
    data = _load()
    for trade in data["trades"]:
        if trade.get("id") == trade_id and trade.get("outcome") == "OPEN":
            entry = trade.get("entry", close_price)
            action = trade.get("action", "BUY")

            if action == "BUY":
                pip_diff = _to_pips(close_price - entry, symbol)
            else:
                pip_diff = _to_pips(entry - close_price, symbol)

            is_win = pip_diff > 0
            pip_val = _pip_value(symbol)
            # pip_diff is now signed: positive=profit, negative=loss
            profit_loss = pip_diff * pip_val

            trade["outcome"] = "MANUAL"
            trade["close_time"] = datetime.now(WIB).isoformat()
            trade["close_price"] = close_price
            trade["pips"] = round(pip_diff, 1)
            trade["profit_usd"] = round(profit_loss, 2)
            trade["profit_idr"] = round(profit_loss * USD_IDR)

            s = data["stats"]
            s["total"] += 1
            s["total_pips"] += pip_diff  # signed
            if is_win:
                s["wins"] += 1
                s["total_profit_usd"] += profit_loss
            else:
                s["losses"] += 1
                s["total_profit_usd"] += profit_loss  # profit_loss already negative

            _save(data)
            return trade
    return None


def get_stats() -> dict:
    """Get current stats."""
    data = _load()
    s = data["stats"]
    total = s["total"]
    wins = s["wins"]
    losses = s["losses"]

    return {
        **s,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
        "open_positions": sum(1 for t in data["trades"] if t.get("outcome") == "OPEN"),
        "total_profit_idr": round(s["total_profit_usd"] * USD_IDR),
        "avg_win_pips": round(s["total_pips"] / wins, 1) if wins > 0 else 0.0,
        "avg_loss_pips": round(s["worst_loss_pips"], 1) if losses > 0 else 0.0,
    }


def get_recent_trades(limit: int = 10) -> list[dict]:
    """Get most recent closed trades."""
    data = _load()
    closed = [t for t in data["trades"] if t.get("outcome") not in ("OPEN", None)]
    closed.sort(key=lambda t: t.get("close_time", ""), reverse=True)
    return closed[:limit]


def get_open_trades() -> list[dict]:
    """Get all currently open trades."""
    data = _load()
    return [t for t in data["trades"] if t.get("outcome") == "OPEN"]


def format_winrate() -> str:
    """Format win rate summary for Telegram."""
    stats = get_stats()
    total = stats["total"]
    wins = stats["wins"]
    losses = stats["losses"]
    wr = stats["win_rate"]
    open_pos = stats["open_positions"]

    # Emoji based on win rate
    if wr >= 60:
        perf = "🟢"
    elif wr >= 40:
        perf = "🟡"
    else:
        perf = "🔴"

    lines = [
        f"📊 <b>TRADE PERFORMANCE</b>",
        f"━━━━━━━━━━━━━━━━",
        f"{perf} Win Rate: <b>{wr:.1f}%</b> ({wins}W / {losses}L)",
        f"📈 Total Trades: {total} | Open: {open_pos}",
        f"━━━━━━━━━━━━━━━━",
        f"💰 Total Pips: {stats['total_pips']:+.1f}",
        f"💵 Profit: <b>${stats['total_profit_usd']:+,.2f}</b> (Rp {stats['total_profit_idr']:+,})",
    ]

    if wins > 0:
        lines.append(f"✅ Best Win: +{stats['best_win_pips']:.1f} pips")
    if losses > 0:
        lines.append(f"❌ Worst Loss: -{stats['worst_loss_pips']:.1f} pips")

    return "\n".join(lines)


def format_history(limit: int = 10) -> str:
    """Format recent trade history for Telegram."""
    trades = get_recent_trades(limit)
    if not trades:
        return "📭 Belum ada riwayat trade."

    lines = ["📋 <b>RIWAYAT TRADE</b>", "━━━━━━━━━━━━━━━━"]
    for t in trades[:limit]:
        emoji = "✅" if t["outcome"] == "TP_HIT" else "❌" if t["outcome"] == "SL_HIT" else "⚪"
        pips = t.get("pips", 0)
        usd = t.get("profit_usd", 0)
        idr = t.get("profit_idr", 0)
        action = t.get("action", "?")
        sym = t.get("symbol", "?")
        close_t = t.get("close_time", "")[:16].replace("T", " ")
        lines.append(
            f"{emoji} {action} {sym} | {t['outcome']}\n"
            f"   Pips: {pips:+.1f} | ${usd:+.2f} (Rp {idr:+,})\n"
            f"   {close_t}"
        )

    return "\n".join(lines)


def format_trade_close_alert(trade: dict) -> str:
    """Format a single trade close alert — clean, GMFX-style."""
    emoji = "✅" if trade["outcome"] == "TP_HIT" else "❌"
    pips = trade.get("pips", 0)
    usd = trade.get("profit_usd", 0)
    idr = trade.get("profit_idr", 0)
    action = trade.get("action", "?")
    symbol = trade.get("symbol", "?")
    entry = trade.get("entry", 0)
    close_p = trade.get("close_price", 0)

    outcome_label = {
        "TP_HIT": "TAKE PROFIT 🎯", "SL_HIT": "STOP LOSS 🛑",
        "MANUAL": "MANUAL CLOSE", "BREAKEVEN": "BREAKEVEN ⚖️"
    }.get(trade["outcome"], trade["outcome"])

    stats = get_stats()
    wr = stats.get("win_rate", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    total = stats.get("total", 0)

    # Format timestamp cleanly
    called_on = trade.get("open_time", "")
    if called_on:
        try:
            dt = datetime.fromisoformat(called_on.replace("Z", "+00:00"))
            called_on = dt.strftime("%d/%m %H:%M WIB")
        except Exception:
            pass
    called_line = f" | 🕐 {called_on}" if called_on else ""

    result = trade["outcome"]
    is_win = result == "TP_HIT"
    
    msg = (
        f"📢 <b>TRADE RESULT — {outcome_label}</b>\n"
        f"   ⬆️ Signal sebelumnya{called_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {action} {symbol} | Entry: {entry} → Close: {close_p}\n"
        f"📐 Pips: <b>{pips:+.1f}</b> | P&L: <b>${usd:+.2f}</b> (Rp {idr:+,})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Winrate: {wr:.1f}% ({wins}W/{losses}L — {total} sinyal)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    if is_win:
        msg += (
            f"🎉 <b>CUAN! Profit secured!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"\n"
            f"🤝 AI Partner lu makin tajem.\n"
            f"   Tapi signal selanjutnya bisa lebih akurat lagi.\n"
            f"   Bayangin 3 AI + Grok News analisa bareng.\n"
            f"\n"
            f"⚡ <b>/subscribe</b> — Rp 50k/bulan\n"
            f"   Unlock AI Signal + Grok News + /levels\n"
        )
    else:
        msg += (
            f"💪 Loss is part of the game.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"\n"
            f"🔋 AI butuh lebih banyak tenaga buat analisa.\n"
            f"   1 AI doang kadang miss — 3 AI lebih presisi.\n"
            f"\n"
            f"⚡ <b>/subscribe</b> — upgrade AI lu sekarang\n"
            f"   Jangan biarin AI lu kerja sendirian\n"
        )
    
    return msg


def format_trade_close_with_cta(trade: dict) -> tuple:
    """Format trade close alert WITH donation CTA inline keyboard.
    
    Returns (text: str, markup: dict) — ready for tg_send with reply_markup.
    """
    emoji = "✅" if trade["outcome"] == "TP_HIT" else "❌"
    pips = trade.get("pips", 0)
    usd = trade.get("profit_usd", 0)
    idr = trade.get("profit_idr", 0)
    action = trade.get("action", "?")
    symbol = trade.get("symbol", "?")
    outcome = trade.get("outcome", "?")

    outcome_label = {
        "TP_HIT": "TAKE PROFIT 🎯", "SL_HIT": "STOP LOSS 🛑",
        "MANUAL": "MANUAL CLOSE", "BREAKEVEN": "BREAKEVEN ⚖️"
    }.get(outcome, outcome)

    stats = get_stats()
    called_on = trade.get("open_time", "")
    called_line = f"\n🗓️ Called on: {called_on}" if called_on else ""

    is_tp = outcome == "TP_HIT"

    if is_tp:
        # ── TP HIT — Victory copywriting ──
        text = (
            f"{emoji} <b>TRADE CLOSED — {outcome_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {action} {symbol} | {pips:+.1f} pips | Rp {idr:+,}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 <b>CUAN! AI Partner lu mendaratkan profit!</b>\n"
            f"\n"
            f"🤝 AI Partner lu makin tajem tiap hari.\n"
            f"   Tapi signal bisa lebih akurat lagi kalo\n"
            f"   lu upgrade ke full AI + Grok News.\n"
            f"   Bayangin 3 AI analisa bareng...\n"
            f"\n"
            f"📰 Grok News [🔒 LOCKED]\n"
            f"   Real-time X/Twitter context\n"
            f"\n"
            f"⬇️ Dukung AI Partner lu ⬇️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Winrate: {stats['win_rate']:.1f}% ({stats['wins']}W/{stats['losses']}L){called_line}"
        )
        markup = {"inline_keyboard": [
            [{"text": "⭐ Subscribe PRO (Rp50K)", "callback_data": "sub:pro"}],
            [{"text": "🚀 Subscription (Nominal Bebas)", "callback_data": "sub:elite"}],
        ]}
    else:
        # ── SL HIT — Humble learning + upgrade funnel ──
        text = (
            f"{emoji} <b>TRADE CLOSED — {outcome_label}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {action} {symbol} | {pips:+.1f} pips | Rp {idr:+,}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"😔 Market liar hari ini. AI catat sebagai lesson.\n"
            f"\n"
            f"🔋 <b>AI Power: ■□□□□ 33%</b> — cuma 1 AI kerja\n"
            f"   Makin banyak AI = makin sedikit false signal.\n"
            f"   Upgrade ke 3 AI + Grok News biar lebih presisi.\n"
            f"\n"
            f"📰 Grok News [🔒 LOCKED]\n"
            f"   Mungkin SL ini bisa dihindari kalo ada context.\n"
            f"\n"
            f"⬇️ Dukung AI biar makin pinter ⬇️\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Winrate: {stats['win_rate']:.1f}% ({stats['wins']}W/{stats['losses']}L){called_line}"
        )
        markup = {"inline_keyboard": [
            [{"text": "📚 Dukung AI Belajar (Tripay)", "callback_data": "sub:pro"}],
        ]}

    return text, markup


# ═══════════════════════════════════════════════════════════════════
# DAILY RECAP — Marketing / Gimmick Report
# ═══════════════════════════════════════════════════════════════════

# $100 modal, 0.01 lot (micro lot)
MICRO_LOT_PIP_VALUE = {
    "XAUUSD": 0.10, "GOLD": 0.10,
    "EURUSD": 0.10, "GBPUSD": 0.10, "USDJPY": 0.09,
    "BTCUSD": 0.01, "BTC": 0.01,
    "USOIL": 0.10, "OIL": 0.10, "CL": 0.10,
}
DEFAULT_MICRO_PIP = 0.10
MODAL_USD = 100


def get_daily_trades(date_str: str = "") -> dict:
    """
    Get all trades for a specific date (YYYY-MM-DD, WIB).
    Returns dict with trades list + summary stats.
    """
    if not date_str:
        date_str = datetime.now(WIB).strftime("%Y-%m-%d")

    data = _load()
    trades = data.get("trades", [])
    
    daily = [t for t in trades if t.get("open_time", "").startswith(date_str)]
    
    wins = [t for t in daily if t.get("outcome") == "TP_HIT"]
    losses = [t for t in daily if t.get("outcome") == "SL_HIT"]
    open_pos = [t for t in daily if t.get("outcome") == "OPEN"]
    
    total_pips = sum(t.get("pips", 0) for t in daily if t.get("outcome") not in ("OPEN", None))
    
    # Calculate $100 micro lot profit
    micro_profit = 0.0
    for t in daily:
        if t.get("outcome") in ("OPEN", None):
            continue
        sym = t.get("symbol", "XAUUSD").upper()
        pip_val = MICRO_LOT_PIP_VALUE.get(sym, DEFAULT_MICRO_PIP)
        micro_profit += t.get("pips", 0) * pip_val
    
    # Pair summary
    pairs = {}
    for t in daily:
        sym = t.get("symbol", "?")
        if sym not in pairs:
            pairs[sym] = {"total": 0, "wins": 0, "losses": 0, "pips": 0.0}
        pairs[sym]["total"] += 1
        if t.get("outcome") == "TP_HIT":
            pairs[sym]["wins"] += 1
        elif t.get("outcome") == "SL_HIT":
            pairs[sym]["losses"] += 1
        pairs[sym]["pips"] += t.get("pips", 0)
    
    return {
        "date": date_str,
        "trades": daily,
        "total_signals": len(daily),
        "wins": len(wins),
        "losses": len(losses),
        "open": len(open_pos),
        "total_pips": round(total_pips, 1),
        "win_rate": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
        "micro_profit": round(micro_profit, 2),
        "micro_profit_pct": round(micro_profit / MODAL_USD * 100, 1),
        "micro_profit_idr": round(micro_profit * USD_IDR),
        "pairs": pairs,
    }


def format_daily_recap(date_str: str = "") -> str:
    """
    Format daily recap report — marketing/gimmick style.
    Shows signals, pairs, win/loss, $100 micro lot profit projection.
    """
    recap = get_daily_trades(date_str)
    
    if not date_str:
        date_str = datetime.now(WIB).strftime("%Y-%m-%d")
    
    # Format date nicely
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_names = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
        day_id = day_names[dt.weekday()]
        date_display = f"{day_id}, {dt.strftime('%d %B %Y')}"
    except Exception:
        date_display = date_str
    
    total = recap["total_signals"]
    wins = recap["wins"]
    losses = recap["losses"]
    wr = recap["win_rate"]
    pips = recap["total_pips"]
    micro = recap["micro_profit"]
    micro_pct = recap["micro_profit_pct"]
    micro_idr = recap["micro_profit_idr"]
    
    # Performance emoji
    if micro > 0:
        perf = "🟢 PROFIT"
    elif micro < 0:
        perf = "🔴 LOSS"
    else:
        perf = "⚪ FLAT"
    
    lines = [
        f"📊 <b>REKAP SINYAL HARIAN</b>",
        f"🗓 {date_display}",
        f"━━━━━━━━━━━━━━━━",
        f"",
        f"📡 <b>Total Sinyal:</b> {total}",
        f"✅ Win: {wins} | ❌ Loss: {losses} | 📊 WR: {wr:.1f}%",
        f"",
        f"━━━━━━━━━━━━━━━━",
        f"📐 <b>Total Pips:</b> {pips:+.1f}",
        f"",
    ]
    
    # Pair breakdown
    pairs = recap.get("pairs", {})
    if pairs:
        lines.append("💱 <b>Pair yang Di-trade:</b>")
        for sym, stats in sorted(pairs.items()):
            p_emoji = "✅" if stats["pips"] >= 0 else "❌"
            lines.append(f"   {p_emoji} {sym}: {stats['total']} sinyal | {stats['pips']:+.1f} pips | {stats['wins']}W/{stats['losses']}L")
    
    lines.extend([
        "",
        f"━━━━━━━━━━━━━━━━",
        f"💵 <b>SIMULASI MODAL $100 (0.01 Lot)</b>",
        f"",
        f"{perf}: <b>${micro:+.2f}</b> (Rp {micro_idr:+,})",
        f"Return: <b>{micro_pct:+.1f}%</b> dalam 1 hari",
        f"",
        f"━━━━━━━━━━━━━━━━",
        f"",
        f"⚡ <i>Ini simulasi — bukan hasil trading sebenarnya.</i>",
        f"📱 Trading real: /analyze xauusd",
        f"🤖 Auto-trade: /autosync on",
        f"",
        f"<i>#VilonaTradeFX #AITrading #XAUUSD</i>",
    ])
    
    return "\n".join(lines)


def format_mini_recap(date_str: str = "") -> str:
    """Shorter recap for channel broadcast — less text, more punch."""
    recap = get_daily_trades(date_str)
    
    total = recap["total_signals"]
    wins = recap["wins"]
    losses = recap["losses"]
    wr = recap["win_rate"]
    micro = recap["micro_profit"]
    micro_pct = recap["micro_profit_pct"]
    
    if total == 0:
        return (
            f"📊 <b>REKAP HARIAN</b> — Belum ada sinyal hari ini\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Bot auto-scan 24/7 — sinyal masuk saat ada setup.\n"
            f"Pantau: /analyze xauusd | /winrate"
        )
    
    perf = "🟢" if micro > 0 else "🔴" if micro < 0 else "⚪"
    
    return (
        f"📊 <b>REKAP SINYAL HARI INI</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📡 {total} sinyal | {wins}✅ {losses}❌ | WR: {wr:.1f}%\n"
        f"📐 Total: {recap['total_pips']:+.1f} pips\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💵 Modal $100 (0.01 Lot): {perf} <b>${micro:+.2f}</b> ({micro_pct:+.1f}%)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"/winrate — Lihat performa lengkap\n"
        f"/history — Riwayat trade"
    )


# ── Quick test ──
if __name__ == "__main__":
    print("=== Trade Tracker Test ===\n")

    # Simulate opening a trade
    sig = {"action": "SELL", "sl": 4370, "tp": 4330, "tp1": 4345, "confidence": 0.78, "grade": "B"}
    tid = open_trade(sig, 4350, "XAUUSD", "ai", "123456")
    print(f"Opened: {tid}")

    # Simulate checking (price hit TP)
    closed = check_outcomes({"XAUUSD": 4325})
    for c in closed:
        print(f"\n{format_trade_close_alert(c)}")

    print(f"\n{format_winrate()}")
    print(f"\n{format_history()}")
    print(f"\n=== DAILY RECAP ===\n{format_daily_recap()}")
