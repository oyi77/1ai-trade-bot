from unittest.mock import patch

import pandas as pd

from tradebot.analytics.learning import (
    format_learning_report,
    learn_from_sl_stats,
    learn_from_tp_stats,
    record_trade_outcome,
)


def test_record_trade_outcome():
    trade = {
        "outcome": "SL_HIT",
        "symbol": "XAUUSD",
        "action": "BUY",
        "entry": 2000.0,
        "sl": 1998.0,
        "confidence": 35,
    }

    with (
        patch("tradebot.analytics.learning._save_lessons") as mock_save,
        patch("tradebot.analytics.learning._load_lessons", return_value={"lessons": []}),
    ):
        lesson = record_trade_outcome(trade, current_price=1995.0)
        assert lesson["type"] == "SL_HIT"
        assert lesson["analysis"]["severity"] == "high"

        mock_save.assert_called_once()


def test_learn_from_tp_stats():
    df = pd.DataFrame(
        [
            {"market_regime": "trending", "tp_pips": 50, "mfe_pips": 100},
            {"market_regime": "trending", "tp_pips": 40, "mfe_pips": 80},
        ]
    )
    res = learn_from_tp_stats(df)
    assert res["trending"]["count"] == 2
    assert res["trending"]["mean_tp_pips"] == 45.0
    assert res["trending"]["mean_mfe_pips"] == 90.0


def test_learn_from_sl_stats():
    df = pd.DataFrame(
        [
            {"market_regime": "ranging", "had_mfe": True},
            {"market_regime": "ranging", "had_mfe": False},
        ]
    )
    res = learn_from_sl_stats(df)
    assert res["ranging"]["count"] == 2
    assert res["ranging"]["reversal_rate"] == 0.5


def test_format_learning_report():
    res = {
        "total_signals": 10,
        "tp_stats": {"trending": {"count": 6, "mean_tp_pips": 40}},
        "sl_stats": {"trending": {"count": 4}},
    }
    out = format_learning_report(res)
    assert "10 Closed Signals" in out
    assert "WIN RATE: 60%" in out
    assert "[trending] 6W" in out
