"""
Strategy 3: Bear Market Long-Only Strategy
Signal TF: 1h  |  Execution TF: 5m

Logic:
  - Uses regime filter (BTC trend, market breadth, volatility)
  - In weak markets: mostly cash, only enter on oversold rebounds
  - In neutral/bull: trend-filtered participation
  - Key: controlled drawdown, strong Calmar/Sortino

Subtypes combined:
  a) Oversold rebound (RSI < 30 on 1h + reversal on 5m)
  b) Relative strength in weak markets (buy strongest, avoid weakest)
  c) Mostly-cash regime: skip trades when regime is bearish
"""

import numpy as np
import pandas as pd
from data.binance_downloader import resample_ohlcv


def compute_regime(df_1h: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """1h regime detection: bull / neutral / bear."""
    p = params or {}
    ema_fast = p.get("regime_ema_fast", 20)
    ema_slow = p.get("regime_ema_slow", 50)
    vol_lookback = p.get("vol_lookback", 24)

    df = df_1h.copy()

    df["ema_fast"] = df["close"].ewm(span=ema_fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=ema_slow, adjust=False).mean()

    # RSI for oversold detection
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta).clip(lower=0).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # Rolling market return (proxy for regime)
    df["ret_24h"] = df["close"].pct_change(24)

    # Volatility regime
    df["vol_24h"] = df["close"].pct_change().rolling(vol_lookback).std()
    df["vol_ma"] = df["vol_24h"].rolling(vol_lookback * 3).mean()
    df["high_vol"] = (df["vol_24h"] > df["vol_ma"] * 1.5).astype(int)

    # Regime classification
    # Bull: fast > slow, positive returns
    # Bear: fast < slow, negative returns
    # Neutral: otherwise
    df["regime"] = "neutral"
    df.loc[
        (df["ema_fast"] > df["ema_slow"]) & (df["ret_24h"] > 0),
        "regime"
    ] = "bull"
    df.loc[
        (df["ema_fast"] < df["ema_slow"]) & (df["ret_24h"] < -0.01),
        "regime"
    ] = "bear"

    # Oversold signal: RSI < 30 and starting to recover
    df["oversold_1h"] = (
        (df["rsi_14"] < 32)
        & (df["rsi_14"] > df["rsi_14"].shift(1))  # RSI turning up
    ).astype(int)

    # Trend participation signal: bull regime + momentum
    df["trend_1h"] = (
        (df["regime"] == "bull")
        & (df["close"] > df["ema_fast"])
        & (df["rsi_14"] > 40)
        & (df["rsi_14"] < 70)
    ).astype(int)

    return df


def compute_exec_indicators(df_5m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """5m execution indicators."""
    df = df_5m.copy()

    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["roc_5"] = df["close"].pct_change(5)

    # Volume
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / (df["vol_ma"] + 1e-10)

    # RSI for entry timing
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(7).mean()
    loss = (-delta).clip(lower=0).rolling(7).mean()
    rs = gain / (loss + 1e-10)
    df["rsi_7_5m"] = 100 - (100 / (1 + rs))

    return df


def generate_signals(df_1m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Full pipeline: 1m -> 1h regime -> 5m execution.
    Returns 5m DataFrame with entry/exit signals.
    """
    p = params or {}

    df_1h = resample_ohlcv(df_1m, "1h")
    df_1h = compute_regime(df_1h, p)

    df_5m = resample_ohlcv(df_1m, "5m")
    df_5m = compute_exec_indicators(df_5m, p)

    # Map 1h signals to 5m
    sig = df_1h[["timestamp", "regime", "oversold_1h", "trend_1h", "high_vol"]].rename(
        columns={"timestamp": "ts_1h"}
    )
    df_5m = df_5m.sort_values("timestamp")
    sig = sig.sort_values("ts_1h")
    df_5m = pd.merge_asof(
        df_5m, sig,
        left_on="timestamp", right_on="ts_1h",
        direction="backward"
    )

    df_5m["entry_signal"] = 0
    df_5m["exit_signal"] = 0

    # ── Entry logic ──

    # Type A: Oversold rebound (works in bear/neutral markets)
    oversold_entry = (
        (df_5m["oversold_1h"] == 1)
        & (df_5m["rsi_7_5m"] < 40)
        & (df_5m["rsi_7_5m"] > df_5m["rsi_7_5m"].shift(1))  # turning up
        & (df_5m["close"] > df_5m["ema_9"])                   # above fast EMA
        & (df_5m["vol_ratio"] > 0.8)
        & (df_5m["volume"] > 0)
    )

    # Type B: Trend participation (only in bull regime, stricter)
    trend_entry = (
        (df_5m["trend_1h"] == 1)
        & (df_5m["ema_9"] > df_5m["ema_21"])
        & (df_5m["roc_5"] > 0.003)               # stronger momentum requirement
        & (df_5m["rsi_7_5m"] > 45)
        & (df_5m["rsi_7_5m"] < 65)
        & (df_5m["vol_ratio"] > 1.2)              # above-average volume
        & (df_5m["volume"] > 0)
    )

    # DO NOT enter in bear + high vol (mostly cash)
    bear_block = (df_5m["regime"] == "bear") & (df_5m["high_vol"] == 1)

    df_5m.loc[oversold_entry & ~bear_block, "entry_signal"] = 1
    df_5m.loc[trend_entry & ~bear_block, "entry_signal"] = 1

    # ── Exit logic ──
    exit_cond = (
        (df_5m["rsi_7_5m"] > 75)            # overbought
        | (df_5m["close"] < df_5m["ema_21"])  # lost trend
        | (df_5m["roc_5"] < -0.015)           # sharp reversal
    )
    df_5m.loc[exit_cond, "exit_signal"] = 1

    return df_5m


def get_default_config():
    from backtests.engine import BacktestConfig
    return BacktestConfig(
        stop_loss=0.020,          # tighter SL for bear regime
        take_profit=0.035,
        trailing_stop=0.012,
        trailing_activation=0.015,
        time_stop_bars=72,        # 72 × 5m = 6 hours
        cooldown_bars=4,
        max_positions=2,          # conservative
        position_size_pct=0.25,
    )


DEFAULT_PARAMS = {
    "regime_ema_fast": 20,
    "regime_ema_slow": 50,
    "vol_lookback": 24,
}
