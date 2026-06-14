import logging

from tradebot.engines.smc import (
    detect_bos,
    detect_false_break,
    detect_fvg_zones,
    detect_idm,
    detect_order_block,
    detect_supply_demand_zones,
)

LOG = logging.getLogger(__name__)


def _clamp_sltp(sig: dict, display: str = "XAUUSD") -> dict:
    """Enforce realistic SL/TP bounds. Prevents 80-pip SL or 760-pip TP."""
    action = sig.get("action", "").upper()
    if action not in ("BUY", "SELL"):
        return sig

    entry = sig.get("entry", 0)
    sl = sig.get("sl", 0)
    if not entry or not sl:
        return sig

    pip_sizes = {"XAUUSD": 0.10, "GOLD": 0.10, "USOIL": 0.01, "BTCUSD": 1.0, "ETHUSD": 0.01}
    s = display.upper()
    pip_size = pip_sizes.get(s, 0.01)
    if s.endswith(".JK") or s.isalpha() and len(s) <= 5:
        pip_size = 0.01

    sl_dist_pts = abs(entry - sl)
    sl_pips = sl_dist_pts / pip_size
    LOG.debug("_clamp_sltp [%s]: %s entry=%s sl=%s sl_pips=%.0f", display, action, entry, sl, sl_pips)

    MIN_SL = 20
    MAX_SL = 35
    MAX_TP = 100
    # Stocks: wider SL/TP in pips since pip=$0.01
    if pip_size == 0.01 and s not in pip_sizes:
        MIN_SL = 50
        MAX_SL = 150
        MAX_TP = 400
        sl_dist_pts = MIN_SL * pip_size
        clamped = True
    elif sl_pips > MAX_SL:
        sl_dist_pts = MAX_SL * pip_size
        clamped = True

    direction_wrong = (action == "BUY" and sig["sl"] > entry) or (action == "SELL" and sig["sl"] < entry)
    if clamped or direction_wrong:
        if action == "BUY":
            sig["sl"] = round(entry - sl_dist_pts, 2)
            sig["tp"] = round(entry + min(sl_dist_pts * 2.0, MAX_TP * pip_size), 2)
        else:
            sig["sl"] = round(entry + sl_dist_pts, 2)
            sig["tp"] = round(entry - min(sl_dist_pts * 2.0, MAX_TP * pip_size), 2)

        sig["tp1"] = sig["tp"]
        sig["_tier_capped"] = True
        LOG.debug("_clamp_sltp: corrected %s to SL=%s TP=%s", action, sig["sl"], sig["tp"])

    return sig


def detect_stier_zone(symbol: str = "XAUUSD", display: str = "XAUUSD", price: float | None = None, ohlcv_bars: list[dict] | None = None) -> tuple[dict | None, str | None]:
    """High-conviction zone scanner: Breaker Block + OB/FVG confluence + Double Sweep."""
    if not ohlcv_bars or len(ohlcv_bars) < 30:
        return None, None
    if not price:
        price = float(ohlcv_bars[-1].get("close", ohlcv_bars[-1].get("open", 0)))

    pip_s = 0.10 if display in ("XAUUSD", "GOLD") else (0.01 if display == "USOIL" else (1.0 if display in ("BTCUSD", "ETHUSD") else 0.0001))

    confluence_zones = []

    # ── Layer 1: Order Blocks + Supply/Demand Zones from SMC engine ──
    obs = []
    try:
        ob_data = detect_order_block(ohlcv_bars)
        if ob_data and isinstance(ob_data, dict) and ob_data.get("direction"):
            ob_price = (ob_data.get("upper", 0) + ob_data.get("lower", 0)) / 2
            if ob_price > 0:
                obs.append({"price": ob_price, "direction": ob_data["direction"].upper(), "strength": ob_data.get("strength", 3)})
        
        sd_zones = detect_supply_demand_zones(ohlcv_bars)
        for z in sd_zones[:5]:
            z_type = z.get("type", "")
            z_dir = "BUY" if "DEMAND" in str(z_type).upper() else "SELL" if "SUPPLY" in str(z_type).upper() else ""
            z_price = (z.get("upper", 0) + z.get("lower", 0)) / 2
            if z_price > 0 and z_dir:
                obs.append({"price": z_price, "direction": z_dir, "strength": z.get("strength", 2)})
    except Exception as e:
        LOG.debug("S-TIER OB scan: %s", e)

    if not obs:
        return None, None

    # ── Layer 2: FVG zones ──
    fvg_zones = []
    try:
        raw_fvg = detect_fvg_zones(ohlcv_bars, lookback=30)
        if raw_fvg:
            fvg_zones.append({
                "top": raw_fvg.get("top", 0),
                "bottom": raw_fvg.get("bottom", 0),
                "mid": (raw_fvg.get("top", 0) + raw_fvg.get("bottom", 0)) / 2,
                "direction": raw_fvg.get("direction", ""),
                "filled": raw_fvg.get("filled", False),
                "size_pips": raw_fvg.get("size_pips", 0)
            })
    except Exception as e:
        LOG.debug("S-TIER FVG scan: %s", e)

    # ── Layer 3: Structure ──
    bos = detect_bos(ohlcv_bars)
    fb = detect_false_break(ohlcv_bars)
    idm = detect_idm(ohlcv_bars)

    bos_price = bos.get("price") if bos else None
    false_break_price = fb.get("price") if fb else None
    fb_dir = fb.get("direction", "") if fb else None
    idm_price = idm.get("price") if idm else None

    PROXIMITY_PIPS = 15

    def _near(a, b):
        return abs(a - b) / pip_s <= PROXIMITY_PIPS

    # ── Layer 4: Trend context ──
    trend_bias = "NEUTRAL"
    try:
        closes = [float(b.get("close", b.get("c", 0))) for b in ohlcv_bars[-30:]]
        if len(closes) >= 20:
            ema20 = sum(closes[-20:]) / 20
            ema10 = sum(closes[-10:]) / 10
            if ema10 > ema20 * 1.002:
                trend_bias = "BULLISH"
            elif ema10 < ema20 * 0.998:
                trend_bias = "BEARISH"
    except Exception:
        pass

    for ob in obs:
        zone_level = ob["price"]
        ob_dir = ob["direction"]
        ob_strength = ob.get("strength", 3)

        if ob_strength < 3:
            continue

        score = 1.0
        reasons = [f"OB {ob_dir} [{ob_strength}/5] @ {zone_level:.2f}"]

        ob_is_bull = "BULL" in ob_dir
        price_above_ob = price > zone_level
        breaker = (ob_is_bull and not price_above_ob) or (not ob_is_bull and price_above_ob)

        if breaker:
            ob_tested = False
            for b in ohlcv_bars[-10:]:
                b_low = float(b.get("low", b.get("l", 0)))
                b_high = float(b.get("high", b.get("h", 0)))
                if _near(zone_level, b_low) or _near(zone_level, b_high):
                    ob_tested = True
                    break

            if ob_tested:
                score += 2.5
                reasons.append("🔥 BREAKER BLOCK — OB broken after test, now acts as S/R")
            else:
                score += 1.0
                reasons.append("⚠️ Breaker unconfirmed — OB not recently tested")

        if false_break_price and _near(zone_level, false_break_price):
            if not fb_dir or fb_dir == ob_dir:
                score += 1.5
                reasons.append(f"⚠️ False Break confirmed @ {false_break_price:.2f}")

        if idm_price and _near(zone_level, idm_price):
            score += 1
            reasons.append(f"💧 IDM sweep @ {idm_price:.2f}")

        for fvg in fvg_zones:
            if _near(zone_level, fvg["mid"]) and not fvg["filled"]:
                direction_match = ("BULL" in ob_dir) == (fvg["direction"] == "BULLISH")
                fvg_good_size = fvg["size_pips"] >= (3 if display in ("XAUUSD", "GOLD") else 2)

                if direction_match and fvg_good_size:
                    score += 1.5
                    reasons.append(f"📐 FVG {fvg['size_pips']:.0f}pip aligned — direction match, mitigation magnet")
                elif direction_match:
                    score += 0.75
                    reasons.append(f"📐 FVG {fvg['size_pips']:.0f}pip aligned — small FVG")
                break

        if false_break_price and idm_price and _near(false_break_price, idm_price) and _near(zone_level, false_break_price):
            score += 2
            reasons.append("💀 DOUBLE SWEEP — liquidity cleared 2x at same level")

        direction = "SELL" if (breaker and ob_is_bull) or (not breaker and not ob_is_bull) else "BUY"

        if breaker and trend_bias != "NEUTRAL":
            breaker_with_trend = (ob_is_bull and trend_bias == "BEARISH") or (not ob_is_bull and trend_bias == "BULLISH")
            if breaker_with_trend:
                score += 0.5
                reasons.append(f"📈 Trend-aligned ({trend_bias}) — higher probability")

        confluence_zones.append({"level": zone_level, "score": score, "reasons": reasons, "direction": direction})

    if not confluence_zones:
        return None, None

    best = max(confluence_zones, key=lambda z: (z["score"], -abs(z["level"] - price)))

    if best["score"] < 3.5:
        return None, None

    direction = best["direction"]
    entry = best["level"]

    if best["score"] >= 6:
        grade, grade_label = "S-TIER", "💀 TRIPLE CONFLUENCE — GOD TIER ZONE"
    elif best["score"] >= 5:
        grade, grade_label = "A", "🔥 BREAKER BLOCK + FVG — High Conviction"
    else:
        grade, grade_label = "B", "⚡ STRUCTURAL ZONE — Valid Confluence"

    atr = None
    try:
        if ohlcv_bars and len(ohlcv_bars) >= 16:
            trs = []
            for i in range(1, min(15, len(ohlcv_bars))):
                high = float(ohlcv_bars[i].get("high", ohlcv_bars[i].get("h", 0)))
                low = float(ohlcv_bars[i].get("low", ohlcv_bars[i].get("l", 0)))
                prev_close = float(ohlcv_bars[i-1].get("close", ohlcv_bars[i-1].get("c", 0)))
                trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
            if trs:
                atr = sum(trs) / len(trs)
    except Exception:
        pass

    sl_distance = round((atr * (1.0 if grade == "S-TIER" else 1.5)) if atr and atr > 0 else (30 * pip_s), 2)
    tp_distance = round(sl_distance * 2.0, 2)

    price_distance = abs(price - entry)
    if price_distance > sl_distance * 0.5:
        LOG.info(
            "S-TIER zone [%s] price %.2f too far from zone %.2f (%.1f > %.1f sl_half) — ZONE WAIT mode, EA will enter when price reaches zone",
            display, price, entry, price_distance, sl_distance * 0.5,
        )

    if direction == "BUY":
        sl, tp = round(entry - sl_distance, 2), round(entry + tp_distance, 2)
        tp2_candidate = round(entry + (tp_distance + (tp_distance - sl_distance) * 0.5), 2)
    else:
        sl, tp = round(entry + sl_distance, 2), round(entry - tp_distance, 2)
        tp2_candidate = round(entry - (tp_distance + (tp_distance - sl_distance) * 0.5), 2)

    tp2 = tp2_candidate if abs(tp2_candidate - entry) / pip_s <= 200 else 0

    reason = f"🤖 S-TIER ZONE [{grade}]: {grade_label}\n" + "\n".join(f"  • {r}" for r in best["reasons"])
    zone_half = entry * 0.0005 if entry > 0 else 0

    sig = {
        "action": direction, "entry": entry,
        "zone_lo": entry - zone_half if zone_half else entry,
        "zone_hi": entry + zone_half if zone_half else entry,
        "entry_mode": "zone",
        "sl": sl, "tp": tp, "tp1": tp, "tp2": tp2,
        "confidence": min(0.95, 0.65 + best["score"] * 0.04),
        "rr_ratio": 2.0,
        "reasoning": reason, "ensemble": "mechanical", "voters": 0,
        "_model": f"S-TIER-ZONE-{grade}", "grade": grade,
        "source": "stier_zone_detector",
        "_tier_capped": False,
    }

    sig = _clamp_sltp(sig, display)
    LOG.info("🎯 S-TIER ZONE [%s]: %s %s @ $%.2f | Score=%.1f | Confluences: %d", grade, display, direction, entry, best['score'], len(best['reasons']))

    return sig, reason
