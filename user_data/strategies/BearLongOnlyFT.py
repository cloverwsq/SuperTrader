"""
Freqtrade Strategy: Bear Market Long-Only Strategy
Signal: 1h regime filter + oversold rebound / trend participation
Execution: 5m entry
Timeframe: 5m (with 1h informative)
"""

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, informative


class BearLongOnlyFT(IStrategy):
    INTERFACE_VERSION: int = 3
    can_short: bool = False

    timeframe = "5m"

    minimal_roi = {
        "0": 0.035,
        "60": 0.02,
        "120": 0.01,
        "360": 0.005,
    }

    stoploss = -0.020
    trailing_stop = True
    trailing_stop_positive = 0.012
    trailing_stop_positive_offset = 0.015
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count: int = 250
    use_exit_signal = True

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_20"] = dataframe["close"].ewm(span=20, adjust=False).mean()
        dataframe["ema_50"] = dataframe["close"].ewm(span=50, adjust=False).mean()

        delta = dataframe["close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta).clip(lower=0).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        dataframe["rsi_14"] = 100 - (100 / (1 + rs))

        dataframe["ret_24h"] = dataframe["close"].pct_change(24)

        vol = dataframe["close"].pct_change().rolling(24).std()
        vol_ma = vol.rolling(72).mean()
        dataframe["high_vol"] = (vol > vol_ma * 1.5).astype(int)

        # Regime
        dataframe["regime"] = 0  # neutral
        dataframe.loc[
            (dataframe["ema_20"] > dataframe["ema_50"]) & (dataframe["ret_24h"] > 0),
            "regime"
        ] = 1  # bull
        dataframe.loc[
            (dataframe["ema_20"] < dataframe["ema_50"]) & (dataframe["ret_24h"] < -0.01),
            "regime"
        ] = -1  # bear

        # Oversold rebound
        dataframe["oversold"] = (
            (dataframe["rsi_14"] < 32)
            & (dataframe["rsi_14"] > dataframe["rsi_14"].shift(1))
        ).astype(int)

        # Trend participation
        dataframe["trend_ok"] = (
            (dataframe["regime"] == 1)
            & (dataframe["close"] > dataframe["ema_20"])
            & (dataframe["rsi_14"] > 40)
            & (dataframe["rsi_14"] < 70)
        ).astype(int)

        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_9"] = dataframe["close"].ewm(span=9, adjust=False).mean()
        dataframe["ema_21"] = dataframe["close"].ewm(span=21, adjust=False).mean()
        dataframe["roc_5"] = dataframe["close"].pct_change(5)
        dataframe["vol_ma"] = dataframe["volume"].rolling(20).mean()
        dataframe["vol_ratio"] = dataframe["volume"] / (dataframe["vol_ma"] + 1e-10)

        delta = dataframe["close"].diff()
        gain = delta.clip(lower=0).rolling(7).mean()
        loss = (-delta).clip(lower=0).rolling(7).mean()
        rs = gain / (loss + 1e-10)
        dataframe["rsi_7_5m"] = 100 - (100 / (1 + rs))

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Oversold rebound entry
        oversold_entry = (
            (dataframe["oversold_1h"] == 1)
            & (dataframe["rsi_7_5m"] < 40)
            & (dataframe["rsi_7_5m"] > dataframe["rsi_7_5m"].shift(1))
            & (dataframe["close"] > dataframe["ema_9"])
            & (dataframe["vol_ratio"] > 0.8)
            & (dataframe["volume"] > 0)
        )

        # Trend participation entry
        trend_entry = (
            (dataframe["trend_ok_1h"] == 1)
            & (dataframe["ema_9"] > dataframe["ema_21"])
            & (dataframe["roc_5"] > 0)
            & (dataframe["rsi_7_5m"] > 45)
            & (dataframe["rsi_7_5m"] < 68)
            & (dataframe["vol_ratio"] > 0.8)
            & (dataframe["volume"] > 0)
        )

        # Block entry in bear + high vol
        bear_block = (dataframe["regime_1h"] == -1) & (dataframe["high_vol_1h"] == 1)

        dataframe.loc[oversold_entry & ~bear_block, "enter_long"] = 1
        dataframe.loc[trend_entry & ~bear_block, "enter_long"] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_cond = (
            (dataframe["rsi_7_5m"] > 75)
            | (dataframe["close"] < dataframe["ema_21"])
            | (dataframe["roc_5"] < -0.015)
        ) & (dataframe["volume"] > 0)

        dataframe.loc[exit_cond, "exit_long"] = 1
        return dataframe
