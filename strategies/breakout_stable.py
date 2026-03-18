"""
Strategy 1A: Breakout for Stable/Liquid Coins (LONG-ONLY)
Signal TF: 30m  |  Execution TF: 5m

Logic:
  - 30m: detect breakout above rolling high
  - 5m: confirm with volume surge + EMA trend alignment
  - Enter long on 5m bar after 30m breakout confirmed
  - Exit: TP / trailing stop / time stop / failed breakout
"""

import numpy as np
import pandas as pd
from data.binance_downloader import resample_ohlcv


def compute_signal_indicators(df_30m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Compute 30m signal-level indicators."""
    p = params or {}
    lookback = p.get("lookback", 20)
    ema_trend = p.get("ema_trend", 50)

    df = df_30m.copy()
    df["rolling_high"] = df["high"].rolling(lookback).max()
    df["prev_high"] = df["rolling_high"].shift(1)
    df["ema_trend"] = df["close"].ewm(span=ema_trend, adjust=False).mean()

    # Breakout: close breaks above previous rolling high
    df["breakout_30m"] = (
        (df["close"] > df["prev_high"])
        & (df["close"].shift(1) <= df["prev_high"].shift(1))
        & (df["close"] > df["ema_trend"])  # must be above long trend
    ).astype(int)

    return df


def compute_exec_indicators(df_5m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Compute 5m execution-level indicators."""
    p = params or {}
    ema_fast = p.get("ema_fast", 9)
    ema_slow = p.get("ema_slow", 21)
    atr_period = p.get("atr_period", 14)
    vol_ma_period = p.get("vol_ma_period", 20)

    df = df_5m.copy()

    df["ema_fast"] = df["close"].ewm(span=ema_fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=ema_slow, adjust=False).mean()

    # ATR
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.rolling(atr_period).mean()
    df["atr_pct"] = df["atr"] / df["close"]

    # Volume
    df["vol_ma"] = df["volume"].rolling(vol_ma_period).mean()
    df["vol_ratio"] = df["volume"] / (df["vol_ma"] + 1e-10)

    # Momentum
    df["roc_5"] = df["close"].pct_change(5)

    return df


def generate_signals(df_1m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Full signal generation pipeline.
    Input: 1m OHLCV DataFrame with 'timestamp' column.
    Output: 5m DataFrame with entry_signal / exit_signal columns.
    """
    p = params or {}

    # Resample 1m -> 30m for signals
    df_30m = resample_ohlcv(df_1m, "30m")
    df_30m = compute_signal_indicators(df_30m, p)

    # Resample 1m -> 5m for execution
    df_5m = resample_ohlcv(df_1m, "5m")
    df_5m = compute_exec_indicators(df_5m, p)

    # Map 30m breakout signal down to 5m
    # Each 5m bar maps to a 30m bar; forward-fill the signal
    df_30m_sig = df_30m[["timestamp", "breakout_30m"]].rename(
        columns={"timestamp": "ts_30m", "breakout_30m": "sig_30m"}
    )
    df_5m["ts_30m"] = df_5m["timestamp"].dt.floor("30min")

    # Use merge_asof to get the latest 30m signal at each 5m bar
    df_5m = df_5m.sort_values("timestamp")
    df_30m_sig = df_30m_sig.sort_values("ts_30m")
    df_5m = pd.merge_asof(
        df_5m, df_30m_sig,
        left_on="timestamp", right_on="ts_30m",
        direction="backward"
    )
    df_5m["sig_30m"] = df_5m["sig_30m"].fillna(0).astype(int)

    # Generate entry: 30m breakout + 5m confirmation
    vol_threshold = p.get("vol_threshold", 1.3)
    atr_min = p.get("atr_min", 0.001)
    atr_max = p.get("atr_max", 0.025)

    df_5m["entry_signal"] = 0
    df_5m["exit_signal"] = 0

    entry = (
        (df_5m["sig_30m"] == 1)
        & (df_5m["ema_fast"] > df_5m["ema_slow"])
        & (df_5m["vol_ratio"] > vol_threshold)
        & (df_5m["atr_pct"] > atr_min)
        & (df_5m["atr_pct"] < atr_max)
        & (df_5m["roc_5"] > 0)
        & (df_5m["volume"] > 0)
    )
    df_5m.loc[entry, "entry_signal"] = 1

    # Exit signal: momentum reversal or trend loss
    exit_cond = (
        (df_5m["close"] < df_5m["ema_slow"])
        | (df_5m["roc_5"] < -0.012)
    )
    df_5m.loc[exit_cond, "exit_signal"] = 1

    return df_5m


def get_default_config():
    from backtests.engine import BacktestConfig
    return BacktestConfig(
        stop_loss=0.025,
        take_profit=0.05,
        trailing_stop=0.015,
        trailing_activation=0.02,
        time_stop_bars=60,    # 60 × 5m = 5 hours
        cooldown_bars=3,
        max_positions=3,
        position_size_pct=0.30,
    )


DEFAULT_PARAMS = {
    "lookback": 20,
    "ema_trend": 50,
    "ema_fast": 9,
    "ema_slow": 21,
    "atr_period": 14,
    "vol_ma_period": 20,
    "vol_threshold": 1.3,
    "atr_min": 0.001,
    "atr_max": 0.025,
}
