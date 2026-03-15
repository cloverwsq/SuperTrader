# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
"""
Strategy: CompetitionBreaker — 10 天炒币比赛专用策略

设计目标：10 天内最大化收益，兼顾风险控制

【时间框架】
  主信号：5m（高频信号，日均 4-8 笔交易）
  趋势过滤：1h（大方向 EMA50/200，避免逆势）

【入场逻辑 — 双向】
  多头：1h EMA50>EMA200（大方向看多）+ 5m EMA20>EMA50（小结构向上）
        + RSI(7) 从超卖反弹（30-55 区间）+ MACD 快速版柱状图转正 + 放量
  空头：1h EMA50<EMA200（大方向看空）+ 5m EMA20<EMA50（小结构向下）
        + RSI(7) 从超买回落（45-70 区间）+ MACD 快速版柱状图转负 + 放量

【出场逻辑】
  ROI 梯度：0min=1.5%，20min=1.0%，45min=0.7%（快速锁定利润）
  追踪止损：盈利 1% 后启动，追踪 0.5%（保住盈利）
  止损：-1.0%（严格控损，5m 级别 1% 反弹很快被拦住）
  exit_signal：RSI 进入超买/超卖区（RSI>72 平多，RSI<28 平空）

【杠杆与仓位】
  固定 3x 杠杆（比赛模式，放大收益）
  max_open_trades：1（集中仓位，不分散）

【ADX 过滤】
  ADX(7) > 20：确认有方向性，不在横盘震荡中入场
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from functools import reduce

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, informative
import talib.abstract as ta
from technical import qtpylib


class CompetitionBreaker(IStrategy):
    """
    10 天比赛专用策略：5m 双向高频 + 1h 趋势过滤 + 3x 杠杆
    """

    INTERFACE_VERSION: int = 3
    can_short: bool = True

    timeframe = "5m"

    # ROI：比赛模式，快进快出，不等待大行情
    minimal_roi = {
        "0":  0.020,   # 0-30min：需达到 2.0%（目标较大波动）
        "30": 0.015,   # 30-60min：1.5%
        "60": 0.010,   # 60-90min：1.0%
        "90": 0.007,   # 90min+：0.7%（兜底）
    }

    stoploss = -0.015   # 1.5% 止损（给 5m 噪音更多空间；3x 杠杆等效价格移动 0.5%）

    # 追踪止损：盈利 1.5% 后启动，追踪 0.7%（锁定较大利润）
    trailing_stop = True
    trailing_stop_positive = 0.007
    trailing_stop_positive_offset = 0.015
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count: int = 200

    use_exit_signal = True
    exit_profit_only = False   # 允许 exit_signal 在任何时候平仓（比赛模式）
    ignore_roi_if_entry_signal = False

    # ============================================================
    # 超参数（保持简洁，默认值即为最优）
    # ============================================================
    # 5m EMA
    ema_fast   = IntParameter(8,  30, default=20, space="buy", optimize=True, load=True)
    ema_slow   = IntParameter(30, 80, default=50, space="buy", optimize=True, load=True)

    # RSI(7) 快速版
    rsi_period = IntParameter(5, 14, default=7,  space="buy", optimize=True, load=True)
    rsi_long_entry  = DecimalParameter(25, 45, decimals=0, default=35, space="buy", optimize=True, load=True)
    rsi_short_entry = DecimalParameter(55, 75, decimals=0, default=65, space="buy", optimize=True, load=True)
    rsi_long_exit   = DecimalParameter(65, 80, decimals=0, default=78, space="sell", optimize=True, load=True)
    rsi_short_exit  = DecimalParameter(20, 35, decimals=0, default=22, space="sell", optimize=True, load=True)

    # ADX 趋势强度
    adx_period    = IntParameter(5, 14, default=7,  space="buy", optimize=True, load=True)
    adx_threshold = DecimalParameter(15, 30, decimals=0, default=25, space="buy", optimize=True, load=True)

    # 成交量
    vol_sma_period = IntParameter(10, 30, default=20, space="buy", optimize=True, load=True)

    plot_config = {
        "main_plot": {
            "ema_fast_val": {"color": "cyan"},
            "ema_slow_val": {"color": "blue"},
        },
        "subplots": {
            "RSI":  {"rsi_val":  {"color": "purple"}},
            "MACD": {
                "macd_fast":     {"color": "blue"},
                "macdsig_fast":  {"color": "orange"},
                "macdhist_fast": {"color": "green", "type": "bar"},
            },
            "ADX":  {"adx_val":  {"color": "yellow"}},
        },
    }

    # ============================================================
    # 1h 大趋势（EMA50/200，过滤逆势入场）
    # ============================================================
    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"]  = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"]    = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    # ============================================================
    # 5m 主指标
    # ============================================================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        # EMA 快慢线（5m 小结构）
        for val in self.ema_fast.range:
            dataframe[f"ema_fast_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.ema_slow.range:
            dataframe[f"ema_slow_{val}"] = ta.EMA(dataframe, timeperiod=val)

        # RSI 快速版（7 周期）
        for val in self.rsi_period.range:
            dataframe[f"rsi_{val}"] = ta.RSI(dataframe, timeperiod=val)

        # MACD 快速版（5,13,4）— 比标准(12,26,9)更适合 5m
        macd = ta.MACD(dataframe, fastperiod=5, slowperiod=13, signalperiod=4)
        dataframe["macd_fast"]     = macd["macd"]
        dataframe["macdsig_fast"]  = macd["macdsignal"]
        dataframe["macdhist_fast"] = macd["macdhist"]

        # 布林带（用于出场参考）
        bb = qtpylib.bollinger_bands(dataframe["close"], window=20, stds=2)
        dataframe["bb_upper"]  = bb["upper"]
        dataframe["bb_lower"]  = bb["lower"]
        dataframe["bb_middle"] = bb["mid"]

        # ADX 快速版（7 周期，比 14 更快响应）
        for val in self.adx_period.range:
            dataframe[f"adx_{val}"] = ta.ADX(dataframe, timeperiod=val)

        # 成交量 SMA
        for val in self.vol_sma_period.range:
            dataframe[f"vol_sma_{val}"] = ta.SMA(dataframe["volume"], timeperiod=val)

        # 便于 plot 显示当前参数值
        dataframe["ema_fast_val"] = dataframe[f"ema_fast_{self.ema_fast.value}"]
        dataframe["ema_slow_val"] = dataframe[f"ema_slow_{self.ema_slow.value}"]
        dataframe["rsi_val"]      = dataframe[f"rsi_{self.rsi_period.value}"]
        dataframe["adx_val"]      = dataframe[f"adx_{self.adx_period.value}"]

        return dataframe

    # ============================================================
    # 3x 杠杆
    # ============================================================
    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag, side: str, **kwargs) -> float:
        return 3.0

    # ============================================================
    # 入场信号
    # ============================================================
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_fast_col = f"ema_fast_{self.ema_fast.value}"
        ema_slow_col = f"ema_slow_{self.ema_slow.value}"
        rsi_col      = f"rsi_{self.rsi_period.value}"
        adx_col      = f"adx_{self.adx_period.value}"
        vol_col      = f"vol_sma_{self.vol_sma_period.value}"

        # ── 1h 大趋势方向 ────────────────────────────────────────────
        trend_bull = dataframe["ema50_1h"]  > dataframe["ema200_1h"]  # 1h 多头结构
        trend_bear = dataframe["ema50_1h"]  < dataframe["ema200_1h"]  # 1h 空头结构

        # ── 5m 小结构 ────────────────────────────────────────────────
        structure_bull = dataframe[ema_fast_col] > dataframe[ema_slow_col]
        structure_bear = dataframe[ema_fast_col] < dataframe[ema_slow_col]

        # ── ADX 趋势强度（过滤横盘震荡）────────────────────────────
        strong_trend = dataframe[adx_col] > self.adx_threshold.value

        # ── MACD 持续动量（连续 2 根 K 线同向，且力度递增）───────────────
        macd_bull = (
            (dataframe["macdhist_fast"] > 0)
            & (dataframe["macdhist_fast"].shift(1) > 0)
            & (dataframe["macdhist_fast"] > dataframe["macdhist_fast"].shift(1))
        )
        macd_bear = (
            (dataframe["macdhist_fast"] < 0)
            & (dataframe["macdhist_fast"].shift(1) < 0)
            & (dataframe["macdhist_fast"] < dataframe["macdhist_fast"].shift(1))
        )

        # ── EMA 交叉确认（快线刚穿越慢线） ──────────────────────────
        ema_cross_bull = (
            (dataframe[ema_fast_col] > dataframe[ema_slow_col])
            & (dataframe[ema_fast_col].shift(1) <= dataframe[ema_slow_col].shift(1))
        )
        ema_cross_bear = (
            (dataframe[ema_fast_col] < dataframe[ema_slow_col])
            & (dataframe[ema_fast_col].shift(1) >= dataframe[ema_slow_col].shift(1))
        )

        # ── 放量确认 ─────────────────────────────────────────────────
        vol_surge = dataframe["volume"] > dataframe[vol_col] * 1.5

        # ── K 线方向确认（信号 K 线本身需方向一致）──────────────────
        bull_candle = dataframe["close"] > dataframe["open"]   # 阳线
        bear_candle = dataframe["close"] < dataframe["open"]   # 阴线

        # ── 做多：1h 牛市结构 + 5m 向上 + RSI 趋势区 + (MACD 持续 OR EMA 金叉) + 放量 + 阳线
        long_entry = (
            trend_bull
            & structure_bull
            & strong_trend
            & (dataframe[rsi_col] > self.rsi_long_entry.value)   # RSI > 35（脱离超卖）
            & (dataframe[rsi_col] < 65)                           # RSI < 65（未超买）
            & (macd_bull | ema_cross_bull)
            & vol_surge
            & bull_candle
            & (dataframe["volume"] > 0)
        )

        # ── 做空：1h 空头结构 + 5m 向下 + RSI 趋势区 + (MACD 持续 OR EMA 死叉) + 放量 + 阴线
        short_entry = (
            trend_bear
            & structure_bear
            & strong_trend
            & (dataframe[rsi_col] < self.rsi_short_entry.value)  # RSI < 65（脱离超买）
            & (dataframe[rsi_col] > 35)                           # RSI > 35（未超卖）
            & (macd_bear | ema_cross_bear)
            & vol_surge
            & bear_candle
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[long_entry,  "enter_long"]  = 1
        dataframe.loc[short_entry, "enter_short"] = 1

        return dataframe

    # ============================================================
    # 出场信号（RSI 极端区域平仓）
    # ============================================================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rsi_col = f"rsi_{self.rsi_period.value}"

        # 平多：RSI 超买（>72）OR 触碰布林上轨
        dataframe.loc[
            (
                (dataframe[rsi_col] > self.rsi_long_exit.value)
                | (dataframe["close"] >= dataframe["bb_upper"])
            )
            & (dataframe["volume"] > 0),
            "exit_long",
        ] = 1

        # 平空：RSI 超卖（<28）OR 触碰布林下轨
        dataframe.loc[
            (
                (dataframe[rsi_col] < self.rsi_short_exit.value)
                | (dataframe["close"] <= dataframe["bb_lower"])
            )
            & (dataframe["volume"] > 0),
            "exit_short",
        ] = 1

        return dataframe
