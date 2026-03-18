"""
Freqtrade Strategy: Breakout for Meme Coins (LONG-ONLY)
Signal: 15m breakout from tight consolidation with volume burst
Execution: 1m with momentum confirmation
Timeframe: 1m (with 15m informative)
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
        "0": 0.07,
        "15": 0.04,
        "30": 0.02,
    }

    stoploss = -0.035
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.025
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count: int = 200
    use_exit_signal = True

    @informative("15m")
    def populate_indicators_15m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        lookback = 10
        dataframe["rolling_high"] = dataframe["high"].rolling(lookback).max()
        dataframe["prev_high"] = dataframe["rolling_high"].shift(1)
        dataframe["rolling_low"] = dataframe["low"].rolling(lookback).min()
        dataframe["consolidation"] = (
            (dataframe["rolling_high"] - dataframe["rolling_low"]) / (dataframe["close"] + 1e-10)
        )
        dataframe["ema_8"] = dataframe["close"].ewm(span=8, adjust=False).mean()
        dataframe["ema_21"] = dataframe["close"].ewm(span=21, adjust=False).mean()
        dataframe["vol_ma_15"] = dataframe["volume"].rolling(15).mean()
        dataframe["vol_ratio_15m"] = dataframe["volume"] / (dataframe["vol_ma_15"] + 1e-10)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_5"] = dataframe["close"].ewm(span=5, adjust=False).mean()
        dataframe["ema_13"] = dataframe["close"].ewm(span=13, adjust=False).mean()
        dataframe["vol_ma_1m"] = dataframe["volume"].rolling(20).mean()
        dataframe["vol_ratio_1m"] = dataframe["volume"] / (dataframe["vol_ma_1m"] + 1e-10)
        dataframe["roc_3"] = dataframe["close"].pct_change(3)
        dataframe["accel"] = dataframe["roc_3"] - dataframe["roc_3"].shift(3)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        breakout_15m = (
            (dataframe["close_15m"] > dataframe["prev_high_15m"])
            & (dataframe["consolidation_15m"].shift(1) < 0.06)
            & (dataframe["vol_ratio_15m_15m"] > 2.0)
            & (dataframe["ema_8_15m"] > dataframe["ema_21_15m"])
        )

        entry = (
            breakout_15m
            & (dataframe["roc_3"] > 0.003)
            & (dataframe["accel"] > 0)
            & (dataframe["ema_5"] > dataframe["ema_13"])
            & (dataframe["vol_ratio_1m"] > 1.0)
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[entry, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_cond = (
            (dataframe["roc_3"] < -0.005)
            | (dataframe["close"] < dataframe["ema_5"])
            | (dataframe["vol_ratio_1m"] < 0.4)
        ) & (dataframe["volume"] > 0)

        dataframe.loc[exit_cond, "exit_long"] = 1
        return dataframe
