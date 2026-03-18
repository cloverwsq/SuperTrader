"""
Freqtrade Strategy: Aggressive Breakout + Momentum for Meme Coins (LONG-ONLY)
Signal: 15m multi-signal (breakout, momentum surge, EMA cross, RSI bounce)
Execution: 1m with light confirmation
Timeframe: 1m (with 15m informative)
Optimized for >20% return in 10-day window.
"""

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, informative


class BreakoutMemeFT(IStrategy):
    INTERFACE_VERSION: int = 3
    can_short: bool = False

    timeframe = "1m"

    minimal_roi = {
        "0": 0.12,
        "20": 0.06,
        "45": 0.03,
    }

    stoploss = -0.04
    trailing_stop = True
    trailing_stop_positive = 0.025
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count: int = 200
    use_exit_signal = True

    @informative("15m")
    def populate_indicators_15m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        lookback = 8
        dataframe["rolling_high"] = dataframe["high"].rolling(lookback).max()
        dataframe["prev_high"] = dataframe["rolling_high"].shift(1)
        dataframe["rolling_low"] = dataframe["low"].rolling(lookback).min()
        dataframe["consolidation"] = (
            (dataframe["rolling_high"] - dataframe["rolling_low"]) / (dataframe["close"] + 1e-10)
        )
        dataframe["ema_5"] = dataframe["close"].ewm(span=5, adjust=False).mean()
        dataframe["ema_13"] = dataframe["close"].ewm(span=13, adjust=False).mean()
        dataframe["vol_ma_10"] = dataframe["volume"].rolling(10).mean()
        dataframe["vol_ratio_15m"] = dataframe["volume"] / (dataframe["vol_ma_10"] + 1e-10)
        dataframe["roc_2_15m"] = dataframe["close"].pct_change(2)

        # RSI-7
        delta = dataframe["close"].diff()
        gain = delta.clip(lower=0).rolling(7).mean()
        loss = (-delta.clip(upper=0)).rolling(7).mean()
        rs = gain / (loss + 1e-10)
        dataframe["rsi_7"] = 100 - (100 / (1 + rs))

        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_5"] = dataframe["close"].ewm(span=5, adjust=False).mean()
        dataframe["ema_13"] = dataframe["close"].ewm(span=13, adjust=False).mean()
        dataframe["vol_ma_1m"] = dataframe["volume"].rolling(15).mean()
        dataframe["vol_ratio_1m"] = dataframe["volume"] / (dataframe["vol_ma_1m"] + 1e-10)
        dataframe["roc_3"] = dataframe["close"].pct_change(3)
        dataframe["roc_5"] = dataframe["close"].pct_change(5)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Signal 1: Classic breakout
        sig_breakout = (
            (dataframe["close_15m"] > dataframe["prev_high_15m"])
            & (dataframe["consolidation_15m"].shift(1) < 0.15)
            & (dataframe["vol_ratio_15m_15m"] > 1.2)
        )

        # Signal 2: Momentum surge
        sig_momentum = (
            (dataframe["roc_2_15m_15m"] > 0.005)
            & (dataframe["vol_ratio_15m_15m"] > 1.5)
            & (dataframe["close_15m"] > dataframe["ema_5_15m"])
        )

        # Signal 3: EMA crossover
        sig_ema_cross = (
            (dataframe["ema_5_15m"] > dataframe["ema_13_15m"])
            & (dataframe["ema_5_15m"].shift(1) <= dataframe["ema_13_15m"].shift(1))
            & (dataframe["vol_ratio_15m_15m"] > 1.0)
        )

        # Signal 4: RSI bounce
        sig_rsi = (
            (dataframe["rsi_7_15m"] > 35)
            & (dataframe["rsi_7_15m"].shift(1) <= 35)
            & (dataframe["roc_2_15m_15m"] > 0)
        )

        any_15m_signal = sig_breakout | sig_momentum | sig_ema_cross | sig_rsi

        # 1m confirmation: minimal
        entry = (
            any_15m_signal
            & (dataframe["roc_3"] > 0.0005)
            & (dataframe["close"] > dataframe["ema_5"])
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[entry, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_cond = (
            (dataframe["roc_5"] < -0.008)
            | (
                (dataframe["close"] < dataframe["ema_13"])
                & (dataframe["roc_3"] < -0.003)
            )
        ) & (dataframe["volume"] > 0)

        dataframe.loc[exit_cond, "exit_long"] = 1
        return dataframe
