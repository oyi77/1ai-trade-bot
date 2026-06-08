#!/usr/bin/env python3
"""
MTF Top-Down Analysis Matrix — Engine Consensus v2.0
=====================================================
5-Timeframe (D1, H4, H1, M15, M5) hierarchical consensus with:
  - D1 & H4 → Macro Trend Filter (weight multiplier)
  - H1 & M15 → Structure Setup (FVG, Liquidity, SnR)
  - M5 → Execution/Trigger (Sniper Entry)
  - Smart cache (D1 4h, H4/H1 15min, M15/M5 realtime)
  - Vectorized Pandas calculations
"""

from __future__ import annotations
import logging, time, os, json as _json
from typing import Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Timeframe config ──
TIMEFRAMES = ["D1", "H4", "H1", "M15", "M5"]
TF_WEIGHTS = {"D1": 0.35, "H4": 0.25, "H1": 0.20, "M15": 0.12, "M5": 0.08}
TF_CACHE_TTL = {"D1": 14400, "H4": 900, "H1": 900, "M15": 0, "M5": 0}  # seconds
TF_YF_INTERVAL = {"D1": "1d", "H4": "60m", "H1": "60m", "M15": "15m", "M5": "5m"}
TF_YF_PERIOD  = {"D1": "6mo", "H4": "1mo", "H1": "14d", "M15": "5d", "M5": "2d"}
TF_BAR_COUNT  = {"D1": 120, "H4": 240, "H1": 168, "M15": 480, "M5": 576}
TF_ENGINE_MIN = {"D1": 80, "H4": 100, "H1": 72, "M15": 96, "M5": 96}

# ── MT5 OHLCV data (pushed by EA via bridge) ──
# Structured as {symbol: {tf: [bar_dict, ...]}}
_MT5_OHLCV_CACHE: dict[str, dict[str, list[dict]]] = {}
_MT5_CACHE_TIME: dict[str, float] = {}

# ── Engine imports (lazy) ──
_engines_loaded = False
_quant_engine = None
_fvg_detector = None
_hermes_pipeline = None
_crt_engine = None
_smc_engine = None
_ultimate_engine = None
_sequoia_math = None
_tv_engine = None

# ── yfinance OHLCV cache ──
_YF_CACHE: dict[str, dict] = {}
_YF_CACHE_TIME: dict[str, float] = {}


def _load_engines():
    """Lazy-load all engine modules."""
    global _engines_loaded, _quant_engine, _fvg_detector, _hermes_pipeline
    global _crt_engine, _smc_engine, _ultimate_engine, _sequoia_math, _tv_engine
    if _engines_loaded:
        return
    try:
        from quant_engine import analyze_quantitative_pattern
        _quant_engine = analyze_quantitative_pattern
    except Exception:
        pass
    try:
        from fvg_detector import detect_fvg
        _fvg_detector = detect_fvg
    except Exception:
        pass
    try:
        from hermes_liquidity_hunter import hermes_pipeline
        _hermes_pipeline = hermes_pipeline
    except Exception:
        pass
    try:
        from crt_tbs_engine import analyze_crt_setup
        _crt_engine = analyze_crt_setup
    except Exception:
        pass
    try:
        from smc_scalper_engine import analyze_smc_scalper, analyze_trend_break
        _smc_engine = (analyze_smc_scalper, analyze_trend_break)
    except Exception:
        pass
    try:
        from ultimate_smc_engine import ultimate_analyze
        _ultimate_engine = ultimate_analyze
    except Exception:
        pass
    try:
        from sequoia_math import turtle_breakout, turtle_trend_filter
        _sequoia_math = (turtle_breakout, turtle_trend_filter)
    except Exception:
        pass
    try:
        from tv_engine import analyze as _tv_analyze
        _tv_engine = _tv_analyze
    except Exception:
        pass
    _engines_loaded = True


def _bars_to_dicts(bars: list) -> list[dict]:
    """Convert raw bar objects to dict list."""
    return [{
        "timestamp": getattr(b, "timestamp", 0),
        "open": float(getattr(b, "open", 0)),
        "high": float(getattr(b, "high", 0)),
        "low": float(getattr(b, "low", 0)),
        "close": float(getattr(b, "close", 0)),
        "volume": float(getattr(b, "volume", 0)),
    } for b in bars] if bars and not isinstance(bars[0], dict) else bars


# ════════════════════════════════════════════════════════════════
# MTF DATA — FROM EA BRIDGE (MT5 Exness — priority) + yfinance fallback
# ════════════════════════════════════════════════════════════════

def push_mt5_ohlcv(symbol: str, tf: str, bars: list[dict]):
    """Called by bridge when EA pushes MT5 OHLCV data."""
    if symbol not in _MT5_OHLCV_CACHE:
        _MT5_OHLCV_CACHE[symbol] = {}
    _MT5_OHLCV_CACHE[symbol][tf] = _bars_to_dicts(bars)
    _MT5_CACHE_TIME[f"{symbol}/{tf}"] = time.time()


def _has_mt5_data(symbol: str, tf: str) -> bool:
    """Check if MT5 data exists and is recent."""
    key = f"{symbol}/{tf}"
    if key not in _MT5_CACHE_TIME:
        return False
    age = time.time() - _MT5_CACHE_TIME[key]
    if age > TF_CACHE_TTL.get(tf, 900):  # stale
        return False
    if symbol in _MT5_OHLCV_CACHE and tf in _MT5_OHLCV_CACHE[symbol]:
        bars = _MT5_OHLCV_CACHE[symbol][tf]
        if len(bars) >= TF_ENGINE_MIN.get(tf, 72):
            return True
    return False


def _fetch_yf_bars(symbol: str, tf: str) -> list[dict] | None:
    """Fetch OHLCV from yfinance with smart caching."""
    import yfinance as yf
    yf_sym = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD"}.get(symbol, symbol)
    interval = TF_YF_INTERVAL[tf]
    period = TF_YF_PERIOD[tf]
    min_bars = TF_ENGINE_MIN[tf]

    cache_key = f"{yf_sym}/{tf}"
    ttl = TF_CACHE_TTL[tf]
    now = time.time()
    if ttl > 0 and cache_key in _YF_CACHE:
        if now - _YF_CACHE_TIME.get(cache_key, 0) < ttl:
            cached = _YF_CACHE[cache_key]
            if len(cached) >= min_bars:
                return cached

    try:
        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period=period, interval=interval)
        if df is None or len(df) < min_bars:
            logger.warning(f"yf {yf_sym} {tf}: got {len(df) if df is not None else 0} bars, need {min_bars}")
            return None
    except Exception as e:
        logger.warning(f"yf {yf_sym} {tf} fetch error: {e}")
        return None

    bars = []
    for idx, row in df.iterrows():
        bars.append({
            "timestamp": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })

    if ttl > 0:
        _YF_CACHE[cache_key] = bars
        _YF_CACHE_TIME[cache_key] = now
    return bars


def fetch_mtf_ohlcv(symbol: str = "XAUUSD") -> dict[str, list[dict]]:
    """
    Fetch OHLCV for all 5 timeframes in parallel.
    Priority: MT5 EA bridge > yfinance fallback.
    Returns {tf: [bar_dict, ...]}
    """
    result = {}
    # Check which TFs need yfinance fetch vs have MT5 data
    yf_tfs = []
    for tf in TIMEFRAMES:
        if _has_mt5_data(symbol, tf):
            result[tf] = _MT5_OHLCV_CACHE[symbol][tf]
            logger.info(f"MT5/{symbol} {tf}: {len(result[tf])} bars")
        else:
            # Check cache first
            yf_sym = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD"}.get(symbol, symbol)
            cache_key = f"{yf_sym}/{tf}"
            ttl = TF_CACHE_TTL[tf]
            now = time.time()
            if ttl > 0 and cache_key in _YF_CACHE:
                if now - _YF_CACHE_TIME.get(cache_key, 0) < ttl:
                    cached = _YF_CACHE[cache_key]
                    if len(cached) >= TF_ENGINE_MIN[tf]:
                        result[tf] = cached
                        logger.info(f"yf-cache/{symbol} {tf}: {len(cached)} bars")
                        continue
            yf_tfs.append(tf)

    # Parallel fetch remaining TFs from yfinance
    if yf_tfs:
        import concurrent.futures as cf
        yf_sym = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD"}.get(symbol, symbol)
        with cf.ThreadPoolExecutor(max_workers=5) as exe:
            future_map = {exe.submit(_fetch_yf_bars, symbol, tf): tf for tf in yf_tfs}
            for future in cf.as_completed(future_map, timeout=90):
                tf = future_map[future]
                try:
                    bars = future.result()
                    if bars:
                        result[tf] = bars
                    else:
                        logger.warning(f"No data for {symbol} {tf}")
                        result[tf] = []
                except Exception as e:
                    logger.warning(f"Fetch error {symbol} {tf}: {e}")
                    result[tf] = []

    # Ensure all TFs present
    for tf in TIMEFRAMES:
        if tf not in result:
            result[tf] = []

    return result


# ════════════════════════════════════════════════════════════════
# SINGLE-TIMEFRAME ENGINE EXECUTION
# ════════════════════════════════════════════════════════════════

def _run_engines_on_tf(ohlcv: list[dict], price: float, symbol: str, tf: str) -> dict:
    """
    Run all 8 engines on a single timeframe's OHLCV.
    Returns {engines: {}, buy_count, sell_count, total, verdict, consensus_pct}
    """
    _load_engines()
    engines = {}
    buys = sells = 0
    active = 0

    # ── 1. Quant Engine ──
    if _quant_engine:
        try:
            qdata = [{"timestamp": b.get("timestamp", 0), "open": float(b["open"]),
                      "high": float(b["high"]), "low": float(b["low"]),
                      "close": float(b["close"]), "volume": float(b.get("volume", 0))}
                     for b in ohlcv]
            result = _quant_engine(qdata, pattern_size=5)
            if result and not result.get("error"):
                dom = result.get("dominant_next", "")
                conf_val = result.get("confidence_score", 0)
                engines["quant"] = {
                    "direction": "BUY" if dom == "G" else ("SELL" if dom == "R" else "HOLD"),
                    "confidence": conf_val,
                    "details": f"G:{result.get('green_pct',0):.0f}% R:{result.get('red_pct',0):.0f}%"
                }
                if dom == "G": buys += 1
                elif dom == "R": sells += 1
                active += 1
        except Exception as e:
            engines["quant"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── 2. FVG Detector ──
    if _fvg_detector:
        try:
            fvg_sigs = _fvg_detector(ohlcv, tf)
            if fvg_sigs and len(fvg_sigs) > 0:
                best = fvg_sigs[0]
                direction = best.direction if hasattr(best, 'direction') else str(best.get("direction", ""))
                engines["fvg"] = {
                    "direction": direction,
                    "confidence": getattr(best, 'confidence', 0.3),
                    "details": f"RR=1:{getattr(best, 'rr_ratio', 0):.1f}"
                }
                if direction == "BUY": buys += 1
                elif direction == "SELL": sells += 1
                active += 1
            else:
                engines["fvg"] = {"direction": "HOLD", "confidence": 0, "details": "no gap"}
        except Exception as e:
            engines["fvg"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── 3. Hermes Liquidity Hunter ──
    if _hermes_pipeline:
        try:
            hermes = _hermes_pipeline(ohlcv, ohlcv, price)
            if hermes and hasattr(hermes, 'action') and hermes.action in ("BUY", "SELL"):
                grade = "A" if getattr(hermes, 'risk_reward_ratio', 0) >= 2.0 else "B"
                engines["hermes"] = {
                    "direction": hermes.action,
                    "confidence": getattr(hermes, 'confidence', 0.6),
                    "details": f"Grade:{grade} RR=1:{getattr(hermes, 'risk_reward_ratio', 0):.1f}"
                }
                if hermes.action == "BUY": buys += 1
                elif hermes.action == "SELL": sells += 1
                active += 1
            else:
                engines["hermes"] = {"direction": "HOLD", "confidence": 0, "details": "no sweep"}
        except Exception as e:
            engines["hermes"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── 4. CRT/TBS ──
    if _crt_engine:
        try:
            crt = _crt_engine(ohlcv, symbol)
            direction = crt.get("signal", "HOLD") if crt else "HOLD"
            if crt and direction in ("BUY", "SELL"):
                grade_label = crt.get("grade_label", "?")
                engines["crt"] = {
                    "direction": direction,
                    "confidence": {"A+": 0.9, "A": 0.8, "B": 0.6, "C": 0.4, "D": 0.2}.get(
                        grade_label[0] if len(grade_label) > 0 else "D", 0.3),
                    "details": f"Grade={grade_label} score={crt.get('score', 0)}"
                }
                if direction == "BUY": buys += 1
                elif direction == "SELL": sells += 1
                active += 1
            else:
                details = crt.get("reasoning", "no setup")[:40] if crt else "no engine"
                engines["crt"] = {"direction": "HOLD", "confidence": 0, "details": details}
        except Exception as e:
            engines["crt"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── 5. SMC Scalper ──
    if _smc_engine:
        try:
            smc_fn, _ = _smc_engine
            smc = smc_fn(ohlcv, symbol)
            direction = smc.get("signal", "HOLD") if smc else "HOLD"
            if smc and direction in ("BUY", "SELL"):
                grade_val = smc.get("grade", 0)
                conf_map = {5: 0.9, 4: 0.75, 3: 0.55, 2: 0.35, 1: 0.2}
                engines["smc"] = {
                    "direction": direction,
                    "confidence": conf_map.get(
                        grade_val.value if hasattr(grade_val, 'value') else int(grade_val), 0.4),
                    "details": f"Grade={smc.get('grade_label','?')} score={smc.get('score',0)}"
                }
                if direction == "BUY": buys += 1
                elif direction == "SELL": sells += 1
                active += 1
            else:
                engines["smc"] = {"direction": "HOLD", "confidence": 0, "details": "no setup"}
        except Exception as e:
            engines["smc"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── 6. Trend Break ──
    if _smc_engine:
        try:
            _, trend_fn = _smc_engine
            trend = trend_fn(ohlcv, symbol)
            direction = trend.get("signal", "HOLD") if trend else "HOLD"
            if trend and direction in ("BUY", "SELL"):
                grade_val = trend.get("grade", 0)
                conf_map = {5: 0.85, 4: 0.7, 3: 0.5, 2: 0.3, 1: 0.15}
                engines["trend"] = {
                    "direction": direction,
                    "confidence": conf_map.get(
                        grade_val.value if hasattr(grade_val, 'value') else int(grade_val), 0.35),
                    "details": f"Grade={trend.get('grade_label','?')} score={trend.get('score',0)}"
                }
                if direction == "BUY": buys += 1
                elif direction == "SELL": sells += 1
                active += 1
            else:
                engines["trend"] = {"direction": "HOLD", "confidence": 0, "details": "no break"}
        except Exception as e:
            engines["trend"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── 7. Ultimate SMC v3.0 ──
    if _ultimate_engine:
        try:
            ult = _ultimate_engine(ohlcv, symbol, price)
            if ult and hasattr(ult, 'signal') and ult.signal in ("BUY", "SELL"):
                conf_val = ult.score / max(getattr(ult, 'max_score', 24), 1) if hasattr(ult, 'score') else 0.5
                engines["ultimate"] = {
                    "direction": ult.signal,
                    "confidence": conf_val,
                    "details": f"Grade:{getattr(ult, 'grade_label', '?')} score={getattr(ult, 'score', 0)}/{getattr(ult, 'max_score', 24)}"
                }
                if ult.signal == "BUY": buys += 1
                elif ult.signal == "SELL": sells += 1
                active += 1
            else:
                engines["ultimate"] = {"direction": "HOLD", "confidence": 0, "details": "no signal"}
        except Exception as e:
            engines["ultimate"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── 8. Sequoia Math (Turtle + Trend Filter) ──
    if _sequoia_math:
        try:
            import pandas as pd
            df = pd.DataFrame(ohlcv)
            df.columns = [c.lower() for c in df.columns]
            turtle_fn, trend_fn = _sequoia_math
            turtle_result = turtle_fn(df)
            trend_result = trend_fn(df)
            if turtle_result is not None and trend_result is not None:
                turtle_last = bool(turtle_result.iloc[-1]) if len(turtle_result) > 0 else False
                if isinstance(trend_result, tuple) and len(trend_result) >= 3:
                    tf_dir = trend_result[2]
                    trend_last = str(tf_dir)
                else:
                    trend_last = str(trend_result.iloc[-1]) if len(trend_result) > 0 else "0"
                if turtle_last:
                    direction = "BUY"
                elif trend_last == "-1":
                    direction = "SELL"
                else:
                    direction = "HOLD"
                engines["sequoia"] = {
                    "direction": direction,
                    "confidence": 0.6 if turtle_last else (0.4 if direction == "SELL" else 0),
                    "details": f"turtle={turtle_last} trend={trend_last}"
                }
                if direction == "BUY": buys += 1
                elif direction == "SELL": sells += 1
                active += 1
            else:
                engines["sequoia"] = {"direction": "HOLD", "confidence": 0, "details": "no signal"}
        except Exception as e:
            engines["sequoia"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── 9. TradingView TA Engine ──
    if _tv_engine:
        try:
            tv_result = _tv_engine(symbol, ohlcv)
            tv_dir = tv_result.get("direction", "HOLD")
            tv_conf = tv_result.get("confidence", 0.5)
            tv_detail = tv_result.get("details", "")
            engines["tv"] = {
                "direction": tv_dir,
                "confidence": tv_conf,
                "details": tv_detail,
            }
            if tv_dir == "BUY": buys += 1
            elif tv_dir == "SELL": sells += 1
            active += 1
        except Exception as e:
            engines["tv"] = {"direction": "ERROR", "confidence": 0, "details": str(e)[:50]}

    # ── TF-local verdict ──
    verdict = "HOLD"
    consensus_pct = 0.0
    if active > 0:
        # Dynamic threshold: need majority + minimum floor
        majority = active // 2 + 1  # e.g., 3/5=3, 2/4=3, 3/6=4, 3/3=2
        min_votes = min(4, max(2, active // 2 + 1))
        threshold = max(majority, min_votes)
        max_votes = max(buys, sells)
        consensus_pct = max_votes / active
        if buys >= threshold and buys > sells:
            verdict = "BUY"
        elif sells >= threshold and sells > buys:
            verdict = "SELL"

    return {
        "engines": engines,
        "buy_count": buys,
        "sell_count": sells,
        "total": active,
        "verdict": verdict,
        "consensus_pct": consensus_pct,
    }


# ════════════════════════════════════════════════════════════════
# VECTORIZED MACRO TREND DETECTION (D1 & H4)
# ════════════════════════════════════════════════════════════════

def _vectorized_macro_trend(ohlcv: list[dict], price: float) -> dict:
    """
    Pandas-vectorized macro trend analysis for D1/H4.
    Returns {trend: "BULLISH"/"BEARISH"/"NEUTRAL", strength: 0-1, ema200_dist: %}
    """
    try:
        import pandas as pd
        df = pd.DataFrame(ohlcv)
        if len(df) < 50:
            return {"trend": "NEUTRAL", "strength": 0.0, "ema200_dist": 0.0}

        close = df["close"].astype(float) if "close" in df.columns else (
            df["Close"].astype(float) if "Close" in df.columns else df.iloc[:, 3].astype(float)
        )
        close_vals = close.values
        price_f = float(close_vals[-1])

        # EMA200
        ema200 = pd.Series(close_vals).ewm(span=200, adjust=False).mean().iloc[-1]
        ema200_dist = (price_f - ema200) / ema200 * 100 if ema200 != 0 and not pd.isna(ema200) else 0

        # SMA20/50 cross
        sma20 = pd.Series(close_vals).rolling(20).mean()
        sma50 = pd.Series(close_vals).rolling(50).mean()
        sma_trend = "BULLISH" if sma20.iloc[-1] > sma50.iloc[-1] else "BEARISH"

        # HH/HL detection (recent 10 bars)
        last_10 = close_vals[-10:]
        making_higher = last_10[-1] > last_10[0]
        making_lower = last_10[-1] < last_10[0]

        # ATR ratio for volatility context
        atr = pd.Series(close_vals).diff().abs().rolling(14).mean().iloc[-1]
        atr_pct = atr / price_f * 100 if price_f > 0 else 0

        # RSI (14)
        delta = pd.Series(close_vals).diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs)).iloc[-1]

        # Score-based trend detection
        bullish_score = 0
        bearish_score = 0

        if sma_trend == "BULLISH":
            bullish_score += 2
        else:
            bearish_score += 2

        if price_f > ema200:
            bullish_score += 2
        else:
            bearish_score += 2

        if making_higher:
            bullish_score += 1
        if making_lower:
            bearish_score += 1

        if rsi > 50:
            bullish_score += 1
        else:
            bearish_score += 1

        total = bullish_score + bearish_score
        if total == 0:
            trend = "NEUTRAL"
            strength = 0.0
        else:
            if bullish_score > bearish_score:
                trend = "BULLISH"
                strength = bullish_score / total
            elif bearish_score > bullish_score:
                trend = "BEARISH"
                strength = bearish_score / total
            else:
                trend = "NEUTRAL"
                strength = 0.0

        return {
            "trend": trend,
            "strength": round(strength, 2),
            "ema200_dist": round(ema200_dist, 2),
            "rsi": round(rsi, 1) if not pd.isna(rsi) else 50.0,
            "atr_pct": round(atr_pct, 2),
            "sma_cross": sma_trend,
        }
    except Exception as e:
        logger.warning(f"Macro trend calc error: {e}")
        return {"trend": "NEUTRAL", "strength": 0.0, "ema200_dist": 0.0}


# ════════════════════════════════════════════════════════════════
# VECTORIZED STRUCTURE DETECTION (H1 & M15 — SnR levels)
# ════════════════════════════════════════════════════════════════

def _vectorized_snr_levels(ohlcv: list[dict], price: float) -> dict:
    """
    Find support/resistance levels and structure boundaries.
    Returns {s1, r1, near_support, near_resistance, structure_type}
    """
    try:
        import pandas as pd
        import numpy as np
        df = pd.DataFrame(ohlcv)
        if len(df) < 30:
            return {"s1": 0, "r1": 0, "near_support": False, "near_resistance": False, "structure_type": "unknown"}

        high = df["high"].astype(float) if "high" in df.columns else (
            df["High"].astype(float) if "High" in df.columns else df.iloc[:, 1].astype(float)
        )
        low = df["low"].astype(float) if "low" in df.columns else (
            df["Low"].astype(float) if "Low" in df.columns else df.iloc[:, 2].astype(float)
        )
        close = df["close"].astype(float) if "close" in df.columns else (
            df["Close"].astype(float) if "Close" in df.columns else df.iloc[:, 3].astype(float)
        )

        # Recent swing high/low (last 20 bars)
        recent_high = float(high.tail(20).max())
        recent_low = float(low.tail(20).min())
        range_pct = (recent_high - recent_low) / price * 100 if price > 0 else 0

        # Volume-weighted support/resistance
        vol = df["volume"].astype(float).tail(20) if "volume" in df.columns else pd.Series([1]*20)
        vwap = (close.tail(20) * vol).sum() / vol.sum() if vol.sum() > 0 else price

        # Distance to levels
        dist_to_high = (recent_high - price) / price * 100 if price > 0 else 0
        dist_to_low = (price - recent_low) / price * 100 if price > 0 else 0
        near_resistance = dist_to_high < 0.5  # within 0.5%
        near_support = dist_to_low < 0.5

        # Structure type
        if range_pct < 1.0:
            structure = "compressed"
        elif range_pct < 2.5:
            structure = "normal"
        else:
            structure = "expanded"

        return {
            "s1": round(recent_low, 2),
            "r1": round(recent_high, 2),
            "vwap": round(vwap, 2),
            "near_support": near_support,
            "near_resistance": near_resistance,
            "range_pct": round(range_pct, 2),
            "structure_type": structure,
        }
    except Exception as e:
        logger.warning(f"SnR levels error: {e}")
        return {"s1": 0, "r1": 0, "near_support": False, "near_resistance": False, "structure_type": "unknown"}


# ════════════════════════════════════════════════════════════════
# VECTORIZED ENTRY DETECTION (M5 — sniper/sweep confirm)
# ════════════════════════════════════════════════════════════════

def _vectorized_entry_trigger(ohlcv: list[dict], price: float) -> dict:
    """
    M5-level micro structure detection.
    Returns {sweep_detected, sniper_entry, micro_trend, momentum}
    """
    try:
        import pandas as pd
        import numpy as np
        df = pd.DataFrame(ohlcv)
        if len(df) < 20:
            return {"sweep_detected": False, "sniper_entry": "NONE", "micro_trend": "NEUTRAL", "momentum": 0}

        close = df["close"].astype(float) if "close" in df.columns else (
            df["Close"].astype(float) if "Close" in df.columns else df.iloc[:, 3].astype(float)
        )
        high = df["high"].astype(float) if "high" in df.columns else (
            df["High"].astype(float) if "High" in df.columns else df.iloc[:, 1].astype(float)
        )
        low = df["low"].astype(float) if "low" in df.columns else (
            df["Low"].astype(float) if "Low" in df.columns else df.iloc[:, 2].astype(float)
        )

        close_vals = close.values
        high_vals = high.values
        low_vals = low.values

        # Recent 10-bar range
        recent_high = float(high_vals[-10:].max())
        recent_low = float(low_vals[-10:].min())

        # Sweep detection: price briefly broke recent swing then reversed
        sweep_high = float(high_vals[-5:].max()) > recent_high * 1.001 and close_vals[-1] < recent_high * 0.999
        sweep_low = float(low_vals[-5:].min()) < recent_low * 0.999 and close_vals[-1] > recent_low * 1.001

        # Momentum (rate of change)
        mom_3 = (close_vals[-1] - close_vals[-3]) / close_vals[-3] * 100 if len(close_vals) >= 3 and close_vals[-3] != 0 else 0
        mom_8 = (close_vals[-1] - close_vals[-8]) / close_vals[-8] * 100 if len(close_vals) >= 8 and close_vals[-8] != 0 else 0

        # Micro trend
        if mom_3 > 0.1 and mom_8 > 0.1:
            micro_trend = "BULLISH"
        elif mom_3 < -0.1 and mom_8 < -0.1:
            micro_trend = "BEARISH"
        else:
            micro_trend = "NEUTRAL"

        # Sniper entry signal
        sniper_entry = "NONE"
        if sweep_high and micro_trend == "BEARISH":
            sniper_entry = "SELL"
        elif sweep_low and micro_trend == "BULLISH":
            sniper_entry = "BUY"
        elif abs(mom_3) > 0.3:
            sniper_entry = "BUY" if mom_3 > 0 else "SELL"

        return {
            "sweep_detected": sweep_high or sweep_low,
            "sweep_type": "LIQUIDITY_HIGH" if sweep_high else ("LIQUIDITY_LOW" if sweep_low else "NONE"),
            "sniper_entry": sniper_entry,
            "micro_trend": micro_trend,
            "momentum": round(mom_3, 3),
            "momentum_8": round(mom_8, 3),
        }
    except Exception as e:
        logger.warning(f"Entry trigger error: {e}")
        return {"sweep_detected": False, "sniper_entry": "NONE", "micro_trend": "NEUTRAL", "momentum": 0}


# ════════════════════════════════════════════════════════════════
# HIERARCHICAL CONSENSUS
# ════════════════════════════════════════════════════════════════

def _compute_hierarchical_verdict(tf_results: dict[str, dict]) -> dict:
    """
    Weighted hierarchical consensus across all timeframes.
    D1 & H4 = macro filter (weight: 0.35 + 0.25 = 0.60)
    H1 & M15 = structure setup (weight: 0.20 + 0.12 = 0.32)
    M5 = entry trigger (weight: 0.08)

    Returns {verdict, consensus_score, mtf_alignment, counter_trend_flags, weight_distribution}
    """
    weighted_buy = 0.0
    weighted_sell = 0.0
    total_weight = 0.0
    counter_trend_flags = []
    macro_trend = "NEUTRAL"
    macro_verdicts = []

    # First pass: determine macro trend from D1 + H4
    for tf in ["D1", "H4"]:
        if tf in tf_results:
            r = tf_results[tf]
            if r.get("verdict") in ("BUY", "SELL"):
                macro_verdicts.append(r["verdict"])

    if all(v == "BUY" for v in macro_verdicts):
        macro_trend = "BULLISH"
    elif all(v == "SELL" for v in macro_verdicts):
        macro_trend = "BEARISH"
    elif macro_verdicts:
        # Mixed — check which is stronger
        d1 = tf_results.get("D1", {})
        h4 = tf_results.get("H4", {})
        d1_bias = 1 if d1.get("buy_count", 0) > d1.get("sell_count", 0) else 0
        h4_bias = 1 if h4.get("buy_count", 0) > h4.get("sell_count", 0) else 0
        macro_trend = "BULLISH" if (d1_bias + h4_bias) > 0 else ("BEARISH" if (d1_bias + h4_bias) < 0 else "NEUTRAL")

    # Second pass: weighted consensus with counter-trend detection
    alignment_count = 0
    total_tfs = 0

    for tf in TIMEFRAMES:
        if tf not in tf_results:
            continue
        r = tf_results[tf]
        weight = TF_WEIGHTS[tf]
        verdict = r.get("verdict", "HOLD")
        total_tfs += 1

        # Counter-trend check
        if verdict == "BUY" and macro_trend == "BEARISH":
            counter_trend_flags.append(f"{tf} BUY counter-trend vs D1/H4 {macro_trend}")
            # Penalize: reduce weight by 50%
            weight *= 0.5
        elif verdict == "SELL" and macro_trend == "BULLISH":
            counter_trend_flags.append(f"{tf} SELL counter-trend vs D1/H4 {macro_trend}")
            weight *= 0.5

        if verdict == "BUY":
            weighted_buy += weight
            if macro_trend == "BULLISH":
                alignment_count += 1
        elif verdict == "SELL":
            weighted_sell += weight
            if macro_trend == "BEARISH":
                alignment_count += 1

        total_weight += weight

    # Normalize
    if total_weight == 0:
        return {
            "verdict": "HOLD",
            "consensus_score": 0.0,
            "mtf_alignment": "NONE",
            "counter_trend_flags": [],
            "macro_trend": macro_trend,
        }

    weighted_buy_norm = weighted_buy / total_weight
    weighted_sell_norm = weighted_sell / total_weight

    # Final verdict
    threshold = 0.35  # Need at least 35% weighted consensus
    if weighted_buy_norm > threshold and weighted_buy_norm > weighted_sell_norm:
        verdict = "BUY"
        consensus_score = weighted_buy_norm
    elif weighted_sell_norm > threshold and weighted_sell_norm > weighted_buy_norm:
        verdict = "SELL"
        consensus_score = weighted_sell_norm
    else:
        verdict = "HOLD"
        consensus_score = max(weighted_buy_norm, weighted_sell_norm)

    # MTF alignment
    alignment_ratio = alignment_count / max(total_tfs, 1)
    if alignment_ratio >= 0.6:
        mtf_alignment = "ALIGNED"
    elif alignment_ratio >= 0.3:
        mtf_alignment = "MIXED"
    else:
        mtf_alignment = "CONFLICT"

    return {
        "verdict": verdict,
        "consensus_score": round(consensus_score, 4),
        "weighted_buy": round(weighted_buy_norm, 4),
        "weighted_sell": round(weighted_sell_norm, 4),
        "mtf_alignment": mtf_alignment,
        "counter_trend_flags": counter_trend_flags,
        "macro_trend": macro_trend,
    }


def _format_pulse_text(mtf_result: dict) -> str:
    """Generate Telegram Market Pulse message from MTF result."""
    ts = mtf_result.get("timestamp", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    price = mtf_result.get("price", 0)
    symbol = mtf_result.get("symbol", "XAUUSD")
    hier = mtf_result.get("hierarchical", {})
    macro_trend = hier.get("macro_trend", "NEUTRAL")
    mtf_alignment = hier.get("mtf_alignment", "MIXED")
    verdict = hier.get("verdict", "HOLD")
    score = hier.get("consensus_score", 0)
    flags = hier.get("counter_trend_flags", [])

    # Timezone
    try:
        from datetime import timedelta
        wib = datetime.now(timezone(timedelta(hours=7))).strftime("%Y.%m.%d %H:%M")
    except Exception:
        wib = ts[:19]

    alignment_emoji = {"ALIGNED": "✅", "MIXED": "⚠️", "CONFLICT": "🔴", "NONE": "⚪️"}
    verdict_emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪️"}

    lines = [
        f"🔄 <b>MARKET PULSE — {symbol}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 {wib} WIB ${price:.2f}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # Per timeframe summary
    lines.append("<b>📊 MTF MATRIX</b>")
    for tf in TIMEFRAMES:
        if tf not in mtf_result.get("timeframes", {}):
            continue
        r = mtf_result["timeframes"][tf]
        tf_v = r.get("verdict", "HOLD")
        eng_icon = verdict_emoji.get(tf_v, "⚪️")
        buy_c = r.get("buy_count", 0)
        sell_c = r.get("sell_count", 0)
        total_c = r.get("total", 0)
        conf = r.get("consensus_pct", 0) * 100
        lines.append(f"{eng_icon} <b>{tf}</b> → {tf_v} ({conf:.0f}% | {buy_c}B/{sell_c}S/{total_c}T)")

    # Macro trend
    macro_emoji = {"BULLISH": "🟢", "BEARISH": "🔴", "NEUTRAL": "⚪️"}
    lines.append("")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🏛 <b>Macro: {macro_emoji.get(macro_trend, '⚪️')} {macro_trend}</b>")
    lines.append(f"{alignment_emoji.get(mtf_alignment, '⚪️')} <b>MTF: {mtf_alignment}</b>")
    lines.append(f"{verdict_emoji.get(verdict, '⚪️')} <b>Hierarchical: {verdict}</b> ({score*100:.0f}%)")

    if flags:
        for f in flags[:2]:
            lines.append(f"⚠️ {f}")

    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    # ── CLEAR DISCLAIMER ──
    lines.append(f"⚠️ <b>INI BUKAN SINYAL EKSEKUSI!</b>")
    lines.append(f"Market Pulse = engine status mentah (raw readings)")
    lines.append(f"Entry + TP/SL hanya muncul jika quality gate lolos → ACTIVE SIGNAL")
    lines.append(f"Jangan FOMO — tunggu konfirmasi resmi ya bro 💪")
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⚡ Isi Bahan Bakar AI → @berkahkaryaforexbotbot")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════

def run_engine_consensus(
    ohlcv: list[dict] | None = None,
    price: float | None = None,
    symbol: str = "XAUUSD",
) -> dict:
    """
    MTF Top-Down Analysis Matrix.

    If only single-timeframe data provided (legacy mode), runs on that data.
    Otherwise auto-fetches 5 timeframes from EA Bridge > yfinance.

    Returns:
    {
        "symbol": "XAUUSD",
        "price": 4322.50,
        "timestamp": "...",
        "timeframes": { "D1": {...}, "H4": {...}, ... },
        "hierarchical": { verdict, consensus_score, mtf_alignment, ... },
        "engines": { flattenend single-tf engines for backwards compat },
        "buy_count": int, "sell_count": int, "verdict": str, "consensus_pct": float,
    }
    """
    _load_engines()

    # If legacy single-TF data provided, still compute all TFs but use given data for M15
    mtf_data = fetch_mtf_ohlcv(symbol)

    # If caller provided M15 data, merge it in
    if ohlcv and len(ohlcv) > 10:
        mtf_data["M15"] = ohlcv

    if price is None and "M15" in mtf_data and mtf_data["M15"]:
        last_bar = mtf_data["M15"][-1]
        price = float(last_bar.get("close", 0))

    if price is None:
        price = 4300.0  # fallback

    # ── Compute per-timeframe ──
    tf_results: dict[str, dict] = {}
    macro_trends: dict[str, dict] = {}
    structure_data: dict[str, dict] = {}

    for tf in TIMEFRAMES:
        bars = mtf_data.get(tf, [])
        if len(bars) < TF_ENGINE_MIN.get(tf, 30):
            logger.warning(f"MTF {symbol} {tf}: only {len(bars)} bars, skipping")
            continue

        # Run 8 engines
        tf_result = _run_engines_on_tf(bars, price, symbol, tf)
        tf_results[tf] = tf_result

        # D1 & H4: macro trend
        if tf in ("D1", "H4"):
            macro = _vectorized_macro_trend(bars, price)
            macro_trends[tf] = macro
            tf_result["macro"] = macro
            tf_result["weight"] = TF_WEIGHTS[tf]

        # H1 & M15: structure levels
        if tf in ("H1", "M15"):
            struct = _vectorized_snr_levels(bars, price)
            structure_data[tf] = struct
            tf_result["structure"] = struct
            tf_result["weight"] = TF_WEIGHTS[tf]

        # M5: entry trigger
        if tf == "M5":
            entry = _vectorized_entry_trigger(bars, price)
            tf_result["entry"] = entry
            tf_result["weight"] = TF_WEIGHTS[tf]

        if tf not in ("D1", "H4"):
            tf_result["weight"] = TF_WEIGHTS[tf]

    # ── Hierarchical consensus ──
    hierarchical = _compute_hierarchical_verdict(tf_results)

    # ── Flattened verdict (backwards compat) ──
    verdict = hierarchical["verdict"]
    consensus_score = hierarchical["consensus_score"]

    # ── Build result ──
    result = {
        "symbol": symbol,
        "price": round(price, 2),
        "timestamp": datetime.now().isoformat(),
        "timeframes": tf_results,
        "macro_trends": macro_trends,
        "structure_data": structure_data,
        "hierarchical": hierarchical,
        # Backwards compat fields
        "engines": tf_results.get("M15", {}).get("engines", {}),
        "buy_count": tf_results.get("M15", {}).get("buy_count", 0),
        "sell_count": tf_results.get("M15", {}).get("sell_count", 0),
        "total": tf_results.get("M15", {}).get("total", 0),
        "verdict": verdict,
        "consensus_pct": consensus_score,
        "mtf_alignment": hierarchical["mtf_alignment"],
        "macro_trend": hierarchical["macro_trend"],
        "counter_trend_flags": hierarchical["counter_trend_flags"],
    }

    # ── Save for dashboard ──
    try:
        _p = os.path.join(os.path.dirname(__file__), "..", "bridges", "signal_bridge", "engine_status.json")
        _p = os.path.normpath(_p)
        # Save MTF-aware version for dashboard
        dashboard_data = {
            "symbol": symbol,
            "price": round(price, 2),
            "timestamp": result["timestamp"],
            "timeframes": {},
            "hierarchical": hierarchical,
        }
        for tf, tr in tf_results.items():
            dashboard_data["timeframes"][tf] = {
                "verdict": tr["verdict"],
                "consensus_pct": tr["consensus_pct"],
                "buy_count": tr["buy_count"],
                "sell_count": tr["sell_count"],
                "total": tr["total"],
                "engines": tr.get("engines", {}),
                "weight": TF_WEIGHTS.get(tf, 0),
            }
            if "macro" in tr:
                dashboard_data["timeframes"][tf]["macro"] = tr["macro"]
            if "structure" in tr:
                dashboard_data["timeframes"][tf]["structure"] = tr["structure"]
            if "entry" in tr:
                dashboard_data["timeframes"][tf]["entry"] = tr["entry"]

        with open(_p, "w") as _f:
            _json.dump(dashboard_data, _f, indent=2)
    except Exception:
        pass

    return result


def get_mtf_pulse_text(symbol: str = "XAUUSD") -> str | None:
    """
    Convenience: fetch MTF data, compute consensus, return formatted pulse text.
    Returns None if data unavailable.
    """
    try:
        result = run_engine_consensus(None, None, symbol)
        if not result:
            return None
        return _format_pulse_text(result)
    except Exception as e:
        logger.error(f"Pulse text error: {e}")
        return None
