"""
Strategy 2: Momentum + Minute-Level Scalping (LONG-ONLY)
Signal TF: 5m  |  Execution TF: 1m

Logic:
  - 5m: detect positive momentum with aligned EMAs + higher lows
  - 1m: enter on continuation, exit on momentum fade
  - Hold 3m to 30m, small TP above fee hurdle
  - Key: must beat 0.10% round-trip fees
"""

import numpy as np
import pandas as pd
from data.binance_downloader import resample_ohlcv


def compute_signal_indicators(df_5m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """5m trend/momentum indicators."""
    p = params or {}

    df = df_5m.copy()
    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()

    df["ema_slope"] = df["ema_5"].pct_change(3)

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(7).mean()
    loss = (-delta).clip(lower=0).rolling(7).mean()
    rs = gain / (loss + 1e-10)
    df["rsi_7"] = 100 - (100 / (1 + rs))

    # Volume
    df["vol_ma"] = df["volume"].rolling(20).mean()
    df["vol_ratio_5m"] = df["volume"] / (df["vol_ma"] + 1e-10)

    # Higher low structure
    df["higher_low"] = (df["low"].shift(1) > df["low"].shift(2)).astype(int)

    # Momentum signal: aligned trend + slope + structure
    df["mom_signal_5m"] = (
        (df["ema_5"] > df["ema_13"])
        & (df["ema_13"] > df["ema_21"])
        & (df["ema_slope"] > 0)
        & (df["higher_low"] == 1)
        & (df["rsi_7"] > 40)
        & (df["rsi_7"] < 72)
        & (df["vol_ratio_5m"] > 0.7)
    ).astype(int)

    return df


def compute_exec_indicators(df_1m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """1m execution indicators."""
    df = df_1m.copy()
    df["ema_3"] = df["close"].ewm(span=3, adjust=False).mean()
    df["ema_8"] = df["close"].ewm(span=8, adjust=False).mean()
    df["ret_1"] = df["close"].pct_change(1)
    df["ret_3"] = df["close"].pct_change(3)

    # ATR for volatility filter
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(10).mean() / df["close"]

    df["vol_ma_1m"] = df["volume"].rolling(20).mean()
    df["vol_ratio_1m"] = df["volume"] / (df["vol_ma_1m"] + 1e-10)

    return df


def generate_signals(df_1m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Full pipeline: 1m -> 5m signals -> 1m execution.
    """
    p = params or {}
    fee_hurdle = p.get("fee_hurdle", 0.0012)

    df_5m = resample_ohlcv(df_1m, "5m")
    df_5m = compute_signal_indicators(df_5m, p)

    df_exec = compute_exec_indicators(df_1m.copy(), p)

    # Map 5m signal to 1m
    sig = df_5m[["timestamp", "mom_signal_5m"]].rename(
        columns={"timestamp": "ts_5m", "mom_signal_5m": "sig_5m"}
    )
    df_exec = df_exec.sort_values("timestamp")
    sig = sig.sort_values("ts_5m")
    df_exec = pd.merge_asof(
        df_exec, sig,
        left_on="timestamp", right_on="ts_5m",
        direction="backward"
    )
    df_exec["sig_5m"] = df_exec["sig_5m"].fillna(0).astype(int)

    df_exec["entry_signal"] = 0
    df_exec["exit_signal"] = 0

    # Entry: 5m momentum confirmed + 1m continuation
    # Stricter to avoid overtrading (fee drag is the #1 enemy for scalping)
    atr_min = p.get("atr_min", 0.001)
    atr_max = p.get("atr_max", 0.010)

    entry = (
        (df_exec["sig_5m"] == 1)
        & (df_exec["ret_1"] > 0.0003)          # stronger positive tick
        & (df_exec["ret_3"] > 0.001)            # 3-bar momentum > fee hurdle
        & (df_exec["ema_3"] > df_exec["ema_8"])
        & (df_exec["atr_pct"] > atr_min)
        & (df_exec["atr_pct"] < atr_max)
        & (df_exec["vol_ratio_1m"] > 1.0)       # above-average volume
        & (df_exec["volume"] > 0)
    )
    df_exec.loc[entry, "entry_signal"] = 1

    # Exit: momentum fade or reversal
    exit_cond = (
        (df_exec["ret_3"] < -fee_hurdle)
        | (df_exec["ema_3"] < df_exec["ema_8"])
    )
    df_exec.loc[exit_cond, "exit_signal"] = 1

    return df_exec


def get_default_config():
    from backtests.engine import BacktestConfig
    return BacktestConfig(
        stop_loss=0.015,          # 1.5% SL
        take_profit=0.020,        # 2% TP (better risk/reward)
        trailing_stop=0.006,
        trailing_activation=0.010,
        time_stop_bars=30,        # 30 × 1m = 30 min max hold
        cooldown_bars=5,          # more cooldown to reduce overtrading
        max_positions=2,
        position_size_pct=0.30,
    )


DEFAULT_PARAMS = {
    "fee_hurdle": 0.0012,
    "atr_min": 0.0008,
    "atr_max": 0.012,
}
