# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# flake8: noqa: F401
# isort: skip_file
"""
Strategy: BearMarketShort v4 — 四因子合并做空（真实资金费率 + 4h 宏观门控）

【宏观层 — 4h 慢速死叉门控】
  - 4h EMA50 < 4h EMA200（确认 4h 级别熊市主趋势结构）
  - 4h MACD hist < 0（4h 动量偏空，反弹期 MACD 转正时门控关闭）

【真实资金费率 — Signal B 核心因子（直接接入 Binance 数据）】
  - funding_rate > 0（多头持续付费给空头 → 市场极度拥挤做多）
  - 近 8 小时资金费率均值 > 0（持续性确认，非单次偶然）
  - 相比 RSI 替代方案，这是真正的链上/交易所数据支撑

【反弹过滤层 — Signal B/C 破位确认】
  - close < 前 2 根 K 线最低点

【信号层 — 三因子 OR】
  Signal A: EMA 死叉 + 布林上轨拒绝 + RSI + MACD + 放量
  Signal B: 真实资金费率 > 0（多头拥挤）+ 动量衰减 + EMA20 失守 + 破位确认
  Signal C: ATR 波动率冲击（地缘风险替代因子）+ 破位确认

【仓位层 — custom_stake_amount（ADX 动态）】
  - 4h ADX ≥ 35：100% 仓位
  - 4h ADX ≥ 28：75% 仓位
  - 其他：50% 仓位
"""

import numpy as np
import pandas as pd
from pandas import DataFrame
from functools import reduce

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, informative
from freqtrade.enums import CandleType
import talib.abstract as ta
from technical import qtpylib


class BearMarketShort(IStrategy):
    """
    熊市三因子做空合约策略 v3（1h + 4h 宏观门控 + 2x 杠杆）
    """

    INTERFACE_VERSION: int = 3
    can_short: bool = True

    timeframe = "1h"

    # ROI：赢家均值目标 ≥5%（2x 杠杆下等效 ≥10% 实际盈利）
    minimal_roi = {
        "0":   0.08,
        "360": 0.06,
        "720": 0.05,
    }

    stoploss = -0.025

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.07
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count: int = 220

    use_exit_signal = True
    exit_profit_only = True
    exit_profit_offset = 0.05
    ignore_roi_if_entry_signal = False

    # ============================================================
    # 超参数
    # ============================================================
    ema_fast_period   = IntParameter(30,  70,  default=50,  space="sell", optimize=True, load=True)
    ema_slow_period   = IntParameter(150, 250, default=200, space="sell", optimize=True, load=True)
    bb_period         = IntParameter(15, 30,  default=20,  space="sell", optimize=True, load=True)
    bb_std            = DecimalParameter(1.5, 3.0, decimals=1, default=2.0, space="sell", optimize=True, load=True)
    rsi_period        = IntParameter(7, 21,   default=14,  space="sell", optimize=True, load=True)
    rsi_short_entry   = DecimalParameter(48, 70, decimals=0, default=55, space="sell", optimize=True, load=True)
    rsi_exit_oversold = DecimalParameter(20, 38, decimals=0, default=28, space="sell", optimize=True, load=True)
    ema20_period      = IntParameter(10, 30,  default=20,  space="sell", optimize=True, load=True)
    atr_period        = IntParameter(7, 21,   default=14,  space="sell", optimize=True, load=True)
    atr_sma_period    = IntParameter(20, 50,  default=30,  space="sell", optimize=True, load=True)
    atr_spike_mult    = DecimalParameter(1.1, 1.8, decimals=1, default=1.3, space="sell", optimize=True, load=True)
    ema100_period     = IntParameter(80, 120, default=100, space="sell", optimize=True, load=True)
    vol_sma_period    = IntParameter(10, 30,  default=20,  space="sell", optimize=True, load=True)
    adx_period        = IntParameter(10, 20,  default=14,  space="sell", optimize=True, load=True)
    adx_threshold     = DecimalParameter(20, 35, decimals=0, default=25, space="sell", optimize=True, load=True)

    plot_config = {
        "main_plot": {
            "ema_fast":  {"color": "red"},
            "ema_slow":  {"color": "orange"},
            "ema100":    {"color": "yellow"},
            "bb_upper":  {"color": "pink"},
            "bb_lower":  {"color": "lightblue"},
        },
        "subplots": {
            "RSI":  {"rsi": {"color": "purple"}},
            "MACD": {
                "macd":       {"color": "blue"},
                "macdsignal": {"color": "orange"},
                "macdhist":   {"color": "green", "type": "bar"},
            },
        },
    }

    # ============================================================
    # 4h 宏观指标（EMA50/200 死叉，稳定的熊市主趋势门控）
    # ============================================================
    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"]    = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"]   = ta.EMA(dataframe, timeperiod=200)
        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd["macdhist"]
        dataframe["rsi"]      = ta.RSI(dataframe, timeperiod=14)
        dataframe["adx"]      = ta.ADX(dataframe, timeperiod=14)
        return dataframe

    # ============================================================
    # 1h 主指标
    # ============================================================
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        for val in self.ema_fast_period.range:
            dataframe[f"ema_fast_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.ema_slow_period.range:
            dataframe[f"ema_slow_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.ema100_period.range:
            dataframe[f"ema100_{val}"]   = ta.EMA(dataframe, timeperiod=val)
        for val in self.ema20_period.range:
            dataframe[f"ema20_{val}"]    = ta.EMA(dataframe, timeperiod=val)

        for period in self.bb_period.range:
            for std in self.bb_std.range:
                bb = qtpylib.bollinger_bands(dataframe["close"], window=period, stds=std)
                dataframe[f"bb_{period}_{std}_upper"]  = bb["upper"]
                dataframe[f"bb_{period}_{std}_middle"] = bb["mid"]
                dataframe[f"bb_{period}_{std}_lower"]  = bb["lower"]

        for val in self.rsi_period.range:
            dataframe[f"rsi_{val}"] = ta.RSI(dataframe, timeperiod=val)

        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macd"]     = macd["macd"]
        dataframe["macdsig"]  = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        for val in self.atr_period.range:
            dataframe[f"atr_{val}"] = ta.ATR(dataframe, timeperiod=val)
        for val in self.atr_sma_period.range:
            dataframe[f"atr_sma_{val}"] = ta.SMA(
                dataframe[f"atr_{self.atr_period.value}"], timeperiod=val
            ) if f"atr_{self.atr_period.value}" in dataframe.columns \
            else ta.SMA(ta.ATR(dataframe, timeperiod=self.atr_period.value), timeperiod=val)

        for val in self.vol_sma_period.range:
            dataframe[f"vol_sma_{val}"] = ta.SMA(dataframe["volume"], timeperiod=val)

        for val in self.adx_period.range:
            dataframe[f"adx_{val}"] = ta.ADX(dataframe, timeperiod=val)

        # ── 真实资金费率（直接接入 Binance funding_rate 数据）──────────
        # freqtrade 将资金费率存储在 CandleType.FUNDING_RATE 数据的 open 列
        funding_df = self.dp.get_pair_dataframe(
            pair=metadata["pair"],
            timeframe=self.timeframe,
            candle_type=CandleType.FUNDING_RATE,
        )
        if funding_df is not None and not funding_df.empty:
            funding_df = funding_df[["date", "open"]].rename(
                columns={"open": "funding_rate"}
            )
            dataframe = dataframe.merge(funding_df, on="date", how="left")
            dataframe["funding_rate"] = dataframe["funding_rate"].ffill().fillna(0)
        else:
            dataframe["funding_rate"] = 0

        # 近 8 小时资金费率均值（确认多头拥挤的持续性，非偶然单次）
        dataframe["funding_rate_mean8"] = dataframe["funding_rate"].rolling(8).mean().fillna(0)

        return dataframe

    # ============================================================
    # 动态仓位：4h ADX 趋势越强，仓位越大
    # ============================================================
    def custom_stake_amount(self, current_time, current_rate, proposed_stake,
                            min_stake, max_stake, leverage, entry_tag, side, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(
            self.config.get("exchange", {}).get("pair_whitelist", ["BTC/USDT:USDT"])[0],
            self.timeframe,
        )
        if dataframe is None or dataframe.empty:
            return proposed_stake * 0.5

        last = dataframe.iloc[-1]
        adx_4h = last.get("adx_4h", 20)

        if adx_4h >= 35:
            ratio = 1.0
        elif adx_4h >= 28:
            ratio = 0.75
        else:
            ratio = 0.5

        stake = proposed_stake * ratio
        return max(min_stake, min(stake, max_stake))

    # ============================================================
    # 入场信号
    # ============================================================
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema_fast_col = f"ema_fast_{self.ema_fast_period.value}"
        ema_slow_col = f"ema_slow_{self.ema_slow_period.value}"
        ema100_col   = f"ema100_{self.ema100_period.value}"
        ema20_col    = f"ema20_{self.ema20_period.value}"
        std_val      = self.bb_std.value
        bb_upper_col = f"bb_{self.bb_period.value}_{std_val}_upper"
        bb_lower_col = f"bb_{self.bb_period.value}_{std_val}_lower"
        rsi_col      = f"rsi_{self.rsi_period.value}"
        vol_col      = f"vol_sma_{self.vol_sma_period.value}"
        atr_col      = f"atr_{self.atr_period.value}"
        atr_sma_col  = f"atr_sma_{self.atr_sma_period.value}"
        adx_col      = f"adx_{self.adx_period.value}"

        # ── 4h 宏观门控：EMA50/200 死叉（稳健，只在真熊市期间开放入场）
        macro_bear_strong = dataframe["ema50_4h"] < dataframe["ema200_4h"]   # 4h 慢速死叉
        macro_bear_macd   = dataframe["macdhist_4h"] < 0                      # 4h 动量偏空

        # ── 1h 反弹过滤 ──────────────────────────────────────────────
        # 宽松版：近 2 根 K 线整体下行（Signal A）
        momentum_down = dataframe["close"] < dataframe["close"].shift(2)
        # 严格版：跌破前 2 根 K 线低点（Signal B/C，排除区间震荡）
        breakdown_confirm = dataframe["close"] < dataframe["low"].shift(1).rolling(2).min()

        # ── 1h 公共条件 ──────────────────────────────────────────────
        strong_trend          = dataframe[adx_col] > self.adx_threshold.value
        death_cross_structure = dataframe[ema_fast_col] < dataframe[ema_slow_col]
        macd_hist_negative    = dataframe["macdhist"] < 0
        macd_hist_turning_neg = (dataframe["macdhist"] < 0) & (dataframe["macdhist"].shift(1) >= 0)

        # ── Signal A: EMA 死叉 + 布林上轨拒绝（需 4h 死叉 + 1h 动量向下）
        signal_a = (
            macro_bear_strong
            & momentum_down
            & death_cross_structure
            & strong_trend
            & (dataframe["close"] >= dataframe[bb_upper_col] * 0.995)
            & (dataframe[rsi_col] > self.rsi_short_entry.value)
            & macd_hist_negative
            & (dataframe["volume"] > dataframe[vol_col])
            & (dataframe["volume"] > 0)
        )

        # ── 真实资金费率：多头拥挤确认 ──────────────────────────────────
        # funding_rate > 0：多头持续向空头支付费用（市场极度偏多，反转风险高）
        # 近 8h 均值 > 0：确认拥挤是持续性的，不是单次偶然
        crowded_long = (
            (dataframe["funding_rate"] > 0)           # 当前资金费率为正
            & (dataframe["funding_rate_mean8"] > 0)   # 近 8h 持续为正
        )

        # ── Signal B: 真实资金费率拥挤 + 动量衰减（需 4h 双重门控 + 破位确认）
        signal_b = (
            macro_bear_strong
            & macro_bear_macd
            & crowded_long                             # 真实资金费率确认多头拥挤
            & breakdown_confirm
            & strong_trend
            & (dataframe["close"] < dataframe[ema100_col])
            & macd_hist_turning_neg
            & (dataframe["close"] < dataframe[ema20_col])
            & (dataframe["volume"] > dataframe[vol_col])
            & (dataframe["volume"] > 0)
        )

        # ── Signal C: ATR 波动率冲击（需 4h 双重门控 + 破位确认）────────
        atr_spike = dataframe[atr_col] > dataframe[atr_sma_col] * self.atr_spike_mult.value
        signal_c = (
            macro_bear_strong
            & macro_bear_macd
            & breakdown_confirm
            & atr_spike
            & strong_trend
            & (dataframe["close"] < dataframe[ema100_col])
            & (dataframe[rsi_col] < 55)
            & macd_hist_negative
            & (dataframe["volume"] > dataframe[vol_col])
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[
            (signal_a | signal_b | signal_c),
            "enter_short",
        ] = 1

        return dataframe

    # ============================================================
    # 出场信号
    # ============================================================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        std_val      = self.bb_std.value
        bb_lower_col = f"bb_{self.bb_period.value}_{std_val}_lower"
        rsi_col      = f"rsi_{self.rsi_period.value}"

        dataframe.loc[
            (
                (
                    (dataframe[rsi_col] < self.rsi_exit_oversold.value)
                    | (dataframe["close"] <= dataframe[bb_lower_col])
                )
                & (dataframe["volume"] > 0)
            ),
            "exit_short",
        ] = 1

        return dataframe
