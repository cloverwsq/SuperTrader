"""
Freqtrade Strategy: Momentum Scalping (LONG-ONLY)
Signal: 5m momentum detection with aligned EMAs
Execution: 1m entry on continuation
Timeframe: 1m (with 5m informative)
"""

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, informative


class MomentumScalpFT(IStrategy):
    INTERFACE_VERSION: int = 3
    can_short: bool = False

    timeframe = "1m"

    minimal_roi = {
        "0": 0.012,
        "10": 0.008,
        "20": 0.005,
        "30": 0.003,
    }

    stoploss = -0.010
    trailing_stop = True
    trailing_stop_positive = 0.004
    trailing_stop_positive_offset = 0.006
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count: int = 100
    use_exit_signal = True

    @informative("5m")
    def populate_indicators_5m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_5"] = dataframe["close"].ewm(span=5, adjust=False).mean()
        dataframe["ema_13"] = dataframe["close"].ewm(span=13, adjust=False).mean()
        dataframe["ema_21"] = dataframe["close"].ewm(span=21, adjust=False).mean()
        dataframe["ema_slope"] = dataframe["ema_5"].pct_change(3)

        delta = dataframe["close"].diff()
        gain = delta.clip(lower=0).rolling(7).mean()
        loss = (-delta).clip(lower=0).rolling(7).mean()
        rs = gain / (loss + 1e-10)
        dataframe["rsi_7"] = 100 - (100 / (1 + rs))

        dataframe["vol_ma"] = dataframe["volume"].rolling(20).mean()
        dataframe["vol_ratio_5m"] = dataframe["volume"] / (dataframe["vol_ma"] + 1e-10)
        dataframe["higher_low"] = (dataframe["low"].shift(1) > dataframe["low"].shift(2)).astype(int)

        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_3"] = dataframe["close"].ewm(span=3, adjust=False).mean()
        dataframe["ema_8"] = dataframe["close"].ewm(span=8, adjust=False).mean()
        dataframe["ret_1"] = dataframe["close"].pct_change(1)
        dataframe["ret_3"] = dataframe["close"].pct_change(3)

        hl = dataframe["high"] - dataframe["low"]
        hc = (dataframe["high"] - dataframe["close"].shift(1)).abs()
        lc = (dataframe["low"] - dataframe["close"].shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        dataframe["atr_pct"] = tr.rolling(10).mean() / dataframe["close"]

        dataframe["vol_ma_1m"] = dataframe["volume"].rolling(20).mean()
        dataframe["vol_ratio_1m"] = dataframe["volume"] / (dataframe["vol_ma_1m"] + 1e-10)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        mom_5m = (
            (dataframe["ema_5_5m"] > dataframe["ema_13_5m"])
            & (dataframe["ema_13_5m"] > dataframe["ema_21_5m"])
            & (dataframe["ema_slope_5m"] > 0)
            & (dataframe["higher_low_5m"] == 1)
            & (dataframe["rsi_7_5m"] > 40)
            & (dataframe["rsi_7_5m"] < 72)
            & (dataframe["vol_ratio_5m_5m"] > 0.7)
        )

        entry = (
            mom_5m
            & (dataframe["ret_1"] > 0)
            & (dataframe["ema_3"] > dataframe["ema_8"])
            & (dataframe["atr_pct"] > 0.0008)
            & (dataframe["atr_pct"] < 0.012)
            & (dataframe["vol_ratio_1m"] > 0.6)
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[entry, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_cond = (
            (dataframe["ret_3"] < -0.0012)
            | (dataframe["ema_3"] < dataframe["ema_8"])
        ) & (dataframe["volume"] > 0)

        dataframe.loc[exit_cond, "exit_long"] = 1
        return dataframe
