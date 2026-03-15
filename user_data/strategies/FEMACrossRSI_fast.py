"""FEMACrossRSI fast_trend variant: ema_fast=12, ema_slow=34, ema_trend=100, rsi_period=10, rsi_min=35, rsi_max=68, vol_sma_period=15"""
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
import talib.abstract as ta
from technical import qtpylib


class FEMACrossRSI_fast(IStrategy):
    INTERFACE_VERSION: int = 3
    can_short: bool = False
    timeframe = "15m"
    minimal_roi = {"0": 0.04, "60": 0.025, "120": 0.015, "240": 0.008}
    stoploss = -0.03
    trailing_stop = True
    trailing_stop_positive = 0.012
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True
    process_only_new_candles = True
    startup_candle_count: int = 120
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    ema_fast       = IntParameter(10, 30,  default=12,  space="buy", optimize=False, load=False)
    ema_slow       = IntParameter(30, 80,  default=34,  space="buy", optimize=False, load=False)
    ema_trend      = IntParameter(80, 200, default=100, space="buy", optimize=False, load=False)
    rsi_period     = IntParameter(7, 21,   default=10,  space="buy", optimize=False, load=False)
    rsi_min        = DecimalParameter(30, 50, decimals=0, default=35, space="buy", optimize=False, load=False)
    rsi_max        = DecimalParameter(60, 80, decimals=0, default=68, space="buy", optimize=False, load=False)
    vol_sma_period = IntParameter(10, 30,  default=15,  space="buy", optimize=False, load=False)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for val in self.ema_fast.range:
            dataframe[f"ema_fast_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.ema_slow.range:
            dataframe[f"ema_slow_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.ema_trend.range:
            dataframe[f"ema_trend_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.rsi_period.range:
            dataframe[f"rsi_{val}"] = ta.RSI(dataframe, timeperiod=val)
        for val in self.vol_sma_period.range:
            dataframe[f"vol_sma_{val}"] = ta.SMA(dataframe["volume"], timeperiod=val)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fast_col  = f"ema_fast_{self.ema_fast.value}"
        slow_col  = f"ema_slow_{self.ema_slow.value}"
        trend_col = f"ema_trend_{self.ema_trend.value}"
        rsi_col   = f"rsi_{self.rsi_period.value}"
        vol_col   = f"vol_sma_{self.vol_sma_period.value}"
        dataframe.loc[
            (
                qtpylib.crossed_above(dataframe[fast_col], dataframe[slow_col])
                & (dataframe["close"] > dataframe[trend_col])
                & (dataframe[rsi_col] > self.rsi_min.value)
                & (dataframe[rsi_col] < self.rsi_max.value)
                & (dataframe["volume"] > dataframe[vol_col])
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe
