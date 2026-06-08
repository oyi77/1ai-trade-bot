"""
Yahoo Finance signal source.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from core import Signal
from core.indicators import ema, rsi, score_trend, classify_signal

LOG = logging.getLogger("signals.yahoo")


def fetch_ohlc(symbol: str, interval: str = "1m", period: str = "2d") -> pd.DataFrame:
    df = yf.download(
        tickers=symbol,
        interval=interval,
        period=period,
        progress=False,
        auto_adjust=True,
        threads=False,
    )
    if df.empty:
        raise ValueError(f"No data returned for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df.dropna()


def generate(symbol: str, interval: str = "1m", period: str = "2d") -> Signal:
    df = fetch_ohlc(symbol, interval, period)
    if len(df) < 60:
        raise ValueError(f"Not enough candles: {len(df)} < 60")

    closes = df["Close"].tolist()
    highs = df["High"].tolist()
    lows = df["Low"].tolist()
    price = float(closes[-1])

    score, reasons = score_trend(closes, highs, lows, mode="binary")
    return classify_signal(score, price, reasons, symbol, source="yahoo")
