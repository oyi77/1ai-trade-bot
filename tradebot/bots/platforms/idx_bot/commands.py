"""
IDX Stock Bot — Telegram Command Handlers.

Slash commands with tier-gated output:
    /start — welcome + tier status
    /pricing — subscription plans
    /analisa <symbol> — unified analysis (tier-gated)
    /bandar <symbol> — smart money score (pro+)
    /anomali <symbol> — anomaly detection (premium+)
    /backtest <symbol> — backtest report (premium+)
    /screener — basic market overview (free)
    /peers <symbol> — peer comparison (pro+)

All output formatted as Telegram HTML with upgrade CTAs.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tradebot.bots.platforms.idx_bot.helpers import (
    format_bandar_output,
    format_fundamental_output,
    format_money,
    format_peer_output,
    format_percent,
    format_price,
    format_screener_output,
)
from tradebot.bots.platforms.idx_bot.tiers import (
    FEATURE_TIERS,
    TIER_INFO,
    TierGate,
    format_locked_feature,
    format_tier_badge,
)

LOG = logging.getLogger("tradebot.bots.idx_bot.commands")

# ── User tier storage (in-memory, replace with DB in production) ──
_user_tiers: dict[int, str] = {}


def get_user_tier(user_id: int) -> str:
    """Get user's subscription tier. Default: free."""
    return _user_tiers.get(user_id, "free")


def set_user_tier(user_id: int, tier: str) -> None:
    _user_tiers[user_id] = tier


# ── Command Handlers ────────────────────────────────────────────────


async def cmd_start(user_id: int) -> str:
    tier = get_user_tier(user_id)
    badge = format_tier_badge(tier)
    info = TIER_INFO.get(tier, TIER_INFO["free"])

    lines = [
        "📊 <b>IDX Stock AI — Trading Assistant</b>",
        f"Analisa saham Indonesia pakai AI + data real-time.",
        "",
        f"Status: {badge} <b>{info['name']}</b> ({info['price']})",
        "",
        "<b>🤖 Command:</b>",
        "• <code>/analisa BBCA</code> — Analisa fundamental + teknikal",
        "• <code>/screener</code> — Lihat market overview",
        "• <code>/pricing</code> — Lihat paket langganan",
    ]

    if TierGate.can_access(tier, "bandar_score"):
        lines.append("• <code>/bandar BBCA</code> — 🐳 Deteksi akumulasi bandar")

    if TierGate.can_access(tier, "anomaly_detection"):
        lines.append("• <code>/anomali BBCA</code> — 🚨 Deteksi anomali harga")

    if TierGate.can_access(tier, "backtest"):
        lines.append("• <code>/backtest BBCA</code> — 📊 Backtest akurasi sinyal")

    if TierGate.can_access(tier, "peer_comparison"):
        lines.append("• <code>/peers BBCA</code> — 👥 Bandingkan dengan sekotornya")

    lines.extend([
        "",
        "💡 <i>Contoh: /analisa BBCA TLKM BBRI</i>",
        "",
        f"⚡ Powered by AI + Yahoo Finance + IDX",
    ])

    return "\n".join(lines)


async def cmd_pricing() -> str:
    return (
        "💳 <b>Paket Langganan IDX Stock AI</b>\n\n"
        "🆓 <b>Free</b> — Rp0\n"
        "• Harga real-time + fundamental scoring\n"
        "• /analisa saham dengan AI\n"
        "• Market overview via /screener\n"
        "• 5 analisa per hari\n\n"
        "💎 <b>Pro</b> — Rp49.000/bulan\n"
        "• Semua fitur Free\n"
        "• 🐳 Bandar Accumulation Score\n"
        "• 👥 Peer comparison dalam 1 sektor\n"
        "• 📈 Sector average comparison\n"
        "• 🔍 Screener 958 saham IDX\n"
        "• Unlimited analisa\n\n"
        "👑 <b>Premium</b> — Rp149.000/bulan\n"
        "• Semua fitur Pro\n"
        "• 🚨 Anomaly detection real-time\n"
        "• 📊 Backtest akurasi sinyal (3 tahun)\n"
        "• 🔍 Sector anomaly scanner\n"
        "• ⚡ AI priority response\n"
        "• 📋 Auto-report harian\n\n"
        "🌟 <b>Lifetime</b> — Rp1.999.000 (sekali bayar)\n"
        "• Premium selamanya. Limited 1000 seat.\n\n"
        "💬 <i>Untuk upgrade, hubungi admin</i>"
    )


async def cmd_analisa(user_id: int, symbol: str) -> str:
    """Unified analysis — tier-gated output."""
    tier = get_user_tier(user_id)
    badge = format_tier_badge(tier)

    # Resolve symbol
    from tradebot.signals.idx_encyclopedia import (
        get_name,
        is_idx_stock,
        resolve_code,
    )

    code = resolve_code(symbol)
    if not is_idx_stock(code):
        return f"❌ <b>{symbol}</b> tidak ditemukan di database IDX.\nGunakan kode saham 4 huruf, contoh: /analisa BBCA"

    try:
        from tradebot.signals.idx_unified import unified_analysis

        result = await unified_analysis(code)
        if not result:
            return f"⚠️ Gagal menganalisa {code}. Coba lagi nanti."
    except Exception as exc:
        LOG.error("Unified analysis failed for %s: %s", code, exc)
        return f"⚠️ Gagal menganalisa {code}. Coba lagi nanti."

    # Build output with tier gating
    lines = [
        f"📊 <b>{code} — {get_name(code)}</b>",
        f"`{result.sector}` / `{result.sub_sector}` | {badge} {get_user_tier(user_id).title()}",
        "",
    ]

    # Always show: price + fundamentals
    if result.price > 0:
        lines.append(format_fundamental_output(result))

    # Pro features
    if TierGate.can_access(tier, "peer_comparison") and result.peers:
        lines.append(format_peer_output(result))
    elif result.peers:
        lines.append("")
        lines.append(format_locked_feature("Peer Comparison", "pro"))

    if TierGate.can_access(tier, "bandar_score") and result.bandar_score > 0:
        lines.append(format_bandar_output(result))
    elif result.bandar_score > 0:
        lines.append("")
        lines.append(format_locked_feature("Bandar Accumulation Score", "pro"))

    # Premium features
    if TierGate.can_access(tier, "anomaly_detection") and result.anomaly_type != "none":
        lines.append(_format_anomaly_output(result))
    elif result.anomaly_type != "none":
        lines.append("")
        lines.append(format_locked_feature("Anomaly Detection", "premium"))

    # Upgrade CTA for free users
    if tier == "free":
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "💎 <b>Upgrade ke Pro (Rp49k/bln)</b>",
            "→ /pricing",
        ])
    elif tier == "pro":
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "👑 <b>Upgrade ke Premium (Rp149k/bln)</b>",
            "→ /pricing",
        ])

    return "\n".join(lines)


async def cmd_bandar(user_id: int, symbol: str) -> str:
    """Bandar accumulation score — PRO feature."""
    tier = get_user_tier(user_id)
    check = TierGate.check(tier, "bandar_score")
    if not check.allowed:
        return _locked_response("🐳 Bandar Accumulation Score", check)

    from tradebot.signals.idx_encyclopedia import get_name, is_idx_stock, resolve_code

    code = resolve_code(symbol)
    if not is_idx_stock(code):
        return f"❌ {symbol} tidak ditemukan."

    try:
        from tradebot.signals.idx_smart_money import SIGNAL_LABELS, SmartMoneyEngine

        engine = SmartMoneyEngine()
        r = await engine.analyze(code)
    except Exception as exc:
        LOG.error("Bandar failed for %s: %s", code, exc)
        return f"⚠️ Gagal menganalisa {code}."

    if not r:
        return f"⚠️ Tidak ada data untuk {code}."

    emoji_map = {
        "🐳 Strong Accumulation": "🐳",
        "📈 Moderate Accumulation": "📈",
        "➡️  Neutral": "➡️",
        "📉 Moderate Distribution": "📉",
        "🚨 Strong Distribution": "🚨",
    }
    main_emoji = emoji_map.get(r.interpretation, "📊")

    lines = [
        f"{main_emoji} <b>Bandar Score — {code} ({get_name(code)})</b>",
        "",
        f"Skor: <b>{r.bandar_score}/100</b> — {r.interpretation}",
        f"Harga: {format_price(r.latest_price)}",
        f"Volume: {format_money(r.latest_volume)} (avg: {format_money(r.avg_volume)})",
        "",
        "<b>Metrik:</b>",
        f"• Volume Surge: {r.volume_surge_ratio:.0f}/100",
        f"• Close Location: {r.close_location:.0f}/100",
        f"• V-P Trend: {r.volume_price_trend:.0f}/100",
        f"• Momentum 7D: {r.momentum_7d:.0f}/100",
        f"• Ease of Movement: {r.ease_of_movement:.0f}/100",
        "",
        "<b>Sinyal Terdeteksi:</b>",
    ]

    for sig in r.signals:
        label = SIGNAL_LABELS.get(sig, sig)
        lines.append(f"• {label}")

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "💡 <i>Bandar Score &gt;60 = akumulasi terdeteksi</i>",
        "💡 <i>Bandar Score &lt;40 = distribusi terdeteksi</i>",
    ])

    return "\n".join(lines)


async def cmd_anomali(user_id: int, symbol: str) -> str:
    """Anomaly detection — PREMIUM feature."""
    tier = get_user_tier(user_id)
    check = TierGate.check(tier, "anomaly_detection")
    if not check.allowed:
        return _locked_response("🚨 Anomaly Detection", check)

    from tradebot.signals.idx_encyclopedia import get_name, is_idx_stock, resolve_code

    code = resolve_code(symbol)
    if not is_idx_stock(code):
        return f"❌ {symbol} tidak ditemukan."

    try:
        from tradebot.signals.idx_anomaly import ANOMALY_SIGNAL_LABELS, AnomalyEngine

        engine = AnomalyEngine()
        r = await engine.analyze(code)
    except Exception as exc:
        LOG.error("Anomaly failed for %s: %s", code, exc)
        return f"⚠️ Gagal menganalisa {code}."

    if not r:
        return f"⚠️ Tidak ada data untuk {code}."

    label = ANOMALY_SIGNAL_LABELS.get(r.anomaly_type, r.anomaly_type)

    lines = [
        f"🚨 <b>Anomaly Detection — {code} ({get_name(code)})</b>",
        "",
        f"Status: <b>{label}</b>",
        f"Score: {r.anomaly_score:.0%}",
    ]

    if r.signal != "HOLD":
        signal_emoji = "🟢" if r.signal == "BUY" else "🔴"
        lines.append(f"Sinyal: {signal_emoji} <b>{r.signal}</b>")

    lines.extend([
        f"Harga: {format_price(r.latest_price)}",
        f"Return Harian: {format_percent(r.daily_return)}",
        f"Volume Surge: {r.volume_surge:.1f}x normal",
        "",
        "<b>Detail:</b>",
    ])

    for d in r.details:
        lines.append(f"• {d}")

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "💡 <i>Anomali bullish = potensi akumulasi sebelum breakout</i>",
        "💡 <i>Anomali bearish = potensi distribusi sebelum penurunan</i>",
    ])

    return "\n".join(lines)


async def cmd_backtest(user_id: int, symbol: str) -> str:
    """Backtest report — PREMIUM feature."""
    tier = get_user_tier(user_id)
    check = TierGate.check(tier, "backtest")
    if not check.allowed:
        return _locked_response("📊 Backtest Report", check)

    from tradebot.signals.idx_encyclopedia import get_name, is_idx_stock, resolve_code

    code = resolve_code(symbol)
    if not is_idx_stock(code):
        return f"❌ {symbol} tidak ditemukan."

    try:
        from tradebot.signals.idx_backtest import BacktestEngine

        engine = BacktestEngine(years=3)
        r = await engine.validate(code)
    except Exception as exc:
        LOG.error("Backtest failed for %s: %s", code, exc)
        return f"⚠️ Gagal backtest {code}."

    if not r or r.total_signals == 0:
        return f"⚠️ Data tidak cukup untuk backtest {code} (minimal 60 hari)."

    lines = [
        f"📊 <b>Backtest — {code} ({get_name(code)})</b>",
        f"Periode: {r.years_analyzed:.1f} tahun | Data: {r.data_points} hari",
        f"Harga: {format_price(r.latest_price)}",
        "",
        "<b>📈 Performa Strategi:</b>",
        f"• Total Sinyal: <b>{r.total_signals}</b>",
        f"• Win Rate: <b>{r.win_rate:.1%}</b>",
        f"• Avg Return: {format_percent(r.avg_return)}",
        f"• Max Return: {format_percent(r.max_return)}",
        f"• Min Return: {format_percent(r.min_return)}",
        "",
        "<b>⏱ Akurasi per Horizon:</b>",
        f"• H+1: <b>{r.h1_accuracy:.1%}</b>",
        f"• H+3: <b>{r.h3_accuracy:.1%}</b>",
        f"• H+5: <b>{r.h5_accuracy:.1%}</b>",
        "",
        "<b>⚠️ Risk Metrics:</b>",
        f"• Sharpe Ratio: {r.sharpe_ratio:.2f}",
        f"• Max Drawdown: {r.max_drawdown:.1f}%",
        f"• Profit Factor: {r.profit_factor:.2f}",
        "",
        "<b>📊 vs Buy & Hold:</b>",
        f"• Buy & Hold: {format_percent(r.buy_hold_return)}",
        f"• Strategy: {format_percent(r.strategy_return)}",
        f"• Alpha: {format_percent(r.alpha)}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Backtest tidak menjamin hasil di masa depan</i>",
    ]

    return "\n".join(lines)


async def cmd_peers(user_id: int, symbol: str) -> str:
    """Peer comparison — PRO feature."""
    tier = get_user_tier(user_id)
    check = TierGate.check(tier, "peer_comparison")
    if not check.allowed:
        return _locked_response("👥 Peer Comparison", check)

    from tradebot.signals.idx_encyclopedia import (
        get_name,
        get_peers,
        get_sub_sector,
        is_idx_stock,
        resolve_code,
    )

    code = resolve_code(symbol)
    if not is_idx_stock(code):
        return f"❌ {symbol} tidak ditemukan."

    name = get_name(code)
    sub = get_sub_sector(code)
    peers = get_peers(code)

    if not peers:
        return f"👥 <b>{code} ({name})</b>\n\nTidak ada peer dalam sektor `{sub}`."

    lines = [
        f"👥 <b>Peer Comparison — {code} ({name})</b>",
        f"Sektor: `{sub}` | {len(peers)} saham",
        "",
        "<b>Saham dalam sektor yang sama:</b>",
    ]

    for i, peer in enumerate(peers[:10], 1):
        peer_name = get_name(peer)
        lines.append(f"{i}. <code>{peer}</code> — {peer_name}")

    lines.extend([
        "",
        "💡 <i>Gunakan /analisa &lt;kode&gt; untuk analisa masing-masing saham</i>",
    ])

    return "\n".join(lines)


async def cmd_screener(user_id: int) -> str:
    """Market overview — free for all."""
    tier = get_user_tier(user_id)
    badge = format_tier_badge(tier)

    # Quick overview of major stocks using the enricher
    from tradebot.signals.idx_encyclopedia import get_name

    majors = ["BBCA", "BBRI", "TLKM", "ASII", "ADRO", "UNVR", "ICBP"]

    lines = [
        f"📊 <b>Market Overview — IDX</b>",
        f"Status: {badge} {get_user_tier(user_id).title()}",
        "",
    ]

    for code in majors:
        try:
            from tradebot.signals.idx_enricher import enrich

            r = await enrich(code)
            if r and r.price > 0:
                chg_emoji = "🟢" if r.per > 0 else "⚪"
                lines.append(
                    f"<code>{code}</code> {format_price(r.price)} | "
                    f"PER {r.per:.1f}x | Score {r.fundamental_score}"
                )
        except Exception:
            lines.append(f"<code>{code}</code> ⏳ loading...")

    lines.extend([
        "",
        "💡 <i>/analisa BBCA untuk analisa detail</i>",
    ])

    if tier == "free":
        lines.extend([
            "",
            "🔒 <b>Pro features locked:</b>",
            "• Screener 958 saham IDX",
            "• Filter by sector, PER, PBV, ROE",
            "→ /pricing",
        ])

    return "\n".join(lines)


# ── Helpers ─────────────────────────────────────────────────────────


def _locked_response(feature_name: str, check) -> str:
    """Format locked feature response."""
    from tradebot.bots.platforms.idx_bot.tiers import format_tier_badge

    return (
        f"{check.message}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: {format_tier_badge(check.user_tier)} {check.user_tier.title()}\n"
        f"Fitur <b>{feature_name}</b> butuh: "
        f"{format_tier_badge(check.required_tier)} {check.required_tier.title()}"
    )


def _format_anomaly_output(result) -> str:
    from tradebot.signals.idx_anomaly import ANOMALY_SIGNAL_LABELS

    label = ANOMALY_SIGNAL_LABELS.get(result.anomaly_type, result.anomaly_type)
    lines = ["", f"🚨 <b>Anomaly:</b> {label}"]
    for d in result.anomaly_details[:2]:
        lines.append(f"   • {d}")
    return "\n".join(lines)
