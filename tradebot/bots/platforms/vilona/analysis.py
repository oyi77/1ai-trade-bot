"""AnalysisHandlersMixin — AI analysis, mechanical signals, auto-loop."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from random import choice

from tradebot.bots.base import BaseBot

LOG = logging.getLogger("tradebot.bots.vilona.analysis")

FOMO_PHRASES = [
    "🔥 Sinyal ini cuma untuk yang FAST RESPONSE!",
    "⚡ 9 engines udah konsensus — tinggal kamu yang belum action!",
    "💰 Orang lain udah cuan, kamu masih tunggu apa?",
    "🎯 Setiap detik delay = profit yang hilang!",
    "🚀 Ini bukan latihan. Ini real signal.",
    "💎 DIAMOND ALERT — Jangan sampai kelewatan!",
    "⚡ Signal premium detected! Upgrade buat akses FULL analysis!",
    "🔥 90% orang yang subscribe cuan tiap hari. Kamu kapan?",
    "💰 Udah 15 member lain yg eksekusi signal ini. Lo ketinggalan!",
    "🎯 Signal akurasi tinggi — cuma buat subscriber PREMIUM.",
]


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
                        if time.time() - last_posted < 10800:
                            LOG.info("Auto signal SKIPPED (dedup): %s %s (last: %ds ago)",
                                     display, sig["action"], int(time.time() - last_posted))
                            continue
                        self._posted_signals[pair] = time.time()
                        price = sig.get("entry", 0)
                        msg = format_signal_basic(sig, price, display)
                        await self._tg_send(msg)
                        self.bridge.post_signal(sig, price)
                        await self._broadcast_signal(sig, display, price)
                        LOG.info("Auto signal: %s %s | %s", display, sig["action"], reason)
                    await asyncio.sleep(2)
            except Exception as e:
                LOG.error("Auto-analysis error: %s", e)
            await asyncio.sleep(self._scan_interval_sec)

    async def _broadcast_signal(self, sig: dict[str, Any], display: str, price: float) -> None:
        """Broadcast signal to subscribers with proven FOMO format from @vilonaaichanel."""
        try:
            from tradebot.signals.subscriptions import get_all_active_subscribers

            subscribers = get_all_active_subscribers()
            if not subscribers:
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
                f"📍 <b>BUY ZONE:</b> ${entry:.2f}\n"
                f"🔴 <b>SL:</b> ${sl:.2f} ({sl_pips:.0f} pip)\n"
                f"🟢 <b>TP:</b> ${tp:.2f} ({tp_pips:.0f} pip)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Grade {grade}\n"
                f"📐 RR 1:{rr:.1f} | <b>Confidence:</b> {confidence:.0%}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Quality Gate PASS\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ Mau signal REAL-TIME langsung ke HP?\n"
                f"   /subscribe all — Subscribe sekarang!\n\n"
                f"💚 Atau dukung server AI biar makin akurat:\n"
                f"   /donate — Isi Bahan Bakar AI\n\n"
                f"⚠️ NFA — Not Financial Advice"
            )

            sent = 0
            for category, user_ids in subscribers.items():
                for uid in user_ids:
                    try:
                        await self._tg_send(msg, chat_id=uid)
                        sent += 1
                    except Exception:
                        LOG.debug("Broadcast failed to %s", uid)
                    await asyncio.sleep(0.05)

            LOG.info("Signal broadcast to %d subscribers for %s", sent, display)
        except Exception as e:
            LOG.warning("Broadcast failed: %s", e)

    def _detect_mechanical_signal(
        self,
        pair: str = "gold",
        price: float | None = None,
        ohlcv_bars: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        from tradebot.bots.platforms.vilona.helpers import (
            killzone_active,
            resolve_yahoo_symbol,
        )

        symbol = resolve_yahoo_symbol(pair)
        display = pair.upper()

        is_forex_metal = display in ("XAUUSD", "USOIL")
        if is_forex_metal:
            lkz, nykz = killzone_active()
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

        quant_result = None
        fvg_signals = []

        if self._engines.get("quant"):
            try:
                from quant_engine import analyze_quantitative_pattern
                qdata = [
                    {"timestamp": b.get("timestamp", 0),
                     "open": float(b["open"]), "high": float(b["high"]),
                     "low": float(b["low"]), "close": float(b["close"]),
                     "volume": float(b.get("volume", 0))}
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
        if quant_result and quant_result.get("match_count", 0) >= 15:
            dom = quant_result.get("dominant_next")
            g = quant_result.get("green_pct", 0)
            r = quant_result.get("red_pct", 0)
            if dom == "G" and g >= 40:
                quant_bias = "BUY"
            elif dom == "R" and r >= 40:
                quant_bias = "SELL"

        fvg_bias = None
        fvg_sig_obj = None
        if fvg_signals:
            fvg_sig_obj = fvg_signals[0]
            if hasattr(fvg_sig_obj, "confidence") and fvg_sig_obj.confidence >= 0.20:
                fvg_bias = fvg_sig_obj.direction

        if quant_bias and fvg_bias and fvg_bias == quant_bias and fvg_sig_obj:
            confidence = round(
                (quant_result["confidence_score"] + fvg_sig_obj.confidence) / 2, 2
            )
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

    async def _ai_analyze(
        self, pair: str = "gold"
    ) -> tuple[dict[str, Any] | None, str | None]:
        from tradebot.bots.platforms.vilona.helpers import (
            killzone_active,
            news_blackout_status,
            resolve_yahoo_symbol,
            session_label,
            wib_now,
        )

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

        mech_sig, mech_reason = self._detect_mechanical_signal(pair, price, ohlcv_bars)
        if mech_sig:
            return mech_sig, mech_reason

        now = wib_now()
        ses = session_label()
        lkz, nykz = killzone_active()
        bn, pn, nn = news_blackout_status()

        user_prompt = (
            f"Analyze {display} ({symbol}) technical setup.\n"
            f"Time: {now.strftime('%Y-%m-%d %H:%M WIB')} | Session: {ses}\n"
            f"Killzone: {'London' if lkz else ''} {'NY' if nykz else ''} | Price: ${price:.2f}\n"
            f"News: {nn or 'none'}\n"
            f"OHLCV bars (last 80 × 15m):\n" + "\n".join(
                f"{b['timestamp']} O:{b['open']:.2f} H:{b['high']:.2f} "
                f"L:{b['low']:.2f} C:{b['close']:.2f} V:{b.get('volume', 0)}"
                for b in ohlcv_bars[-30:]
            )
        )
        return await self._call_ai(user_prompt, display)

    async def _call_tier1_gemini(self, user_prompt: str) -> dict[str, Any] | None:
        from tradebot.bots.platforms.vilona.helpers import extract_json

        system_prompt = self._build_system_prompt()
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        LOG.info("Tier 1 (Gemini) analyzing...")
        response = await self._call_gemini_native(full_prompt)
        if not response:
            LOG.warning("Tier 1 Gemini returned no response")
            return None

        result = extract_json(response)
        if not result:
            LOG.warning("Tier 1 Gemini response not parseable: %.200s", response)
            return None
        return result

    async def _call_gemini_native(self, prompt: str) -> str | None:
        import urllib.request

        api_key = self.gemini_key
        if not api_key:
            LOG.warning("GEMINI_API_KEY not set")
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"  # noqa: E501
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048},
        }).encode()

        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
            candidates = data.get("candidates", [])
            if candidates:
                return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        except Exception as e:
            LOG.error("Gemini native call failed: %s", e)
        return None

    async def _call_ai_with_model(self, model: str, user_content: str) -> str:
        import urllib.request

        api_keys = {
            "deepseek": self.deepseek_key,
            "openai": self.openai_key,
            "claude": self.claude_key,
        }
        api_key = api_keys.get(model, self.deepseek_key)
        if not api_key:
            return ""

        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
        }).encode()

        url_map = {
            "deepseek": "https://api.deepseek.com/chat/completions",
            "openai": "https://api.openai.com/v1/chat/completions",
            "claude": "https://api.anthropic.com/v1/messages",
        }
        url = url_map.get(model, "https://api.deepseek.com/chat/completions")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read())
            if model == "claude":
                return result.get("content", [{}])[0].get("text", "")
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            LOG.error("AI call (%s) failed: %s", model, e)
            return ""

    def _build_system_prompt(self) -> str:
        return (
            "You are a precise XAUUSD (Gold) futures technical analyst. "
            "Use ICT/SMC concepts: fair value gaps, order blocks, market structure. "
            "Return ONLY valid JSON with action, confidence (0-100), entry, sl, tp, reasoning. "
            "If the setup is unclear, be honest and return HOLD with low confidence."
        )

    async def _call_ai(self, user_prompt: str, display: str) -> tuple[dict[str, Any] | None, str | None]:
        from tradebot.bots.platforms.vilona.helpers import extract_json

        tier1 = await self._call_tier1_gemini(user_prompt)
        if not tier1:
            return None, "AI analysis unavailable"

        tier1_conf = tier1.get("confidence", 0)
        if tier1_conf < 75:
            LOG.info("Tier 1 confidence %.0f%% < 75%% — pipeline halted", tier1_conf)
            return {"action": "HOLD", "confidence": tier1_conf, "grade": "C",
                    "reasoning": f"Gemini confidence {tier1_conf:.0f}% below threshold"}, None

        try:
            sniper_prompt = (
                f"Cross-check this {display} analysis. "
                f"Tier 1 (Gemini) says: {json.dumps(tier1)}. "
                f"Verify or override. Return valid JSON."
            )
            sniper_raw = await self._call_ai_with_model("deepseek-chat", sniper_prompt)
            sniper = extract_json(sniper_raw) if sniper_raw else None
            if sniper and sniper.get("action") in ("BUY", "SELL"):
                sig = {**tier1, **sniper}
                sig["source"] = "gemini→deepseek-sniper"
                sig["gemini_confidence"] = tier1_conf
                LOG.info("Sniper confirmed: %s @ %.0f%%", sig["action"], sig.get("confidence", 0))
                return sig, sig.get("reasoning", "")
        except Exception as e:
            LOG.warning("Sniper call failed: %s", e)

        return tier1, tier1.get("reasoning", "Sniper unavailable — Gemini workhorse only.")
