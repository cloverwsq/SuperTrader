# pragma pylint: disable=missing-docstring, invalid-name
"""
Strategy: AggressiveBear15m  v4 — 多品种熊市反弹做空
=====================================================
核心逻辑（继承 v3 高质量入场，扩展至多品种高杠杆）：
  - 等价格从低位反弹到 EMA21 阻力位 → 出现阴线拒绝 → 做空
  - v3 同款入场（高胜率），5 个品种 × 5x 杠杆 → 大幅提升绝对收益
  - 止损从 -2.5% 放宽至 -4%（5x 时市价风险保持 ~0.8%，与 v3 相同）
  - ROI 上限提高至 25%，让追踪止盈主导退出

v4 vs v3 改进：
  ① 多品种：BTC/ETH/SOL/XRP/ADA 同时交易（配置文件中设置）
  ② 杠杆提升：5x（强信号 7x），win 的收益放大
  ③ 止损匹配杠杆：-4%（5x × 0.8% 市价 ≈ v3 的 3x × 0.83%）
  ④ 追踪止盈：6% 启动，-2.5% 跟踪，可捕捉大行情
  ⑤ 目标收益：两个月熊市 >20%

入场条件（同 v3）：
  1h ：EMA_fast < EMA_slow（中期下行趋势）
  15m：价格反弹至 EMA21 附近 → 出现阴线拒绝 → RSI 回落 → MACD 持续负
"""

from datetime import datetime
from typing import Optional
import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, informative
from freqtrade.enums import CandleType
import talib.abstract as ta


class AggressiveBear15m(IStrategy):

    INTERFACE_VERSION: int = 3
    can_short: bool = True
    timeframe = "15m"

    # 高 ROI 门槛 → 让追踪止盈主导出场，不过早截断获利
    minimal_roi = {
        "0":    0.25,   # 25%（5x = 5% 市价）几乎不会直接触发
        "480":  0.12,   # 8h 后降至 12%
        "960":  0.06,   # 16h 后降至 6%
        "1440": 0.03,   # 24h 后降至 3%（兜底）
    }

    stoploss = -0.04                          # 4% 仓位（5x = 0.8% 市价）
    trailing_stop = True
    trailing_stop_positive = 0.02             # 峰值后退 2% 触发
    trailing_stop_positive_offset = 0.06      # 利润达 6% 后启动追踪
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    startup_candle_count: int = 220

    use_exit_signal = True
    exit_profit_only = True       # 亏损靠 stoploss，不靠信号
    ignore_roi_if_entry_signal = False

    # ── 超参数 ──────────────────────────────────────────────────────
    ema21_period  = IntParameter(15, 30,   default=21,   space="sell", optimize=True, load=True)
    ema50_period  = IntParameter(35, 65,   default=50,   space="sell", optimize=True, load=True)

    rsi_period    = IntParameter(7,  21,   default=14,   space="sell", optimize=True, load=True)
    rsi_bounce    = DecimalParameter(40, 62, decimals=0, default=52,
                                     space="sell", optimize=True, load=True)  # 反弹 RSI 高点

    vol_period    = IntParameter(10, 30,   default=20,   space="sell", optimize=True, load=True)

    # EMA 阻力贴近度
    ema_touch_pct = DecimalParameter(0.001, 0.015, decimals=3, default=0.007,
                                     space="sell", optimize=True, load=True)

    gate_ema_fast = IntParameter(15, 25,   default=20,   space="sell", optimize=True, load=True)
    gate_ema_slow = IntParameter(40, 60,   default=50,   space="sell", optimize=True, load=True)

    # ── 1h 门控 ─────────────────────────────────────────────────────
    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        for val in self.gate_ema_fast.range:
            dataframe[f"ema_fast_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.gate_ema_slow.range:
            dataframe[f"ema_slow_{val}"] = ta.EMA(dataframe, timeperiod=val)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    # ── 15m 主指标 ──────────────────────────────────────────────────
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:

        for val in self.ema21_period.range:
            dataframe[f"ema21_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.ema50_period.range:
            dataframe[f"ema50_{val}"] = ta.EMA(dataframe, timeperiod=val)
        for val in self.rsi_period.range:
            dataframe[f"rsi_{val}"] = ta.RSI(dataframe, timeperiod=val)
        for val in self.vol_period.range:
            dataframe[f"vol_sma_{val}"] = ta.SMA(dataframe["volume"], timeperiod=val)

        macd = ta.MACD(dataframe, fastperiod=12, slowperiod=26, signalperiod=9)
        dataframe["macdhist"] = macd["macdhist"]
        dataframe["atr14"]    = ta.ATR(dataframe, timeperiod=14)

        # 资金费率
        funding_df = self.dp.get_pair_dataframe(
            pair=metadata["pair"],
            timeframe="1h",
            candle_type=CandleType.FUNDING_RATE,
        )
        if funding_df is not None and not funding_df.empty:
            funding_df = funding_df[["date", "open"]].rename(columns={"open": "funding_rate"})
            dataframe = dataframe.merge(funding_df, on="date", how="left")
            dataframe["funding_rate"] = dataframe["funding_rate"].ffill().fillna(0)
        else:
            dataframe["funding_rate"] = 0

        return dataframe

    # ── 动态杠杆 ────────────────────────────────────────────────────
    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: Optional[str], side: str, **kwargs) -> float:
        if entry_tag and "fund" in entry_tag:
            return min(7.0, max_leverage)   # 资金费率偏高时增加杠杆
        return min(5.0, max_leverage)

    # ── 入场信号 ────────────────────────────────────────────────────
    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        ema21_col = f"ema21_{self.ema21_period.value}"
        ema50_col = f"ema50_{self.ema50_period.value}"
        rsi_col   = f"rsi_{self.rsi_period.value}"
        vol_col   = f"vol_sma_{self.vol_period.value}"
        gf_col    = f"ema_fast_{self.gate_ema_fast.value}_1h"
        gs_col    = f"ema_slow_{self.gate_ema_slow.value}_1h"

        # ── 1h 门控：中期趋势向下 + 价格仍在 1h EMA50 下方（过滤强反弹）──
        # 关键：若价格已站上 1h EMA50，说明是趋势反转/强反弹，不做空
        gate_bear = (
            (dataframe[gf_col] < dataframe[gs_col])         # 1h 死叉
            & (dataframe["close"] < dataframe[gs_col])       # 15m 价格在 1h EMA50 下方
            & (dataframe["rsi_1h"] < 60)                     # 1h 动量偏弱
        )

        # ── 核心：反弹触及 EMA21 后阴线拒绝 ──────────────────────────
        # 价格高点触及 EMA21（当根或前一根）
        touched_ema = (
            (dataframe["high"] >= dataframe[ema21_col] * (1 - self.ema_touch_pct.value))
            & (dataframe["high"] <= dataframe[ema21_col] * (1 + self.ema_touch_pct.value * 3))
        )
        # 阴线拒绝：收盘在 EMA21 下方
        rejection = (
            (dataframe["close"] < dataframe[ema21_col])
            & (dataframe["close"] < dataframe["open"])
        )

        # ── 辅助确认 ─────────────────────────────────────────────────
        # RSI 反弹后回落
        rsi_pullback = (
            (dataframe[rsi_col] > self.rsi_bounce.value - 8)
            & (dataframe[rsi_col] < self.rsi_bounce.value + 12)
        )
        # EMA50 压制
        below_ema50 = dataframe["close"] < dataframe[ema50_col]
        # MACD 仍负
        macd_neg = dataframe["macdhist"] < 0
        # 成交量确认
        vol_ok = dataframe["volume"] > dataframe[vol_col]

        # ── enter_tag ────────────────────────────────────────────────
        dataframe["enter_tag"] = "base"
        dataframe.loc[dataframe["funding_rate"] > 0.0001, "enter_tag"] = "fund"

        # ── 主入场 ───────────────────────────────────────────────────
        entry = (
            gate_bear
            & (touched_ema | touched_ema.shift(1))
            & rejection
            & rsi_pullback
            & below_ema50
            & macd_neg
            & vol_ok
            & (dataframe["volume"] > 0)
        )

        dataframe.loc[entry, "enter_short"] = 1
        return dataframe

    # ── 出场信号 ────────────────────────────────────────────────────
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rsi_col   = f"rsi_{self.rsi_period.value}"
        ema21_col = f"ema21_{self.ema21_period.value}"

        # RSI 深度超卖（≤25）才平仓 — 不用 EMA21 交叉，让追踪止盈负责
        # 移除 EMA21 exit：它过早截断获利（trailing stop 会更好地管理出场）
        dataframe.loc[
            (dataframe[rsi_col] <= 25)
            & (dataframe["volume"] > 0),
            "exit_short",
        ] = 1

        return dataframe
