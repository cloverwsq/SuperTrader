"""
Strategy 1B: Aggressive Breakout + Momentum for Meme Coins (LONG-ONLY)
Signal TF: 15m  |  Execution TF: 1m

Optimized for >20% return in 10-day window on high-vol meme coins.

Logic:
  - 15m: detect breakouts (consolidation break OR momentum surge OR volume spike)
  - 1m: light confirmation, fast entry
  - Wider TP, trailing stops to let winners run
  - Multiple signal types to generate more trades
"""

import numpy as np
import pandas as pd
from data.binance_downloader import resample_ohlcv


def compute_signal_indicators(df_15m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """15m signal indicators — multi-signal approach for meme coins."""
    p = params or {}
    lookback = p.get("lookback", 8)
    ema_fast = p.get("sig_ema_fast", 5)
    ema_slow = p.get("sig_ema_slow", 13)

    df = df_15m.copy()

    # Core price structure
    df["rolling_high"] = df["high"].rolling(lookback).max()
    df["prev_high"] = df["rolling_high"].shift(1)
    df["rolling_low"] = df["low"].rolling(lookback).min()
    df["consolidation"] = (df["rolling_high"] - df["rolling_low"]) / (df["close"] + 1e-10)

    # Trend EMAs
    df["ema_fast"] = df["close"].ewm(span=ema_fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=ema_slow, adjust=False).mean()

    # Volume analysis
    df["vol_ma"] = df["volume"].rolling(10).mean()
    df["vol_ratio_15m"] = df["volume"] / (df["vol_ma"] + 1e-10)

    # Momentum indicators on 15m
    df["roc_2_15m"] = df["close"].pct_change(2)
    df["roc_4_15m"] = df["close"].pct_change(4)

    # RSI for oversold bounces
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(7).mean()
    loss = (-delta.clip(upper=0)).rolling(7).mean()
    rs = gain / (loss + 1e-10)
    df["rsi_7"] = 100 - (100 / (1 + rs))

    # Signal 1: Classic breakout from consolidation
    df["sig_breakout"] = (
        (df["close"] > df["prev_high"])
        & (df["consolidation"].shift(1) < 0.15)
        & (df["vol_ratio_15m"] > 1.2)
    ).astype(int)

    # Signal 2: Momentum surge (strong candle + volume)
    df["sig_momentum"] = (
        (df["roc_2_15m"] > 0.005)
        & (df["vol_ratio_15m"] > 1.5)
        & (df["close"] > df["ema_fast"])
    ).astype(int)

    # Signal 3: EMA crossover with volume
    df["ema_cross"] = (
        (df["ema_fast"] > df["ema_slow"])
        & (df["ema_fast"].shift(1) <= df["ema_slow"].shift(1))
        & (df["vol_ratio_15m"] > 1.0)
    ).astype(int)

    # Signal 4: RSI bounce from oversold with upward momentum
    df["sig_rsi_bounce"] = (
        (df["rsi_7"] > 35)
        & (df["rsi_7"].shift(1) <= 35)
        & (df["roc_2_15m"] > 0)
    ).astype(int)

    # Combine: any signal fires
    df["breakout_15m"] = (
        (df["sig_breakout"] == 1)
        | (df["sig_momentum"] == 1)
        | (df["ema_cross"] == 1)
        | (df["sig_rsi_bounce"] == 1)
    ).astype(int)

    return df


def compute_exec_indicators(df_1m: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """1m execution indicators — lightweight for fast entry."""
    p = params or {}

    df = df_1m.copy()
    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()

    df["vol_ma_1m"] = df["volume"].rolling(15).mean()
    df["vol_ratio_1m"] = df["volume"] / (df["vol_ma_1m"] + 1e-10)

    df["roc_3"] = df["close"].pct_change(3)
    df["roc_5"] = df["close"].pct_change(5)
    df["accel"] = df["roc_3"] - df["roc_3"].shift(3)

    # ATR for adaptive exits
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr_10"] = tr.rolling(10).mean()

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

    # Entry: 15m signal + minimal 1m confirmation
    # Just need price above short EMA and positive momentum
    entry = (
        (df_exec["sig_15m"] == 1)
        & (df_exec["roc_3"] > 0.0005)
        & (df_exec["close"] > df_exec["ema_5"])
        & (df_exec["volume"] > 0)
    )
    df_exec.loc[entry, "entry_signal"] = 1

    # Exit: only on clear momentum reversal (not minor dips)
    exit_cond = (
        (df_exec["roc_5"] < -0.008)
        | ((df_exec["close"] < df_exec["ema_13"]) & (df_exec["roc_3"] < -0.003))
    )
    df_exec.loc[exit_cond, "exit_signal"] = 1

    return df_exec


def get_default_config():
    from backtests.engine import BacktestConfig
    return BacktestConfig(
        stop_loss=0.04,           # 4% stop — wider to avoid noise
        take_profit=0.12,         # 12% TP — let meme runners run
        trailing_stop=0.025,      # 2.5% trail
        trailing_activation=0.03, # activate trail after 3% gain
        time_stop_bars=45,        # 45 × 1m = 45 min max hold
        cooldown_bars=3,
        max_positions=3,          # more capital deployed
        position_size_pct=0.40,   # 40% per position
    )


DEFAULT_PARAMS = {
    "lookback": 8,
    "sig_ema_fast": 5,
    "sig_ema_slow": 13,
}
