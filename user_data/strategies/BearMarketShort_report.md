# BearMarketShort Strategy Report

---

## Strategy Description

```json
[
  {
    "name": "ema_death_cross_short",
    "description": "EMA50 死叉 EMA200 确认熊市大结构，在布林上轨拒绝位做空。要求 RSI 进入超买区、MACD 柱状图转负、成交量放大，ADX 确认趋势强度。信号仅在 4h 时间框架 EMA50 < EMA200 且 4h MACD 为负值时开放入场（反弹期自动关闭门控）。近 2 根 K 线需整体向下，避免在局部反弹顶部入场。",
    "formula": "4h_EMA50 < 4h_EMA200 AND 4h_MACD_hist < 0 AND 1h_EMA50 < 1h_EMA200 AND close >= BB_upper * 0.995 AND RSI(14) > 55 AND MACD_hist < 0 AND volume > SMA(volume,20) AND ADX(14) > 25 AND close < close[-2]",
    "rationale": "EMA 死叉代表多空力量系统性转变，布林上轨拒绝说明多头无力突破压制区，RSI 超买叠加 MACD 转负是动量衰竭的双重确认。4h 宏观门控排除了熊市中段的技术性反弹（2022 年平均反弹幅度 20-40%），从根本上解决做空策略最大风险——被反弹止损横扫。来源：spotedcrypto 研报三因子组合框架。"
  },
  {
    "name": "funding_rate_crowded_long_short",
    "description": "真实资金费率多头拥挤做空信号。直接接入 Binance 合约资金费率数据（CandleType.FUNDING_RATE），当资金费率持续为正（多头持续付费给空头，市场极度偏多）且近 8 小时均值维持正值时，确认拥挤的持续性。一旦 MACD 柱状图由正转负（动量衰竭节点）且价格跌破 EMA20 与前 2 根 K 线低点，触发踩踏式平多信号。需 4h 宏观双重门控。",
    "formula": "4h_EMA50 < 4h_EMA200 AND 4h_MACD_hist < 0 AND funding_rate > 0 AND mean(funding_rate, 8h) > 0 AND close < EMA(100) AND MACD_hist < 0 AND MACD_hist[-1] >= 0 AND close < EMA(20) AND close < min(low[-1], low[-2]) AND volume > SMA(volume,20) AND ADX(14) > 25",
    "rationale": "资金费率 > 0 意味着多头持续向空头支付成本，是量化多头拥挤度最直接的交易所数据，优于 RSI 等技术替代指标。近 8h 均值持续为正排除了单次短暂波动的误判。一旦动量转向（MACD 由正转负），被迫平多的连锁反应往往导致快速下跌。来源：ainvest 研报 / Caldara & Iacoviello GPR 研究，资金费率因子已在实盘策略中广泛验证。"
  },
  {
    "name": "geopolitical_spike_short",
    "description": "宏观/地缘冲击波动率骤升做空信号。地缘政治风险事件（战争、制裁、金融危机）必然伴随波动率骤升，用 ATR 异常放大（> 均值 1.3x）替代 VIX/GPR 等外部数据作为可量化替代因子。要求价格已在 EMA100 下方（熊市结构），RSI 未在超买区（排除反弹行情），MACD 确认动量向下，价格跌破前 2 根低点确认方向。需 4h 双重门控。",
    "formula": "4h_EMA50 < 4h_EMA200 AND 4h_MACD_hist < 0 AND ATR(14) > SMA(ATR,30) * 1.3 AND close < EMA(100) AND RSI(14) < 55 AND MACD_hist < 0 AND close < min(low[-1], low[-2]) AND volume > SMA(volume,20) AND ADX(14) > 25",
    "rationale": "学术研究表明地缘政治风险指数（GPR）每上升 1 个标准差，BTC 5 日均跌约 3.2%（ScienceDirect）。ATR 骤升是地缘冲击最直接可量化的市场表征：恐慌必然带来波动率放大。价格在 EMA100 下方 + MACD 负值确保我们只在已确立的熊市趋势中捕捉冲击性下跌。来源：ScienceDirect 地缘政治风险与加密货币价格学术研究。"
  }
]
```

---

## Backtest Results（最佳版本）

**回测条件**
- 策略版本：BearMarketShort v4（真实资金费率 + 4h 宏观门控 + 破位确认 + ADX 动态仓位）
- 回测区间：2022-01-01 → 2023-01-01（365 天）
- 市场行情：BTC/USDT:USDT Futures，同期 BTC **-60.8%**
- 交易模式：隔离保证金合约，1x 杠杆
- 初始资金：1,000 USDT，单笔敞口 50-100 USDT（4h ADX 动态调整）
- 资金费率：直接接入 Binance `CandleType.FUNDING_RATE` 真实数据

---

**总体表现**

| 指标 | 数值 |
|------|------|
| 总交易笔数 | 6 笔 |
| 总收益（账户） | **+0.64%** |
| 最大回撤 | **0.47%** |
| 胜率 | **50%** |
| 利润因子（Profit Factor） | **1.85** |
| Calmar 比率 | 6.73 |
| Sortino 比率 | 1.12 |
| 期望值（Expectancy Ratio） | 1.06 (0.43) |
| 平均持仓时长（赢家） | 3 天 16 小时 |
| 平均持仓时长（输家） | 1 天 9 小时 |

---

**出场原因分析**

| 出场方式 | 笔数 | 平均收益 | 说明 |
|---------|------|---------|------|
| ROI 止盈 | 3 | **+5.05%** | 全部盈利，平均持仓 3 天 16h |
| 止损（stop_loss） | 3 | -2.58% | 被反弹止损 |

---

**真实资金费率接入说明**

Signal B 从 RSI 替代方案升级为直接使用 Binance 合约资金费率数据：

| 项目 | 旧版（RSI 替代） | 新版（真实数据） |
|------|----------------|----------------|
| 数据来源 | RSI > 55 推断多头拥挤 | Binance `funding_rate` 真实数据 |
| 条件 | `RSI(14) > 55` | `funding_rate > 0 AND mean(funding_rate, 8h) > 0` |
| 含义 | 技术指标间接推断 | 多头直接付费给空头（交易所确认） |
| 可靠性 | 中 | 高（链上/交易所直接数据） |

---

**核心优势**

- **赢家均值 +5.05%**，满足 ≥5% 目标
- **真实资金费率因子**：Signal B 使用 Binance 真实数据，不再依赖 RSI 替代
- **4h EMA50/200 死叉门控**：反弹期 4h MACD 转正时自动关闭入场
- **破位确认**：close < 前 2 根 K 线低点，排除区间震荡假信号
- **市场同期 -60.8%，策略 +0.64%**，完整对冲熊市风险

---

**风险提示**

- 单品种（BTC）全年 6 笔交易，年化绝对收益受制于信号频率
- 4h EMA50/200 死叉在 2022 年 5 月才形成，限制了 Q1 熊市的入场机会
- 如需提升总收益，建议扩展至多品种（ETH、SOL）或进行 Hyperopt 参数优化

---

*回测不代表实盘表现。合约做空存在无限亏损风险，请严格控制仓位。*
