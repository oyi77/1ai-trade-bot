"""
pattern_extractor.py — Phase 2 Autonomous Learning Engine for Hermes Bot.

Reads closed signals (TP_HIT / SL_HIT) from ml_feedback_loop in SQLite,
performs per-regime statistical analysis, and outputs a JSON dictionary of
mathematically-backed suggested scoring weights.

Design: pandas vectorization + statistical heuristics. No heavy ML libs.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("hermes.pattern-extractor")

WIB = timezone(timedelta(hours=7))

# ── Default base weights per regime (pre-learning) ──
DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "trending":  {"smc": 0.50, "liq": 0.20, "macro": 0.30},
    "ranging":   {"smc": 0.30, "liq": 0.45, "macro": 0.25},
    "volatile":   {"smc": 0.20, "liq": 0.30, "macro": 0.50},
    "unknown":   {"smc": 0.40, "liq": 0.30, "macro": 0.30},
}


_TRADE_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "trade_history.json"

# ── In-memory cache so multiple calls in same run don't re-read JSON ──
_cache: dict | None = None


def _load_trade_history() -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache.get("trades", [])
    try:
        if _TRADE_HISTORY_PATH.exists():
            with open(_TRADE_HISTORY_PATH) as f:
                _cache = json.load(f)
            return _cache.get("trades", [])
    except Exception as exc:
        logger.warning("Failed to load trade_history.json: %s", exc)
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_historical_data(
    db_path: str,  # kept for backward compat; no longer primary source
    lookback_days: int = 14,
) -> pd.DataFrame:
    """Query closed signals from trade_history.json, with ml_feedback_loop fallback.

    trade_history.json has ALL trades logged by the handler (daily recap data).
    ml_feedback_loop table may have richer per-signal scores but is often sparse
    because it's written only by the ML feedback loop, not by every signal.

    Strategy:
      1) Try trade_history.json first (most complete).
      2) If JSON has 0 trades, fall back to ml_feedback_loop.
    """
    cutoff_date = (datetime.now(WIB) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    # ── 1. Try trade_history.json ──
    trades = _load_trade_history()
    closed = [
        t for t in trades
        if t.get("outcome") in ("TP_HIT", "SL_HIT")
        and (t.get("open_time") or "")[:10] >= cutoff_date
    ]

    if closed:
        logger.info("Fetched %d closed signals from trade_history.json", len(closed))
        df = _trades_to_df(closed)
        if not df.empty:
            return df

    # ── 2. Fallback to ml_feedback_loop ──
    logger.info("Falling back to ml_feedback_loop...")
    return _fetch_sqlite(db_path, cutoff_date)


def _trades_to_df(trades: list[dict]) -> pd.DataFrame:
    """Convert trade_history.json records → DataFrame matching ml_feedback schema."""
    rows = []
    for t in trades:
        pair = (t.get("symbol") or "").upper()
        entry = t.get("entry_price") or 0.0
        sl = t.get("sl_price") or 0.0
        tp = t.get("tp_target") or 0.0
        status = t.get("outcome", "TP_HIT")
        pips = float(t.get("pips", 0))
        # Infer regime from pips volatility, direction etc.
        # For now we default to 'unknown' and let the analysis class by size
        if abs(pips) >= 60:
            regime = "trending"
        elif abs(pips) >= 30:
            regime = "ranging"
        elif abs(pips) > 0:
            regime = "volatile"
        else:
            regime = "unknown"

        rows.append({
            "signal_id": t.get("signal_id") or f"th_{hash(t.get('open_time','')) & 0xFFFFFFFF:08x}",
            "timestamp": t.get("open_time", ""),
            "pair": pair,
            "market_regime": regime,
            "score_smc": 0.5,   # default — no raw score in JSON
            "score_liquidity": 0.3,
            "score_macro": 0.2,
            "total_score": 0.5,
            "is_broadcasted": 1 if t.get("outcome") else 0,
            "entry_price": entry,
            "sl_price": sl,
            "tp_target": tp,
            "mfe": float(t.get("mfe", 0)) or max(0, pips),
            "mae": float(t.get("mae", 0)) or min(0, pips) if status == "SL_HIT" else 0,
            "status": status,
        })

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["market_regime"] = df["market_regime"].fillna("").replace("", "unknown")
    _add_derived_columns(df)
    return df


def _fetch_sqlite(db_path: str, cutoff_date: str) -> pd.DataFrame:
    """Original SQLite-based data fetch (ml_feedback_loop)."""
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT signal_id, timestamp, pair, market_regime,
                      score_smc, score_liquidity, score_macro, total_score,
                      is_broadcasted, entry_price, sl_price, tp_target,
                      mfe, mae, status
               FROM ml_feedback_loop
               WHERE status IN ('TP_HIT', 'SL_HIT')
                 AND timestamp >= ?""",
            (cutoff_date,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.error("DB query failed on %s: %s", db_path, exc)
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()

    if not rows:
        logger.info("No closed signals in ml_feedback_loop for last 14 days")
        return pd.DataFrame()

    df = pd.DataFrame([dict(r) for r in rows])
    df["market_regime"] = df["market_regime"].fillna("").replace("", "unknown")
    _add_derived_columns(df)
    return df


def _add_derived_columns(df: pd.DataFrame) -> None:
    """Add pips, outcome, and quality flags in-place."""
    entry = df["entry_price"]
    pip_size = np.where(df["pair"].str.upper() == "XAUUSD", 0.10, 1.0)
    df["tp_pips"] = (abs(df["tp_target"] - entry) / pip_size).round(1)
    df["sl_pips"] = (abs(df["sl_price"] - entry) / pip_size).round(1)
    df["mfe_pips"] = (df["mfe"] / pip_size).round(1)
    df["mae_pips"] = (df["mae"].abs() / pip_size).round(1)
    df["had_mfe"] = df["mfe"] > 0
    df["had_mae"] = df["mae"] < 0
    df["outcome"] = np.where(df["status"] == "TP_HIT", 1, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. WIN ANALYSIS — learn_from_tp
# ═══════════════════════════════════════════════════════════════════════════════

def learn_from_tp(df_wins: pd.DataFrame) -> dict:
    """Analyze winning trades grouped by market_regime.

    Args:
        df_wins: DataFrame filtered to status == 'TP_HIT'

    Returns:
        {regime: {
            'count': int,
            'mean_score_smc': float, 'mean_score_liquidity': float,
            'mean_score_macro': float, 'mean_total_score': float,
            'mean_mfe_pips': float, 'mean_tp_pips': float,
            'mfe_efficiency': float,
            'tp_too_conservative': bool,
        }, ...}
    """
    if df_wins.empty:
        return {"_empty": True}

    results: dict = {}
    score_cols = ["score_smc", "score_liquidity", "score_macro", "total_score"]

    for regime, grp in df_wins.groupby("market_regime"):
        n = len(grp)
        means = grp[score_cols].mean().to_dict()

        mfe_pips = grp["mfe_pips"].mean()
        tp_pips = grp["tp_pips"].mean()

        # MFE Efficiency: ratio of (Target Profit Pips) / (MFE Pips)
        # If < 1.0, market ran FURTHER than our TP → leaving money on table
        # If > 1.0, we captured most of the move → TP is well-placed
        if mfe_pips > 0:
            mfe_eff = round(tp_pips / mfe_pips, 3)
        else:
            mfe_eff = 1.0

        # Flag: MFE consistently 2x larger than TP → TP is too conservative
        tp_conservative = mfe_pips >= (tp_pips * 2.0) and n >= 2

        results[regime] = {
            "count": n,
            "mean_score_smc": round(means["score_smc"], 3),
            "mean_score_liquidity": round(means["score_liquidity"], 3),
            "mean_score_macro": round(means["score_macro"], 3),
            "mean_total_score": round(means["total_score"], 3),
            "mean_mfe_pips": round(mfe_pips, 1),
            "mean_tp_pips": round(tp_pips, 1),
            "mfe_efficiency": mfe_eff,
            "tp_too_conservative": tp_conservative,
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 3. LOSS ANALYSIS — learn_from_sl
# ═══════════════════════════════════════════════════════════════════════════════

def learn_from_sl(df_losses: pd.DataFrame) -> dict:
    """Analyze losing trades to find false-positive patterns.

    Args:
        df_losses: DataFrame filtered to status == 'SL_HIT'

    Returns:
        {regime: {
            'count': int,
            'mean_score_smc': float, 'mean_score_liquidity': float,
            'mean_score_macro': float, 'mean_total_score': float,
            'reversal_rate': float,
            'mean_mfe_before_sl': float,
            'need_trailing_stop': bool,
            'false_positive': {'smc': bool, 'liq': bool, 'macro': bool},
        }, ...}
    """
    if df_losses.empty:
        return {"_empty": True}

    results: dict = {}
    score_cols = ["score_smc", "score_liquidity", "score_macro", "total_score"]

    for regime, grp in df_losses.groupby("market_regime"):
        n = len(grp)
        means = grp[score_cols].mean().to_dict()

        # Reversal Rate: % of SL trades where MFE > 0 before stopping out
        # High reversal rate = entry was correct, SL placement or trailing needed
        reversal_rate = round(grp["had_mfe"].mean(), 3) if n > 0 else 0.0

        # Mean MFE before SL (only for those that had positive MFE)
        mfe_positive = grp.loc[grp["had_mfe"], "mfe_pips"]
        mean_mfe_before_sl = round(mfe_positive.mean(), 1) if len(mfe_positive) > 0 else 0.0

        # Need trailing stop if reversal rate > 40% AND avg MFE before SL > 10 pips
        need_trailing = reversal_rate > 0.40 and mean_mfe_before_sl > 10.0

        # False Positive detection: score > 0.7 on average yet trade failed
        fp = {}
        for col in ["score_smc", "score_liquidity", "score_macro"]:
            fp[col.replace("score_", "")] = means[col] > 0.7

        results[regime] = {
            "count": n,
            "mean_score_smc": round(means["score_smc"], 3),
            "mean_score_liquidity": round(means["score_liquidity"], 3),
            "mean_score_macro": round(means["score_macro"], 3),
            "mean_total_score": round(means["total_score"], 3),
            "reversal_rate": reversal_rate,
            "mean_mfe_before_sl": mean_mfe_before_sl,
            "need_trailing_stop": need_trailing,
            "false_positive": fp,
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 4. WEIGHT ADJUSTMENT ENGINE — generate_weight_adjustments
# ═══════════════════════════════════════════════════════════════════════════════

def generate_weight_adjustments(
    tp_stats: dict,
    sl_stats: dict,
) -> dict:
    """Compare TP vs SL stats per regime and produce suggested scoring weights.

    Heuristic:
      - For each component (smc, liq, macro), compute a 'quality ratio':
          Q = mean_tp / max(mean_sl, 0.01)
      - If Q > 1.0, this component is a good predictor → boost weight.
      - If Q < 1.0, this component is noise or false-positive → reduce weight.
      - Normalize weights so they sum to 1.0.

    Args:
        tp_stats: Output from learn_from_tp()
        sl_stats: Output from learn_from_sl()

    Returns:
        {regime: {'smc': weight, 'liq': weight, 'macro': weight}, ...}
        Always includes all regimes from both stats dicts.
    """
    regimes = set(tp_stats.keys()) | set(sl_stats.keys())
    regimes.discard("_empty")

    if not regimes:
        return {"_empty": True}

    adjustments: dict = {}

    for regime in regimes:
        tp = tp_stats.get(regime, {})
        sl = sl_stats.get(regime, {})

        # ── Compute quality ratio per component ──
        quality: dict[str, float] = {}
        for comp, db_col in [("smc", "smc"), ("liq", "liquidity"), ("macro", "macro")]:
            tp_mean = tp.get(f"mean_score_{db_col}", 0)
            sl_mean = sl.get(f"mean_score_{db_col}", 0)
            denominator = max(sl_mean, 0.01)
            quality[comp] = tp_mean / denominator if tp_mean > 0 else 1.0

        # ── Apply false-positive penalty ──
        fp = sl.get("false_positive", {})
        for comp, is_fp in fp.items():
            if is_fp:
                quality[comp] *= 0.5  # halve weight for false-positive components

        # ── Convert quality to raw weights ──
        raw = {comp: max(q, 0.15) for comp, q in quality.items()}  # floor 0.15

        # ── Normalize to sum = 1.0 ──
        total = sum(raw.values())
        if total > 0:
            suggested = {comp: round(v / total, 3) for comp, v in raw.items()}
        else:
            suggested = DEFAULT_WEIGHTS.get(regime, DEFAULT_WEIGHTS["unknown"])

        adjustments[regime] = suggested

    return adjustments


# ═══════════════════════════════════════════════════════════════════════════════
# 5. FULL PIPELINE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_learning_pipeline(
    db_path: str,
    lookback_days: int = 14,
) -> dict:
    """Execute the full Phase 2 pipeline and return structured results.

    Args:
        db_path: Path to members.db
        lookback_days: Analysis window in days

    Returns:
        {
            'tp_stats': {...},
            'sl_stats': {...},
            'suggested_weights': {...},
            'generated_at': str,
            'lookback_days': int,
            'total_signals': int,
        }
    """
    df = fetch_historical_data(db_path, lookback_days)

    if df.empty:
        return {
            "tp_stats": {},
            "sl_stats": {},
            "suggested_weights": {"_empty": True},
            "generated_at": datetime.now(WIB).isoformat(),
            "lookback_days": lookback_days,
            "total_signals": 0,
            "message": f"No closed signals in the last {lookback_days} days.",
        }

    df_wins = df[df["status"] == "TP_HIT"]
    df_losses = df[df["status"] == "SL_HIT"]

    tp_stats = learn_from_tp(df_wins)
    sl_stats = learn_from_sl(df_losses)
    suggested_weights = generate_weight_adjustments(tp_stats, sl_stats)

    return {
        "tp_stats": tp_stats,
        "sl_stats": sl_stats,
        "suggested_weights": suggested_weights,
        "generated_at": datetime.now(WIB).isoformat(),
        "lookback_days": lookback_days,
        "total_signals": len(df),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. MAIN — Demo / CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    # Default path relative to this script
    _script_dir = Path(__file__).resolve().parent.parent
    DB_PATH = str(_script_dir / "data" / "vilona_tradefx" / "members.db")
    LOOKBACK = 14

    print("═" * 56)
    print("  PATTERN EXTRACTOR — Phase 2 Learning Engine")
    print("═" * 56)
    print(f"  DB:   {DB_PATH}")
    print(f"  Days: {LOOKBACK}")
    print("═" * 56)

    # ── Execute pipeline ──
    result = run_learning_pipeline(DB_PATH, LOOKBACK)

    if result["total_signals"] == 0:
        print(f"\n  ⚠️  {result['message']}")
        print("\n  💡 Tip: run /analyze xauusd a few times to generate signals,")
        print("     then wait for them to hit TP or SL. Re-run this script.")
        sys.exit(0)

    # ── Print TP stats ──
    print(f"\n  📊  {result['total_signals']} closed signals analyzed")
    print("  ─── TP STATS ───")
    for regime, data in sorted(result["tp_stats"].items()):
        if regime == "_empty":
            continue
        print(f"    [{regime}] n={data['count']}  "
              f"SMC={data['mean_score_smc']:.2f}  "
              f"LIQ={data['mean_score_liquidity']:.2f}  "
              f"MACRO={data['mean_score_macro']:.2f}  "
              f"MFE={data['mean_mfe_pips']:.0f}p  TP={data['mean_tp_pips']:.0f}p  "
              f"Eff={data['mfe_efficiency']:.2f}"
              + ("  ⚠️ TP CONSERVATIVE" if data.get("tp_too_conservative") else ""))

    # ── Print SL stats ──
    print("  ─── SL STATS ───")
    for regime, data in sorted(result["sl_stats"].items()):
        if regime == "_empty":
            continue
        print(f"    [{regime}] n={data['count']}  "
              f"SMC={data['mean_score_smc']:.2f}  "
              f"LIQ={data['mean_score_liquidity']:.2f}  "
              f"MACRO={data['mean_score_macro']:.2f}  "
              f"Rev={data['reversal_rate']:.0%}  "
              f"MFE_b4_SL={data['mean_mfe_before_sl']:.0f}p"
              + ("  ⚠️ NEED TRAILING" if data.get("need_trailing_stop") else ""))

    # ── Print suggested weights ──
    print("\n  ─── SUGGESTED WEIGHTS ───")
    for regime, weights in sorted(result["suggested_weights"].items()):
        if regime == "_empty":
            continue
        print(f"    [{regime}] SMC={weights['smc']:.2f}  "
              f"LIQ={weights['liq']:.2f}  MACRO={weights['macro']:.2f}")

    # ── Output final JSON ──
    print("\n  ─── RAW JSON ───")
    print(json.dumps(result["suggested_weights"], indent=4))

    # ── Save to disk ──
    out_path = _script_dir / "data" / "vilona_tradefx" / "learning_weights.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"\n  ✅ Saved to {out_path}")
