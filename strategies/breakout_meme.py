"""
Strategy 1B: Breakout for Meme Coins (LONG-ONLY)
Signal TF: 15m  |  Execution TF: 1m

Logic:
  - 15m: detect short consolidation breakout with volume burst
  - 1m: confirm momentum and enter
  - Tighter time stops (meme moves are fast)
  - Quick TP, fast failed-breakout exit
"""

import numpy as np
import pandas as pd
from data.binance_downloader import resample_ohlcv


def compute_signal_indicators(df_15m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """15m signal indicators for meme breakout."""
    p = params or {}
    lookback = p.get("lookback", 10)
    ema_fast = p.get("sig_ema_fast", 8)
    ema_slow = p.get("sig_ema_slow", 21)

    df = df_15m.copy()

    df["rolling_high"] = df["high"].rolling(lookback).max()
    df["prev_high"] = df["rolling_high"].shift(1)
    df["rolling_low"] = df["low"].rolling(lookback).min()
    df["consolidation"] = (df["rolling_high"] - df["rolling_low"]) / (df["close"] + 1e-10)

    df["ema_fast"] = df["close"].ewm(span=ema_fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=ema_slow, adjust=False).mean()

    # Volume burst on 15m
    df["vol_ma"] = df["volume"].rolling(15).mean()
    df["vol_ratio_15m"] = df["volume"] / (df["vol_ma"] + 1e-10)

    # Breakout from consolidation (relaxed for meme coins)
    df["breakout_15m"] = (
        (df["close"] > df["prev_high"])
        & (df["consolidation"].shift(1) < 0.10)  # wider consolidation ok for meme
        & (df["vol_ratio_15m"] > 1.5)             # lower volume threshold
        & (df["ema_fast"] > df["ema_slow"])
    ).astype(int)

    return df


def compute_exec_indicators(df_1m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """1m execution indicators."""
    p = params or {}

    df = df_1m.copy()
    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_13"] = df["close"].ewm(span=13, adjust=False).mean()

    df["vol_ma_1m"] = df["volume"].rolling(20).mean()
    df["vol_ratio_1m"] = df["volume"] / (df["vol_ma_1m"] + 1e-10)

    df["roc_3"] = df["close"].pct_change(3)
    df["roc_5"] = df["close"].pct_change(5)
    df["accel"] = df["roc_3"] - df["roc_3"].shift(3)

    return df


def generate_signals(df_1m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Full pipeline: 1m data -> 15m signals -> 1m execution.
    Returns 1m DataFrame with entry_signal / exit_signal.
    """
    p = params or {}

    # Signal on 15m
    df_15m = resample_ohlcv(df_1m, "15m")
    df_15m = compute_signal_indicators(df_15m, p)

    # Execution on 1m
    df_exec = compute_exec_indicators(df_1m.copy(), p)

    # Map 15m signal to 1m
    sig = df_15m[["timestamp", "breakout_15m"]].rename(
        columns={"timestamp": "ts_15m", "breakout_15m": "sig_15m"}
    )
    df_exec = df_exec.sort_values("timestamp")
    sig = sig.sort_values("ts_15m")
    df_exec = pd.merge_asof(
        df_exec, sig,
        left_on="timestamp", right_on="ts_15m",
        direction="backward"
    )
    df_exec["sig_15m"] = df_exec["sig_15m"].fillna(0).astype(int)

    df_exec["entry_signal"] = 0
    df_exec["exit_signal"] = 0

    # Entry: 15m breakout + 1m momentum confirmation (relaxed)
    entry = (
        (df_exec["sig_15m"] == 1)
        & (df_exec["roc_3"] > 0.001)
        & (df_exec["ema_5"] > df_exec["ema_13"])
        & (df_exec["vol_ratio_1m"] > 0.7)
        & (df_exec["volume"] > 0)
    )
    df_exec.loc[entry, "entry_signal"] = 1

    # Exit: momentum fade
    exit_cond = (
        (df_exec["roc_3"] < -0.005)
        | (df_exec["close"] < df_exec["ema_5"])
        | (df_exec["vol_ratio_1m"] < 0.4)
    )
    df_exec.loc[exit_cond, "exit_signal"] = 1

    return df_exec


def get_default_config():
    from backtests.engine import BacktestConfig
    return BacktestConfig(
        stop_loss=0.035,
        take_profit=0.07,
        trailing_stop=0.02,
        trailing_activation=0.025,
        time_stop_bars=30,    # 30 × 1m = 30 min
        cooldown_bars=5,
        max_positions=2,
        position_size_pct=0.25,
    )


DEFAULT_PARAMS = {
    "lookback": 10,
    "sig_ema_fast": 8,
    "sig_ema_slow": 21,
}
