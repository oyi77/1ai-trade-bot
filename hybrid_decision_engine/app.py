"""
Hybrid Decision Engine — FastAPI Server
========================================
Port: 8770 (configurable via HYBRID_PORT env)

Phase 1 Endpoints:
  GET  /health                  — Service health + uptime
  GET  /api/get-data            — Fetch OHLCV (Bridge → ccxt → yfinance)
  POST /api/run-analysis       — Run parallel analyzer pipeline (Phase 2)
  GET  /api/cache/stats         — Cache statistics

Future Endpoints (Phase 3-4):
  POST /api/signal              — Get final decision + broadcast
  GET  /api/signals/history     — Recent generated signals

Architecture:
  This service runs INDEPENDENTLY from the existing bridge (:8765)
  and bot handler. If it crashes, existing services are unaffected.
"""
from __future__ import annotations

import logging
import os
import sys
import time
import concurrent.futures
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ── Project path setup ──
ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = ENGINE_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from hybrid_decision_engine import config, __version__, __phase__
from hybrid_decision_engine.data_fetcher import get_ohlcv, get_live_price, get_cache_stats
from hybrid_decision_engine.analyzers import LSTMAnalyzer, ZFCoreAnalyzer, MarketIntegrityAnalyzer

# ── Logging ──
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_DIR / "hybrid_engine.log"),
    ],
)
logger = logging.getLogger("hybrid.api")

WIB = timezone(timedelta(hours=7))
START_TIME = time.time()

# ── Analyzer instances (thread-safe, stateless) ──
_lstm = LSTMAnalyzer()
_zf_core = ZFCoreAnalyzer()
_integrity = MarketIntegrityAnalyzer()


# ═══════════════════════════════════════════════════════════════════
#  PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════

class GetDataRequest(BaseModel):
    """Request for /api/get-data."""
    symbol: str = Field("XAUUSD", description="Trading symbol (XAUUSD, BTCUSD, ETHUSD, USOIL)")
    timeframe: str = Field("M1", description="OHLCV timeframe (M1, M5, M15, H1, H4, D1)")
    limit: int = Field(200, ge=10, le=1000, description="Number of candles to fetch")
    force_refresh: bool = Field(False, description="Bypass cache and fetch fresh data")


class RunAnalysisRequest(BaseModel):
    """Request for /api/run-analysis."""
    symbol: str = Field("XAUUSD", description="Trading symbol")
    timeframe: str = Field("M1", description="Timeframe for analysis")
    limit: int = Field(200, ge=10, le=1000, description="Number of candles to analyze")
    current_price: Optional[float] = Field(None, description="Current market price (auto-fetched if None)")


# ═══════════════════════════════════════════════════════════════════
#  LIFESPAN
# ═══════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    logger.info("🚀 Hybrid Decision Engine starting on %s:%s", config.HOST, config.PORT)
    logger.info("   Data sources: MT5 Bridge → ccxt → yfinance → CSV cache")
    logger.info("   Cache TTL: OHLCV=%ds, Price=%ds", config.OHLCV_CACHE_TTL, config.PRICE_CACHE_TTL)
    yield
    logger.info("🛑 Hybrid Decision Engine shutting down")


# ═══════════════════════════════════════════════════════════════════
#  FASTAPI APP
# ═══════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Vilona Hybrid Decision Engine",
    description="Multi-analyzer signal generation with cross-validation",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ═══════════════════════════════════════════════════════════════════
#  ROUTES — HEALTH
# ═══════════════════════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    """Service health check with uptime and cache stats."""
    uptime = time.time() - START_TIME
    cache = get_cache_stats()
    return {
        "status": "ok",
        "service": "hybrid-decision-engine",
        "version": __version__,
        "phase": __phase__,
        "uptime_seconds": round(uptime, 1),
        "timestamp": datetime.now(WIB).isoformat(),
        "cache": cache,
    }


# ═══════════════════════════════════════════════════════════════════
#  ROUTES — DATA
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/get-data")
async def api_get_data(
    symbol: str = Query("XAUUSD", description="Trading symbol"),
    timeframe: str = Query("M1", description="Timeframe"),
    limit: int = Query(200, ge=10, le=1000, description="Number of candles"),
    force_refresh: bool = Query(False, description="Bypass cache"),
):
    """
    Fetch OHLCV data via fallback chain:
      MT5 Bridge → ccxt → yfinance → CSV cache

    Returns candle data and metadata.
    """
    logger.info("📡 GET /api/get-data: %s %s limit=%d", symbol, timeframe, limit)

    df = get_ohlcv(symbol, timeframe, limit, force_refresh)
    if df is None or df.empty:
        raise HTTPException(
            status_code=503,
            detail=f"All data sources failed for {symbol} {timeframe}. "
                   "Check MT5 Bridge connectivity and network."
        )

    # Also fetch live price for context
    live_price = get_live_price(symbol)

    return {
        "status": "ok",
        "symbol": symbol.upper(),
        "timeframe": timeframe.upper(),
        "candles": len(df),
        "live_price": live_price,
        "first_candle": _row_to_dict(df, 0),
        "last_candle": _row_to_dict(df, -1),
        "data": df.to_dict(orient="records"),
    }


@app.post("/api/get-data")
async def api_get_data_post(req: GetDataRequest):
    """POST variant of /api/get-data (for complex queries)."""
    return await api_get_data(
        symbol=req.symbol,
        timeframe=req.timeframe,
        limit=req.limit,
        force_refresh=req.force_refresh,
    )


# ═══════════════════════════════════════════════════════════════════
#  ROUTES — ANALYSIS (Phase 2 — placeholder now)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/run-analysis")
async def api_run_analysis(req: RunAnalysisRequest):
    """
    Run the full parallel analysis pipeline:
      1. LSTM Analysis (Prediction, Swing S/R, Trend)
      2. ZF-Core Analysis (ZF-Score 68/32, Position)
      3. Market Integrity (Liquidity Void, Spoofing, Risk)
      → Hybrid Signal Generator → Final Decision
    """
    logger.info("🔬 POST /api/run-analysis: %s %s", req.symbol, req.timeframe)
    pipeline_start = time.time()

    # Fetch data first
    df = get_ohlcv(req.symbol, req.timeframe, req.limit)
    if df is None or df.empty:
        raise HTTPException(status_code=503, detail=f"No data available for {req.symbol}")

    current_price = req.current_price or get_live_price(req.symbol)

    # ── Parallel analyzer execution with timeout circuit breaker ──
    analyzer_results = {}
    analyzer_meta = {}
    analyzers = [
        ("lstm", _lstm, config.TIMEOUT_LSTM),
        ("zf_core", _zf_core, config.TIMEOUT_ZF),
        ("integrity", _integrity, config.TIMEOUT_INTEGRITY),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="analyzer") as pool:
        future_map = {}
        for name, analyzer, timeout in analyzers:
            t0 = time.time()
            future = pool.submit(analyzer.safe_analyze, df, req.symbol, current_price=current_price)
            future_map[future] = (name, timeout, t0)

        for future in concurrent.futures.as_completed(future_map, timeout=config.TIMEOUT_TOTAL_PIPELINE):
            name, timeout, t0 = future_map[future]
            elapsed = (time.time() - t0) * 1000
            try:
                result = future.result(timeout=timeout)
                analyzer_results[name] = result
                analyzer_meta[name] = {
                    "success": result.success,
                    "action": result.action,
                    "confidence": result.confidence,
                    "blocked": result.blocked,
                    "execution_time_ms": round(elapsed, 1),
                    "error": result.error if not result.success else None,
                }
                if result.success:
                    logger.info("  ✅ %s: %s (conf=%.2f, %.0fms)", name, result.action, result.confidence, elapsed)
                else:
                    logger.warning("  ❌ %s: CRASH — %s (%.0fms)", name, result.error, elapsed)
            except concurrent.futures.TimeoutError:
                logger.warning("  ⏰ %s: TIMEOUT after %.0fms", name, elapsed)
                analyzer_results[name] = None
                analyzer_meta[name] = {"success": False, "action": None, "confidence": 0, "blocked": False, "execution_time_ms": round(elapsed, 1), "error": f"TIMEOUT after {timeout}s"}
            except Exception as e:
                logger.error("  💥 %s: UNEXPECTED — %s (%.0fms)", name, e, elapsed)
                analyzer_results[name] = None
                analyzer_meta[name] = {"success": False, "action": None, "confidence": 0, "blocked": False, "execution_time_ms": round(elapsed, 1), "error": str(e)}

    # ── Hybrid Signal Generator (cross-validation) ──
    decision = _hybrid_cross_validate(analyzer_results, current_price)
    pipeline_elapsed = (time.time() - pipeline_start) * 1000

    return {
        "status": "ok",
        "symbol": req.symbol.upper(),
        "timeframe": req.timeframe.upper(),
        "candles_available": len(df),
        "current_price": current_price,
        "analyzers": analyzer_meta,
        "hybrid_decision": decision,
        "pipeline_ms": round(pipeline_elapsed, 1),
        "timestamp": datetime.now(WIB).isoformat(),
    }


def _hybrid_cross_validate(results: dict, current_price: Optional[float] = None) -> dict:
    """Cross-validate analyzer outputs and produce final decision."""
    lstm = results.get("lstm")
    zf = results.get("zf_core")
    integrity = results.get("integrity")

    # Rule 1: Market Integrity BLOCK → no signal
    if integrity and integrity.blocked:
        return {
            "signal": None,
            "confidence": 0,
            "reasoning": f"⛔ INTEGRITY BLOCK: {integrity.block_reason}",
            "mode": "BLOCKED",
        }

    # Rule 2: Integrity WARN → reduce confidence of any signal
    integrity_penalty = 0.0
    if integrity and integrity.action == "WARN":
        integrity_penalty = 0.2

    # Rule 3: Both LSTM and ZF-Core present → cross-validate
    if lstm and lstm.success and zf and zf.success:
        if lstm.action == zf.action and lstm.action in ("BUY", "SELL"):
            # Agreement → strong signal
            avg_conf = (lstm.confidence + zf.confidence) / 2
            boosted = min(avg_conf * config.SOLO_CONFIDENCE_BOOST, 0.95)
            boosted = max(boosted - integrity_penalty, 0.0)

            sl = lstm.metadata.get("sl") or zf.metadata.get("sl")
            tp = lstm.metadata.get("tp") or zf.metadata.get("tp")

            return {
                "signal": lstm.action,
                "confidence": round(boosted, 4),
                "reasoning": f"DUAL CONSENSUS: LSTM({lstm.confidence:.2f}) ∩ ZF({zf.confidence:.2f}) agree → {lstm.action}",
                "mode": "DUAL",
                "sl": sl,
                "tp": tp,
                "lstm_confidence": lstm.confidence,
                "zf_confidence": zf.confidence,
            }
        else:
            # Disagreement → no signal
            return {
                "signal": None,
                "confidence": 0,
                "reasoning": f"CONFLICT: LSTM={lstm.action}({lstm.confidence:.2f}) vs ZF={zf.action}({zf.confidence:.2f})",
                "mode": "CONFLICT",
            }

    # Rule 4: Solo mode (one analyzer failed/crashed) — strict threshold
    solo = None
    if lstm and lstm.success and lstm.action in ("BUY", "SELL"):
        solo = lstm
    elif zf and zf.success and zf.action in ("BUY", "SELL"):
        solo = zf

    if solo:
        if solo.confidence >= config.MIN_CONFIDENCE_SOLO:
            final_conf = max(solo.confidence - integrity_penalty, 0.0)
            return {
                "signal": solo.action,
                "confidence": round(final_conf, 4),
                "reasoning": f"SOLO {solo.analyzer.upper()}: {solo.action} (conf={solo.confidence:.2f}, min={config.MIN_CONFIDENCE_SOLO})",
                "mode": "SOLO",
                "sl": solo.metadata.get("sl"),
                "tp": solo.metadata.get("tp"),
            }
        else:
            return {
                "signal": None,
                "confidence": 0,
                "reasoning": f"SOLO {solo.analyzer.upper()} BLOCKED: {solo.action} confidence {solo.confidence:.2f} < {config.MIN_CONFIDENCE_SOLO} minimum",
                "mode": "SOLO_BLOCKED",
            }

    # Rule 5: No valid analyzer output
    return {
        "signal": None,
        "confidence": 0,
        "reasoning": "NO SIGNAL: insufficient analyzer output",
        "mode": "NO_DATA",
    }


# ═══════════════════════════════════════════════════════════════════
#  ROUTES — CACHE & MONITORING
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/cache/stats")
async def api_cache_stats():
    """Return cache statistics for monitoring."""
    return get_cache_stats()


@app.get("/api/price")
async def api_price(symbol: str = Query("XAUUSD")):
    """Get current live price for a symbol."""
    price = get_live_price(symbol)
    if price is None:
        raise HTTPException(status_code=503, detail=f"Price unavailable for {symbol}")
    return {
        "symbol": symbol.upper(),
        "price": price,
        "timestamp": datetime.now(WIB).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

def _row_to_dict(df, idx) -> dict:
    """Extract a single row from DataFrame as dict."""
    try:
        row = df.iloc[idx]
        return {k: (v.item() if hasattr(v, "item") else v) for k, v in row.to_dict().items()}
    except (IndexError, KeyError):
        return {}


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "hybrid_decision_engine.app:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
        log_level=config.LOG_LEVEL.lower(),
        access_log=True,
    )
