"""AnalysisHandlersMixin — AI analysis, mechanical signals, auto-loop."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any

from tradebot.bots.base import BaseBot
from tradebot.bots.platforms.vilona.helpers import (
    killzone,
    news_blackout_status,
    resolve_yahoo_symbol,
    session,
    wib_fmt,
    wib_now,
)

LOG = logging.getLogger("tradebot.bots.vilona.analysis")


class AnalysisHandlersMixin(BaseBot):
    """Mixin providing AI analysis and auto-scanning methods for VilonaBot."""

    async def _auto_analysis_loop(self) -> None:
        from tradebot.bots.platforms.vilona.helpers import (
            SUPPORTED_PAIRS,
            format_signal_basic,
        )

        LOG.info("Auto-analysis loop started")
        while self._running:
            try:
                for pair in SUPPORTED_PAIRS[:5]:
                    if not self._running:
                        break
                    sig, reason = self._detect_mechanical_signal(pair)
                    if reason and reason.startswith("⏳"):
                        LOG.info("Auto-analysis skipped: %s", reason)
                    elif sig and sig.get("action") != "HOLD":
                        display = pair.upper()
                        last_posted = self._posted_signals.get(pair, 0)
                        if time.time() - last_posted < 5400:
                            LOG.info(
                                "Auto signal SKIPPED (dedup): %s %s (last: %ds ago)",
                                display,
                                sig["action"],
                                int(time.time() - last_posted),
                            )
                            continue

                        # Daily circuit breaker: max 5 mechanical signals/day per pair
                        today_key = datetime.now().strftime("%Y-%m-%d")
                        daily_key = f"{pair}:{today_key}"
                        daily_count = getattr(self, "_signal_daily_count", {})
                        if daily_count.get(daily_key, 0) >= 5:
                            LOG.info(
                                "Auto signal SKIPPED (daily limit): %s — %d/5 today",
                                display,
                                daily_count.get(daily_key, 0),
                            )
                            continue
                        if not hasattr(self, "_signal_daily_count"):
                            self._signal_daily_count = {}
                        self._signal_daily_count[daily_key] = self._signal_daily_count.get(daily_key, 0) + 1

                        self._posted_signals[pair] = time.time()
                        price = sig.get("entry", 0)
                        msg = format_signal_basic(sig, price, display)
                        await self._tg_send(msg)
                        from tradebot.bots.platforms.vilona.helpers import post_signal_to_bridge

                        post_signal_to_bridge(sig, price)
                        await self._broadcast_signal(sig, display, price)
                        LOG.info("Auto signal: %s %s | %s", display, sig["action"], reason)
                    await asyncio.sleep(2)
            except Exception as e:
                LOG.error("Auto-analysis error: %s", e)
            await asyncio.sleep(self._scan_interval_sec)

    async def _broadcast_signal(self, sig: dict[str, Any], display: str, price: float) -> None:
        """Broadcast signal to subscribers — throttled, deduped, Telegram-safe."""
        try:
            from tradebot.signals.subscriptions import get_all_active_subscribers

            subscribers = get_all_active_subscribers()
            if not subscribers:
                return

            # Deduplicate — one user can sub to multiple categories
            all_uids: set[str] = set()
            for uids in subscribers.values():
                all_uids.update(uids)
            if not all_uids:
                return

            action = sig.get("action", "HOLD")
            entry = sig.get("entry", price or 0)
            sl = sig.get("sl", 0)
            tp = sig.get("tp", 0)
            confidence = sig.get("confidence", 0)
            grade = sig.get("grade", "?")
            rr = sig.get("rr_ratio", sig.get("rr", 0))

            icon = "🟢" if action == "BUY" else "🔴"
            sl_pips = abs(entry - sl) if sl else 0
            tp_pips = abs(tp - entry) if tp else 0

            msg = (
                f"{icon} <b>SINYAL {action} — {display}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📍 <b>ENTRY:</b> ${entry:.2f}\n"
                f"🔴 <b>SL:</b> ${sl:.2f} ({sl_pips:.0f} pip)\n"
                f"🟢 <b>TP:</b> ${tp:.2f} ({tp_pips:.0f} pip)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Grade {grade}\n"
                f"📐 RR 1:{rr:.1f} | <b>Confidence:</b> {confidence:.0%}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ NFA — Not Financial Advice"
            )

            sent = 0
            for uid in all_uids:
                try:
                    await self._tg_send(msg, chat_id=uid)
                    sent += 1
                except Exception:
                    LOG.debug("Broadcast failed to %s", uid)
                # Telegram limit: ~20 msgs/min to same chat — 3s per send is safe
                await asyncio.sleep(3.0)

            LOG.info("Signal broadcast to %d unique subscribers for %s", sent, display)
        except Exception as e:
            LOG.warning("Broadcast failed: %s", e)

    def _detect_mechanical_signal(
        self,
        pair: str = "gold",
        price: float | None = None,
        ohlcv_bars: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        symbol = resolve_yahoo_symbol(pair)
        display = pair.upper()

        is_forex_metal = display in ("XAUUSD", "USOIL")
        if is_forex_metal:
            lkz, nykz = killzone()
            if not (lkz or nykz):
                return None, f"⏳ {display} mechanical signal SKIPPED — outside London/NY killzone"

        if not self._market_data:
            return None, None
        if not ohlcv_bars:
            try:
                ohlcv_bars = self._market_data.get_bars_dicts(symbol, "15m", 80)
            except Exception:
                return None, None
        if not ohlcv_bars or len(ohlcv_bars) < 15:
            return None, None
        if not price:
            price = float(ohlcv_bars[-1].get("close", ohlcv_bars[-1].get("open", 0)))

        # ── Priority 1: S-TIER Zone (mechanical, highest conviction) ──
        try:
            from scripts.vilona_tradefx_handler import detect_stier_zone

            stier_sig, stier_reason = detect_stier_zone(
                display, display, price, ohlcv_bars
            )
            if stier_sig and stier_sig.get("action") in ("BUY", "SELL"):
                LOG.info(
                    "🎯 Auto S-TIER [%s]: %s @ %.2f | Grade=%s",
                    display,
                    stier_sig["action"],
                    stier_sig.get("entry", 0),
                    stier_sig.get("grade", "?"),
                )
                return stier_sig, stier_reason
        except Exception as e:
            LOG.debug("S-TIER mechanical failed for %s: %s", display, e)

        # ── Priority 2: Quant + FVG (mechanical, medium conviction) ──
        quant_result = None
        fvg_signals = []

        if self._engines.get("quant"):
            try:
                from quant_engine import analyze_quantitative_pattern

                qdata = [
                    {
                        "timestamp": b.get("timestamp", 0),
                        "open": float(b["open"]),
                        "high": float(b["high"]),
                        "low": float(b["low"]),
                        "close": float(b["close"]),
                        "volume": float(b.get("volume", 0)),
                    }
                    for b in ohlcv_bars
                ]
                quant_result = analyze_quantitative_pattern(qdata, pattern_size=5)
            except Exception as e:
                LOG.debug("Quantitative pattern analysis failed: %s", e)

        if self._engines.get("fvg"):
            try:
                from fvg_detector import detect_fvg

                fvg_signals = detect_fvg(ohlcv_bars, "M1")
            except Exception as e:
                LOG.debug("FVG detection failed: %s", e)

        quant_bias = None
        if quant_result and quant_result.get("match_count", 0) >= 20:
            dom = quant_result.get("dominant_next")
            g = quant_result.get("green_pct", 0)
            r = quant_result.get("red_pct", 0)
            if dom == "G" and g >= 50:
                quant_bias = "BUY"
            elif dom == "R" and r >= 50:
                quant_bias = "SELL"

        fvg_bias = None
        fvg_sig_obj = None
        if fvg_signals:
            fvg_sig_obj = fvg_signals[0]
            if hasattr(fvg_sig_obj, "confidence") and fvg_sig_obj.confidence >= 0.35:
                fvg_bias = fvg_sig_obj.direction

        if quant_bias and fvg_bias and fvg_bias == quant_bias and fvg_sig_obj:
            confidence = round((quant_result["confidence_score"] + fvg_sig_obj.confidence) / 2, 2)
            reasoning = (
                f"🤖 MECHANICAL SIGNAL | Quant {quant_bias} "
                f"({quant_result['green_pct']:.0f}%G/{quant_result['red_pct']:.0f}%R) "
                f"+ FVG {fvg_sig_obj.direction} ({fvg_sig_obj.fvg_zone.size_pips:.0f}pip)"
            )
            return {
                "action": quant_bias,
                "confidence": confidence,
                "entry": price,
                "sl": price - (price * 0.015) if quant_bias == "BUY" else price + (price * 0.015),
                "tp": price + (price * 0.025) if quant_bias == "BUY" else price - (price * 0.025),
                "reasoning": reasoning,
                "grade": "A",
                "_model": "quant+fvg",
            }, reasoning

        return None, None

    async def _ai_analyze(self, pair: str = "gold") -> tuple[dict[str, Any] | None, str | None]:
        symbol = resolve_yahoo_symbol(pair)
        display = pair.upper()

        if not self._market_data:
            return None, "Market data unavailable"
        try:
            ohlcv_bars = self._market_data.get_bars_dicts(symbol, "15m", 80)
        except Exception as e:
            return None, f"Data fetch failed: {e}"
        if not ohlcv_bars or len(ohlcv_bars) < 15:
            return None, "Insufficient data"

        price = float(ohlcv_bars[-1].get("close", 0))
        if not price:
            return None, "Price unavailable"

        # Try mechanical fallback first
        mech_sig, mech_reason = self._detect_mechanical_signal(pair, price, ohlcv_bars)
        if mech_sig:
            return mech_sig, mech_reason

        # Fetch DXY
        from tradebot.services.briefing_service import _fetch_dxy

        dxy = _fetch_dxy()

        # Call ask_ai_ensemble
        sess = session(wib_now().hour)
        lkz, nykz = killzone()
        kz_str = "London" if lkz else ("NY" if nykz else "None")

        # Basic loss count tracker simulation
        loss_count = 0

        # Determine user tier
        tier = "starter"
        # Check from database
        try:
            from tradebot.services.members_service import get_member

            m = get_member(str(self.chat_id))
            if m:
                tier = m.get("tier", "starter")
        except Exception:
            pass

        sig = await self.ask_ai_ensemble(
            price=price,
            dxy=dxy,
            sess=sess,
            kz_str=kz_str,
            loss_count=loss_count,
            ohlcv_data=ohlcv_bars,
            display=display,
            tier=tier,
        )

        if not sig:
            return None, "AI Ensemble returned no signal"

        return sig, sig.get("reasoning", "")

    async def ask_ai_ensemble(
        self,
        price: float,
        dxy: float | None,
        sess: str,
        kz_str: str,
        loss_count: int,
        premium: bool = False,
        ohlcv_data: list | None = None,
        display: str = "XAUUSD",
        tier: str = "starter",
    ) -> dict[str, Any] | None:
        """Multi-AI consensus — tier-based model selection.

        starter:  DeepSeek only (solo, max 55% conf) — free tier
        pro:      DeepSeek + GPT-4o (dual, max 85% conf) — donor
        elite:    All 3 models + Grok News (max 95% conf) — premium subscriber
        """
        self._ai_token_usage = {}

        data_section = f"💰 Current Price: ${price:.2f}"
        if ohlcv_data:
            data_section += (
                f"\n📊 OHLCV (last {len(ohlcv_data)} bars): {json.dumps(ohlcv_data[-10:])}"
            )
        if dxy:
            data_section += f"\n💵 DXY: {dxy:.2f}"

        is_blackout, is_post_news, news_name = news_blackout_status()
        news_protocol = ""
        if is_post_news:
            news_protocol = f"\n\n⚡ POST-NEWS PROTOCOL: {news_name}\n🔴 MODE: LIQUIDITY HUNTER — counter-trend only\n"
        elif is_blackout:
            news_protocol = f"\n\n🚫 PRE-NEWS BLACKOUT: {news_name} — WAJIB HOLD.\n"

        prompt = (
            f"🕐 {wib_fmt()} | Session: {sess} | Killzone: {kz_str}\n"
            f"🔴 Circuit Breaker: Loss hari ini: {loss_count}/3\n"
            f"{news_protocol}\n{data_section}\n\n"
            f"Analisis {display} dengan SMC + SnR. Entry/SL/TP wajib dari data.\n"
            f"R:R minimum 1:2. {'⚠️ FRIDAY: SL +10-15 pips extra.' if wib_now().weekday() == 4 else ''}\n"
            f"⚡ XAUUSD 3-DIGIT: 1 pip = 0.10. SL 30 pip = 3.0 poin harga. TP 60 pip = 6.0 poin.\n"
            f"Contoh SELL entry=4334: SL=4337.00 (+3.0 poin = 30 pip) TP=4328.00 (−6.0 poin = 60 pip) untuk RR 1:2"
        )

        is_free_tier = tier == "starter" and not premium

        # 1. DeepSeek
        deepseek = await self._call_deepseek(prompt)

        # 2. GPT-4o
        gpt4o = None
        if not is_free_tier:
            gpt4o = await self._call_openai(prompt, model="gpt-4o")

        # 3. Grok News (Twitter Context)
        grok_news = None
        if not is_free_tier:
            grok_news = await self._call_grok_news(display, price)

        token_total = sum(v.get("total", 0) for v in self._ai_token_usage.values())
        token_prompt = sum(v.get("prompt", 0) for v in self._ai_token_usage.values())
        token_completion = sum(v.get("completion", 0) for v in self._ai_token_usage.values())

        signals = []
        if deepseek and deepseek.get("action") in ("BUY", "SELL"):
            signals.append({"sig": deepseek, "name": "DeepSeek-V3", "weight": 1.2})
        if gpt4o and gpt4o.get("action") in ("BUY", "SELL"):
            signals.append({"sig": gpt4o, "name": "GPT-4o", "weight": 1.0})

        model_count = len(signals)
        tier_label = {"starter": "🆓 Free", "pro": "⭐ Pro", "elite": "👑 Elite"}.get(
            tier, tier.upper()
        )

        conf_caps = {"starter": 0.55, "pro": 0.85, "elite": 0.95}
        conf_cap = conf_caps.get(tier, 0.95)
        if premium:
            conf_cap = 0.95

        buy_votes = [s for s in signals if s["sig"]["action"] == "BUY"]
        sell_votes = [s for s in signals if s["sig"]["action"] == "SELL"]

        # ── Mechanical validation helper ──
        def _mech_vet(sig_action: str, ohlcv: list | None, price_f: float) -> tuple[float | None, str | None]:
            """Returns (confidence_multiplier, warning) or (None, msg) to block."""
            if not ohlcv or len(ohlcv) < 30:
                return 1.0, None
            try:
                from scripts.vilona_tradefx_handler import detect_stier_zone
                v_sig, _ = detect_stier_zone(display, display, price_f, ohlcv)
                if v_sig and v_sig.get("action") in ("BUY", "SELL"):
                    v_grade = v_sig.get("grade", "B")
                    if v_grade in ("A", "S-TIER") and v_sig["action"] != sig_action:
                        LOG.warning("MECH BLOCK: S-TIER %s vs AI %s %s", v_sig["action"], sig_action, display)
                        return None, f"S-TIER {v_sig['action']} contradicts AI {sig_action}"
                    if v_grade in ("A", "S-TIER") and v_sig["action"] == sig_action:
                        LOG.info("MECH BOOST: S-TIER %s confirms AI %s %s", v_sig["action"], sig_action, display)
                        return 1.3, f"S-TIER {v_sig['action']} confirms (+30%)"
                if v_sig and v_sig.get("action") in ("BUY", "SELL") and v_sig["action"] != sig_action:
                    # Lower-grade contradiction — slash confidence
                    return 0.6, f"S-TIER {v_sig['action']} disagrees (-40%)"
            except Exception:
                pass
            return 1.0, None

        # ── Grok news cross-check ──
        def _news_vet(sig_action: str, grok: dict | None) -> tuple[float | None, str | None]:
            """Returns (confidence_multiplier, warning) or (None, msg) to block."""
            if not grok or not isinstance(grok, dict):
                return 1.0, None
            sentiment = grok.get("sentiment", "NEUTRAL")
            impact = grok.get("impact", "LOW")
            if impact == "HIGH":
                if (sig_action == "BUY" and sentiment == "BEARISH") or \
                   (sig_action == "SELL" and sentiment == "BULLISH"):
                    LOG.warning("NEWS BLOCK: HIGH %s news contradicts %s %s", sentiment, sig_action, display)
                    return None, f"HIGH impact {sentiment} news contradicts {sig_action}"
                if impact == "MEDIUM":
                    if (sig_action == "BUY" and sentiment == "BEARISH") or \
                       (sig_action == "SELL" and sentiment == "BULLISH"):
                        return 0.7, f"MEDIUM {sentiment} news reduces confidence (-30%)"
            return 1.0, None

        # Consensus
        if len(buy_votes) >= 2 or len(sell_votes) >= 2:
            winner = buy_votes if len(buy_votes) >= 2 else sell_votes
            conf = sum(s["sig"].get("confidence", 0) * s["weight"] for s in winner) / sum(
                s["weight"] for s in winner
            )
            sig = winner[0]["sig"].copy()
            sig["confidence"] = min(conf, conf_cap)
            sig["ensemble"] = "dual"
            sig["voters"] = len(winner)
            sig["_model"] = "+".join(s["name"] for s in winner)
            sig["_tier"] = tier_label
            sig["_tier_capped"] = is_free_tier
            sig["_models"] = f"{model_count}/2"
            sig["_token_total"] = token_total
            sig["_token_prompt"] = token_prompt
            sig["_token_completion"] = token_completion
            sig["_grok_news"] = grok_news

            # ── S-TIER + Grok cross-check for dual consensus ──
            vet_conf, vet_warn = _mech_vet(sig["action"], ohlcv_data, price)
            if vet_conf is None:
                sig["confidence"] = round(sig["confidence"] * 0.3, 3)
                sig["_warning"] = vet_warn or ""
                LOG.warning("DUAL overridden: AI %s vetoed by S-TIER %s", sig["action"], display)
            elif vet_conf != 1.0:
                sig["confidence"] = round(min(sig["confidence"] * vet_conf, conf_cap), 3)
                sig["_warning"] = vet_warn or ""

            news_conf, news_warn = _news_vet(sig["action"], grok_news)
            if news_conf is None:
                sig["confidence"] = round(sig["confidence"] * 0.3, 3)
                sig["_news_warning"] = news_warn or ""
            elif news_conf != 1.0:
                sig["confidence"] = round(sig["confidence"] * news_conf, 3)
                sig["_news_warning"] = news_warn or ""

            LOG.info(
                "AI CONSENSUS [%d/%d]: %s conf=%.0f%% tier=%s tokens=%d",
                len(winner),
                len(signals),
                sig["action"],
                sig["confidence"] * 100,
                tier,
                token_total,
            )
            return sig

        # Solo fallback
        if signals:
            best = max(signals, key=lambda s: s["sig"].get("confidence", 0) * s["weight"])
            sig = best["sig"].copy()
            sig["confidence"] = min(sig.get("confidence", 0), conf_cap)
            sig["ensemble"] = "solo"
            sig["voters"] = 1
            sig["_model"] = best["name"]
            sig["_tier"] = tier_label
            sig["_tier_capped"] = is_free_tier
            sig["_models"] = f"{model_count}/2"
            sig["_token_total"] = token_total
            sig["_token_prompt"] = token_prompt
            sig["_token_completion"] = token_completion
            sig["_grok_news"] = grok_news

            # ── Solo quality gate: mechanical MUST NOT strongly disagree ──
            vet_conf, vet_warn = _mech_vet(sig["action"], ohlcv_data, price)
            if vet_conf is None:
                LOG.warning("SOLO %s BLOCKED: S-TIER contradicts %s %s", best["name"], sig["action"], display)
                return None
            if vet_conf != 1.0:
                sig["confidence"] = round(min(sig["confidence"] * vet_conf, conf_cap), 3)
                sig["_warning"] = vet_warn or ""

            # ── Grok news: solo signals are blocked by HIGH contradictory news ──
            news_conf, news_warn = _news_vet(sig["action"], grok_news)
            if news_conf is None:
                LOG.warning("SOLO %s BLOCKED: HIGH impact %s news %s", best["name"],
                            grok_news.get("sentiment", "?") if grok_news else "?", display)
                return None
            if news_conf != 1.0:
                sig["confidence"] = round(sig["confidence"] * news_conf, 3)
                sig["_news_warning"] = news_warn or ""

            LOG.info(
                "SOLO [%s]: %s conf=%.0f%% tier=%s tokens=%d",
                best["name"],
                sig["action"],
                sig["confidence"] * 100,
                tier,
                token_total,
            )
            return sig

        # Hold fallback
        if deepseek:
            s = dict(deepseek)
            s["ensemble"] = "hold"
            s["voters"] = 0
            s["_model"] = "DeepSeek-V3"
            s["_tier"] = tier_label
            s["_tier_capped"] = is_free_tier
            s["_models"] = "0/2"
            s["_token_total"] = token_total
            s["_token_prompt"] = token_prompt
            s["_token_completion"] = token_completion
            s["_grok_news"] = grok_news
            return s

        return None

    def _build_system_prompt(self) -> str:
        return (
            "Kamu adalah Vilona Trade FX — Full-Stack Institutional AI Trading System.\n"
            "Senior Hedge Fund Portfolio Manager menganalisis market dengan data REAL.\n\n"
            "⚠️ CRITICAL RULE: Analisa HARUS berdasarkan DATA OHLCV yang diberikan dalam prompt.\n"
            "DILARANG mengarang harga, level, atau pola yang tidak ada di data.\n"
            "Jika data tidak tersedia → HOLD. Jika data tidak mendukung setup → HOLD.\n\n"
            "═══════════════════════════════════════════\n"
            "🛡️ CONSTITUTION (Non-Negotiable)\n"
            "═══════════════════════════════════════════\n"
            "LAW #1 — CIRCUIT BREAKER: loss_count >= 3 → WAJIB HOLD. TIDAK ADA pengecualian.\n"
            "LAW #2 — REALISTIC: Target 5-15%/bulan, bukan 100%.\n"
            "LAW #3 — COMPOUNDING > JACKPOT: $1,000 @ 10%/bln → 12 bln: $3,138 | 5 thn: $300K+\n"
            "LAW #4 — DUAL RISK TIER: SKC ≥ 8.7 → 1% risk | SKC 7.0-8.6 → 0.5% risk | SKC < 7.0 → SKIP\n"
            "LAW #5 — DON'T CHASE: Entry hanya setelah candle CLOSED dengan konfirmasi.\n"
            "LAW #6 — PIP CALCULATION: XAUUSD broker 3-digit → 1 pip = 0.10. USOIL 3-digit → 1 pip = 0.01. BTCUSD → 1 pip = 1.0. Forex → 1 pip = 0.00010 (5-digit) / 0.01 (JPY). entry/sl/tp = HARGA ABSOLUTE.\n"
            "LAW #7 — SL/TP RULES: SL 20-35 pip dari entry. TP = SL × RR (min 1:2). Contoh SELL entry=4334: SL=4337.00 (+3.0 poin = 30 pip) TP=4328.00 (−6.0 poin = 60 pip) untuk RR 1:2.\n\n"
            "═══════════════════════════════════════════\n"
            "🔬 SKC SCORING ENGINE (Max 10 pts)\n"
            "═══════════════════════════════════════════\n"
            "S — STRUKTUR (Max 4.0): W1/D1 aligned(+1.5) | H4 CHoCH/BOS(+1.5) | H1 POI(+0.5) | M15/M5(+0.5)\n"
            "K — KONFLUENSI (Max 3.5): Liq sweep(+1.0) | ≥3TF bias aligned(+1.0) | Killzone active(+0.75) | S/R round number(+0.75)\n"
            "C — KONTEKS (Max 2.5): Macro align(+1.0) | News align(+1.0) | Clean chart no chop(+0.5)\n\n"
            "OUTPUT: JSON only. No markdown, no text outside JSON.\n"
            "Return exactly this JSON structure:\n"
            "{\n"
            ' "action":"BUY|SELL|HOLD",\n'
            ' "entry":0.0, "sl":0.0, "tp":0.0,\n'
            ' "sl_pips":0, "tp_pips":0,\n'
            ' "rr_ratio":"1:X.XX",\n'
            ' "confidence":0.0, "grade":"A|B|C|D",\n'
            ' "combat_style":"SNIPER|COMMANDO|CRUSADER|LIQUIDITY_HUNTER|HOLD",\n'
            ' "bias":"BULLISH|BEARISH|NEUTRAL",\n'
            ' "skc_score":{"s_struktur":0.0,"k_konfluensi":0.0,"c_konteks":0.0,"total":0.0,"zone":"GREEN|YELLOW|RED"},\n'
            ' "risk_tier":"1%|0.5%|SKIP",\n'
            ' "layer_1":"TRIGGERED|WAITING|N/A",\n'
            ' "layer_2":"CONFIRMED|PENDING|FAILED",\n'
            ' "confluences":["factor1","factor2"],\n'
            ' "reasoning":"6-8 kalimat ANALISA LENGKAP..."\n'
            "}"
        )

    def _extract_json(self, content: str) -> dict[str, Any] | None:
        content = re.sub(r"```[a-z]*\s*", "", content)
        start = content.find("{")
        if start < 0:
            return None
        depth = 0
        end = start
        for i, ch in enumerate(content[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        json_str = content[start:end]
        json_str = re.sub(r"[\x00-\x1f]+", " ", json_str)
        try:
            return json.loads(json_str, strict=False)
        except Exception:
            return None

    async def _call_deepseek(self, prompt: str) -> dict[str, Any] | None:
        key = self.deepseek_key
        if not key:
            return None
        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/v1/chat/completions",
                data=json.dumps(
                    {
                        "model": "deepseek-chat",
                        "max_tokens": 800,
                        "temperature": 0.3,
                        "messages": [
                            {"role": "system", "content": self._build_system_prompt()},
                            {"role": "user", "content": prompt},
                        ],
                    }
                ).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read())
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                self._ai_token_usage["deepseek"] = {
                    "prompt": usage.get("prompt_tokens", 0),
                    "completion": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                }
                return self._extract_json(content)
        except Exception as e:
            LOG.warning("DeepSeek error: %s", e)
            return None

    async def _call_openai(self, prompt: str, model: str = "gpt-4o") -> dict[str, Any] | None:
        key = self.openai_key
        if not key:
            return None
        try:
            messages = [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": prompt},
            ]
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(
                    {"model": model, "max_tokens": 800, "temperature": 0.3, "messages": messages}
                ).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read())
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                self._ai_token_usage["openai"] = {
                    "prompt": usage.get("prompt_tokens", 0),
                    "completion": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                }
                return self._extract_json(content)
        except Exception as e:
            LOG.warning("OpenAI error: %s", e)
            return None

    async def _call_grok_news(self, display: str, price: float) -> dict[str, Any] | None:
        key = self.grok_api_key
        if not key:
            return None
        try:
            news_prompt = (
                f"Search X/Twitter for the LATEST breaking news, macro events, or market-moving "
                f"headlines about {display} (currently ${price:.2f}). "
                f"Focus on: FOMC/Fed speakers, NFP/CPI/economic data, geopolitical events, "
                f"major institutional moves, or sentiment shifts in the last 2 hours.\n\n"
                f"Return ONLY a structured JSON with these fields:\n"
                f'{{"headline": "1 most impactful headline", '
                f'"sentiment": "BULLISH/BEARISH/NEUTRAL", '
                f'"impact": "HIGH/MED/LOW", '
                f'"detail": "2-3 sentence context explaining WHY this matters for {display}"}}\n\n'
                f"Be CONCISE. Max 150 words total. If no significant news, headline='No major catalysts'."
            )
            req = urllib.request.Request(
                self.grok_url,
                data=json.dumps(
                    {
                        "model": "grok-2-latest",
                        "max_tokens": 300,
                        "temperature": 0.3,
                        "messages": [{"role": "user", "content": news_prompt}],
                    }
                ).encode(),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=25) as r:
                data = json.loads(r.read())
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                self._ai_token_usage["grok"] = {
                    "prompt": usage.get("prompt_tokens", 0),
                    "completion": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                }
                news = self._extract_json(content)
                if news and isinstance(news, dict):
                    return news
                return {
                    "headline": content[:200],
                    "sentiment": "NEUTRAL",
                    "impact": "LOW",
                    "detail": "",
                }
        except Exception as e:
            LOG.warning("Grok News error: %s", e)
            return None
