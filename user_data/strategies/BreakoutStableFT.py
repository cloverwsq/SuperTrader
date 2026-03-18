"""
Freqtrade Strategy: Breakout for Stable/Liquid Coins (LONG-ONLY)
Signal: 30m breakout above rolling high
Execution: 5m with EMA + volume confirmation
Timeframe: 5m (with 30m informative)
"""

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, informative


class BreakoutStableFT(IStrategy):
    INTERFACE_VERSION: int = 3
    can_short: bool = False  # LONG-ONLY

    timeframe = "5m"

    minimal_roi = {
        "0": 0.05,
        "60": 0.03,
        "120": 0.015,
        "240": 0.005,
    }

    stoploss = -0.025
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count: int = 200
    use_exit_signal = True
    exit_profit_only = False

    # Parameters
    lookback = IntParameter(10, 30, default=20, space="buy", optimize=True, load=True)
    ema_fast = IntParameter(5, 15, default=9, space="buy", optimize=True, load=True)
    ema_slow = IntParameter(15, 30, default=21, space="buy", optimize=True, load=True)
    vol_threshold = DecimalParameter(1.0, 2.5, decimals=1, default=1.3, space="buy", optimize=True, load=True)

    # 30m informative for breakout detection
    @informative("30m")
    def populate_indicators_30m(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rolling_high_20"] = dataframe["high"].rolling(20).max()
        dataframe["prev_high"] = dataframe["rolling_high_20"].shift(1)
        dataframe["ema_50"] = dataframe["close"].ewm(span=50, adjust=False).mean()
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for val in self.ema_fast.range:
            dataframe[f"ema_fast_{val}"] = dataframe["close"].ewm(span=val, adjust=False).mean()
        for val in self.ema_slow.range:
            dataframe[f"ema_slow_{val}"] = dataframe["close"].ewm(span=val, adjust=False).mean()

        dataframe["vol_ma_20"] = dataframe["volume"].rolling(20).mean()
        dataframe["vol_ratio"] = dataframe["volume"] / (dataframe["vol_ma_20"] + 1e-10)
        dataframe["roc_5"] = dataframe["close"].pct_change(5)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_f = f"ema_fast_{self.ema_fast.value}"
        ema_s = f"ema_slow_{self.ema_slow.value}"

        # 30m breakout: close above previous rolling high + above 50 EMA
        breakout_30m = (
            (dataframe["close_30m"] > dataframe["prev_high_30m"])
            & (dataframe["close_30m"] > dataframe["ema_50_30m"])
        )

        # 5m confirmation
        entry = (
            breakout_30m
            & (dataframe[ema_f] > dataframe[ema_s])
            & (dataframe["vol_ratio"] > self.vol_threshold.value)
            & (dataframe["roc_5"] > 0)
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[entry, "enter_long"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_s = f"ema_slow_{self.ema_slow.value}"

        exit_cond = (
            (dataframe["close"] < dataframe[ema_s])
            | (dataframe["roc_5"] < -0.012)
        ) & (dataframe["volume"] > 0)

        dataframe.loc[exit_cond, "exit_long"] = 1
        return dataframe
