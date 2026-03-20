# Crypto Strategy Executor Prompt v4
**As of March 20, 2026**

## Purpose
LLM-driven crypto strategy execution engine for 10-day hackathon competition.
System must monitor market data, compute indicators, apply predefined rules, and execute trades deterministically.

---

## Architecture Overview

```
market_data (OHLCV)
    ↓
[Layer 1] BTC Regime Filter (30m or 1h)
    ↓
[Layer 2] Momentum Pre-Screen (15m / 30m proxy)
    ↓
[Layer 3] Microstructure Execution (5m / 30m)
    ↓
trade_decision (entry/exit/hold)
```

---

## Core Framework: Freqtrade IStrategy v3

**Platform**: Freqtrade (not Jesse)
**Timeframes**: 5m, 30m (primary execution)
**API**: Binance Spot (no leverage, no shorting)
**Execution Model**: Signal fires at bar close → execute at next bar open

---

## Seven Strategy Portfolio

### 1️⃣ Bear Market Long-Only (Daily, 9 coins)
**Purpose**: Capital preservation in bear markets via regime filtering.
**Universe**: BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOT, LINK

**Entry Rules** (ALL required):
- BTC > EMA(200) AND EMA(50) > EMA(200) → regime ON, else 100% cash
- Coin: price > EMA(100), ret20d > 0, ret60d > 0
- Ranking: composite_score = 0.25×ret20 + 0.20×ret60 + 0.20×ema_dist + 0.35×risk_adj_momentum
- Hold bonus: +0.10 to current holdings (reduce turnover)
- Allocation: Top 1–2 coins, inverse-volatility weighted, max 60% per coin
- Entry at daily close

**Exit Rules**:
- ATR trailing stop: price < (peak - 2.5× ATR)
- EMA/momentum break: price < EMA(100) OR ret20d ≤ 0
- Regime OFF: BTC < EMA(200) → 100% cash
- Re-entry delay: 2 bars confirmation

**Position Sizing**: Inverse volatility, capped 60% single position
**Rebalance**: Daily
**ROI**: Trailing stop primary; gradual decay targets (not critical)

---

### 2️⃣ Crypto Breakout Long-Only 30m (7 coins)
**Purpose**: Volatile/recovery market trading via 6h breakout + volume.
**Universe**: BTC, ETH, SOL, BNB, XRP, DOGE, ADA

**Entry Rules** (ALL required):
- BTC close > BTC MA(20) [V2 faster response vs V1's MA50]
- Coin price > highest_high(48 bars) [6h = 96 × 30m bars]
- Coin price > MA(50)
- Volume > 1.5× vol_MA(40 bars / 20h)
- RSI(14) > 55
- ATR% < 1.5× recent_ATR_avg [volatility regime filter]
- Single bar move |Δ%| < 8% [anti-chase]
- Execution: next bar open

**Exit Rules** (ANY triggers):
- Price < lowest_low(24 bars) [4h = 12h]
- Price < entry × 0.95 [5% hard stop]
- Price < peak × 0.90 [10% trailing from high]
- ROI: 20% instant / 15% @30h / 10% @60h / 5% @120h
- Portfolio equity ≥ +20% → close ALL, halt [competition target]

**Position Sizing**: Equal capital per slot, max 3 open
**Fee Model**: 0.1% round-trip assumed

---

### 3️⃣ FeeOptMomentum5m (Variable universe, Freqtrade)
**Purpose**: Fee-first execution with 3-layer signal architecture.
**Min Edge Gate**: +0.50% net minimum per trade (6× maker round-trip buffer)

**Layer 1: BTC Regime (30m)**
- STRONG_BULL (2): BTC > EMA_fast & > EMA_slow, 3h return > +0.5%
- WEAK_BULL (1): BTC > EMA_slow, 3h return > -1%
- NEUTRAL (0): near SMA50 (±2%), 3h return > -2%
- BEAR (-1): below SMA50 OR accelerating down
- Emergency: BTC 3h drop > -4% → force taker exit

**Layer 2: Momentum Pre-Screen (15m proxy via rolling windows)**
- 6h momentum > +0.5%
- Volume > 1.5× baseline (10h avg)
- Price > EMA(20)
- RSI(7) ∈ [45, 78]
- Composite momentum_score = 0.4×ret20 + 0.3×ret24h + 0.15×vol_expansion + 0.15×breakout_dist

**Layer 3: 5m Execution (Fee-aware)**
- Mode A – Micro-breakout: close > 30m_high + volume surge + RSI momentum
- Mode B – Pullback continuation: already broke 15m, pulls to EMA5/VWAP, resumes
- Anti-chase: skip bars with |Δ%| > 3%
- Minimum edge gate: momentum_score > 2.0 [no noise trades]

**Entry**:
- Initial stake: 40% of budget
- Scale-in: +60% when floating profit > +0.3% [breakout confirmed]

**Exit** (priority order):
1. Hard stop: -0.6%
2. Regime fail: BTC turns BEAR → taker exit
3. Momentum decay: MACD flip + volume fade
4. EMA cross-down: EMA5 < EMA20
5. Trailing: +0.6% → trail -0.8% from peak
6. Time stop: 60min no progress & <0.3% profit → cut at -0.3%
7. ROI tiers: +0.6% @10h / +0.9% @5h / +1.2% @2.5h / +1.5% instant

**Parameters** (sensitive):
- MIN_EDGE: 0.005 (0.5% minimum score)
- MOM_THRESHOLD: 0.005 (6h coin return)
- VOL_SURGE_15M: 1.5
- EXEC_BREAKOUT: 6 bars (30m channel)
- TRAILING_STOP_PCT: 0.008 (0.8%)
- TIME_STOP_BARS: 12 (60 min)

---

### 4️⃣ MarchBreaker30m (20 coins, Competition)
**Purpose**: +20% return in 10-day spot-only competition.
**Dynamic Rotation**: Top-6 by 48h momentum only

**Universe** (20 coins, rotation selects top-6):
- AI/Infra: TAO, RENDER, FET, INJ, NEAR, SEI
- High-β L1: SOL, SUI, APT, ARB, OP, AVAX
- Meme: PEPE, BONK, DOGE, NOT
- Gaming: IMX, GALA, MANA, AXS

**Entry Rules** (ALL required):
- Coin in Top-6 by 48h momentum
- Close > highest_high(12) [6h]
- Close > MA(20)
- Volume > 2.0× vol_MA(20) [stronger threshold]
- 48 < RSI(7) < 82
- |Δ%| < 20% [anti-chase]
- BTC > MA(50) [macro bullish]

**Exit Rules** (ANY triggers):
- Close < lowest_low(8) [4h]
- Close < entry × 0.95 [5%]
- Close < peak × 0.92 [8% trailing]
- ROI: 20% instant / 15% @30h / 10% @60h / 5% @120h
- Portfolio equity ≥ +20% → close ALL [competition target]

**Position Sizing**: Max 5, equal capital
**Halt Condition**: +20% portfolio TP reached → lock in, stop all trading

---

### 5️⃣ MemeRotationAggressive5m (7-14 Meme coins, 5m)
**Purpose**: Fast meme momentum capture via cross-sectional rotation.
**Three Parameter Variants**: HIGH_FREQ / BALANCED / MAX_RETURN

**Core Logic**:
- At every 5m bar, rank all coins by momentum score
- Select Top-N (typically 2-3) by absolute momentum thresholds
- Only these Top-N are eligible for NEW entries
- Existing positions exit independently on technicals

**Momentum Score** (approximation for per-coin ranking):
```
score = 0.6 × ret_1h + 0.4 × ret_4h
```
- ret_1h = close / close_12bars_ago - 1
- ret_4h = close / close_48bars_ago - 1

**Entry Rules** (ALL required):
- ret_1h > 1.0–2.0% [variant-dependent, BALANCED=1.0%]
- ret_4h > 1.5–3.0% [variant-dependent, BALANCED=1.5%]
- Momentum score > threshold [variant-dependent, BALANCED=1.2%]
- Close > breakout_high(12 bars)
- Close > EMA(21)
- Close > rolling_VWAP(48 bars)
- Volume > 1.5–2.0× baseline [variant-dependent]
- RSI(7) ∈ [50–76, strict]
- Body% < 5–8% [anti-chase, variant-dependent]
- BTC regime ≥ NEUTRAL [at minimum]
- In neutral BTC regime: tighten momentum 1.5×

**Exit Rules** (ANY triggers):
- Close < exit_low(6 bars)
- RSI(7) < 32–40 [collapse, variant-dependent]
- EMA(9) cross-under
- VWAP breakdown + low break
- Time stop: 90min (18 bars) without 0.5% progress
- Profit protection: if ever +3%, now <1% → exit
- Trailing stop: 2–3% from peak [variant-dependent]
- Hard stop: 2.5–4.0% [variant-dependent]
- ROI ladder: +6% @0 / +4% @30min / +2.5% @60min

**Variants**:
- **HIGH_FREQ**: ret_1h > 0.5%, looser RSI, tighter trail (1.5%), max 3 positions
- **BALANCED** (default): ret_1h > 1.0%, moderate filters, 2% trail, 2 positions
- **MAX_RETURN**: ret_1h > 2.0%, strict momentum, loose trailing (2.5%), higher drawdown tolerance

**Position Sizing**: Regime-scaled
- Bullish BTC: 100% of slot
- Neutral BTC: 70% of slot
- Risk-off: 50% of slot (or block entirely)

---

### 6️⃣ Dynamic Cross-Sectional Rotation (All 65 competition pairs, 5m)
**Purpose**: Real-time rotation across entire competition universe.
**Core**: Rank all 65 coins at every 5m bar by composite momentum.

**Momentum Scoring**:
```
score = 0.6 × ret_1h + 0.4 × ret_4h
```
- Absolute minimum: score > 3.0% [only trade genuinely strong coins]
- Select: Top-N by score (typically N=3) that also exceed MIN_SCORE

**Entry Rules**:
- Coin in Top-N by 5m score AND score > 3.0% absolute
- Price > highest_high(24 bars) [2h]
- Price > EMA(21)
- Price > rolling_VWAP(48 bars)
- Volume > 2.0× baseline
- RSI(7) ∈ [58, 82]
- Body% < 6%
- BTC regime ≥ NEUTRAL [strictly enforced]

**Exit Rules**:
- Trailing stop: 4% from peak
- Hard stop: 4.5%
- Time stop: 3h (36 bars) without 0.5% progress
- ROI: 15% instant / 10% @36bars / 6% @72bars / 3% @108bars
- RSI collapse: < 32

**Position Sizing**: Max 2 simultaneous
**Fee Model**: 0.1% round-trip + 0.05% slippage

---

### 7️⃣ MemeRotation Standalone (7 meme coins, 5m standalone engine)
**Purpose**: Simplified meme rotation for production deployment (no freqtrade dependency).
**Coins**: FETUSDT, CHZUSDT, BONKUSDT, SHIBUSDT, NOTUSDT, FLOKIUSDT, (±PEPEUSDT)

**Entry**:
- ret_1h > 1.2%, ret_4h > 1.8%, score > 1.4%
- Price > high(12), > EMA(21), > VWAP
- Vol > 1.8×, RSI ∈ [55, 78], body < 5%
- BTC bullish OR neutral (regime ≥ 1)

**Exit**:
- Trailing 3% from peak, hard stop 3.5%
- Time stop: 90min < 0.5% profit
- Profit protect: peak +3%, now <1%
- ROI: 10% / 6% @30min / 4% @60min / 2.5% @120min

**Variants**: BALANCED (default), HIGH_FREQ, MAX_RETURN with parameter overrides

---

## Indicator Library (All Available)

### Trend
- EMA(span) – exponential moving average
- SMA(window) – simple moving average
- Supertrend(period, mult)

### Momentum
- RSI(period) – relative strength index [0-100]
- MACD(fast, slow, signal) → (macd_line, signal, histogram)
- Momentum = close / close[N bars ago] - 1

### Volatility
- ATR(period) – average true range
- ATR% = ATR / close
- Bollinger Bands(period, std_dev)

### Volume
- Volume ratio = current_vol / SMA_vol
- Volume surge = ratio > threshold

### Price Action
- Highest_high(lookback) – max high over N bars (shift-1 for no look-ahead)
- Lowest_low(lookback) – min low over N bars (shift-1)
- Breakout = close > highest_high

### Composite
- VWAP(window) = Σ(typical_price × volume) / Σ(volume)
- Candle body% = |close - open| / open
- Regime score (BTC): based on price vs EMA + recent return

---

## Execution Rules (Universal)

### Signal Timing
- Signal fires at bar **CLOSE**
- Execution at next bar **OPEN**
- No look-ahead bias: use shift(1) on rolling max/min

### Regime Gates
- BTC/macro regime determines position sizing & entry eligibility
- HARD risk-off blocks NEW entries entirely
- Neutral regime requires 1.5× stricter momentum filters

### Position Sizing
- Inverse volatility: lower-vol coins → higher weight (capped per coin)
- Regime scale: bullish=100%, neutral=70%, risk-off=50%
- Max positions: 2–5 depending on strategy
- Equal capital allocation per slot (rebalance on entry/exit)

### Fee Constraints
- Assume 0.1% maker + 0.1% slippage per round-trip = 0.2% total cost
- Minimum expected return per trade: ≥0.4% net (to break even + margin)
- Use only maker orders where possible; emergency exits use taker

### Time Stops
- If trade open > T minutes (30–90 depending on timeframe)
  AND profit < target (0.3–0.5%)
  → **force exit** to recycle capital
- Rationale: meme/breakout moves are fast or stall entirely

### Profit Protection
- If trade ever reached peak_profit% → now < entry_profit%
  → **exit immediately** (secondary trailing logic)
- Example: hit +3%, now at +1% → exit (was running 300bps profit, gave back 200bps)

---

## Decision Tree (Pseudocode)

```python
for each_5m_or_30m_bar:
    # 1. Update all indicators
    compute_BTC_regime()
    compute_coin_returns_1h_4h()
    compute_momentum_score()

    # 2. Exit checks (every position)
    for position in open_positions:
        if check_hard_stop() or check_trailing_stop():
            EXIT(position)
        elif check_time_stop():
            EXIT(position)
        elif check_profit_protect():
            EXIT(position)
        elif check_exit_signal():
            EXIT(position)

    # 3. Entry signal (if slots available)
    if BTC_regime >= NEUTRAL and available_slots > 0:
        for coin in universe:
            if pass_regime_gate() and \
               pass_momentum_gate() and \
               pass_technical_gate():
                ENTER(coin, sized_by_regime)
```

---

## Backtest Specifications

### Data
- **Source**: Binance Spot (public API)
- **Timeframes**: 5m / 30m primary; 1h for BTC regime
- **History**: 200–300 bar warmup (50–150h depending on timeframe)
- **Fee Model**: 0.1% taker + 0.05% slippage = 0.15% one-way

### Validation
- **Walk-forward**: Test IS (2021–2023) vs OOS (2024–2026)
- **Robustness**: Vary key parameters ±20%, check alpha degradation
- **Regime stress**: Isolated bear / bull / chop periods

---

## Implementation Notes for Engineers

### For Freqtrade IStrategy
1. Implement `populate_indicators()` to compute all required signals
2. Set `informative_pairs()` to include BTC/USDT at different timeframes
3. Use `populate_entry_trend()` to mark `enter_long = 1` only when ALL conditions true
4. Use `populate_exit_trend()` for exit_signal OR rely on `custom_stoploss()`
5. Implement `custom_stake_amount()` for regime-based position sizing
6. Use `adjust_trade_position()` for scale-in logic (if supported)
7. Enable `use_exit_signal = True` and `use_custom_stoploss = True`

### For Standalone Engine
1. Fetch OHLCV from Binance REST API (cache locally)
2. Maintain rolling indicator state (deques for O(1) updates)
3. Simulate next-bar-open execution via position tracking
4. Record all trades: entry_price, exit_price, pnl_pct, bars_held, exit_reason
5. Compute metrics: total_return, max_dd, sharpe, win_rate, profit_factor

### Key Gotchas
- **Look-ahead bias**: Always use `shift(1)` on rolling max/min
- **Regime lag**: BTC regime from 1h bars → apply to 5m bars via floor-and-backfill
- **Fee bleed**: High turnover strategies need strict entry filters; looseness bleeds alpha
- **Cross-sectional ranking**: Freqtrade per-pair isolation → approximate with per-coin absolute thresholds OR use standalone engine
- **Timestamp alignment**: All pairs may not have candles at same timestamp; use last-valid-observation-carried-forward (LOCF)

---

## Competition Target

**Objective**: +20% net return in 10-day window (Mar 21–31, 2026).
**Capital**: $10k initial (spot only, no leverage).
**Exit Condition**: Portfolio equity ≥ $12k → close ALL positions, halt trading.

**Strategy Selection for Competition**:
1. **Primary**: MarchBreaker30m OR Dynamic Cross-Sectional Rotation (both engineered for +20% target)
2. **Secondary**: MemeRotationAggressive5m-BALANCED (high trade count, consistent win rate)
3. **Ensemble**: Run multiple strategies on disjoint coin subsets; aggregate into single portfolio

---

## Version History

- **v4 (Mar 20, 2026)**: Unified 7-strategy prompt; freqtrade-native; fee-first design; cross-sectional rotation support
- **v3**: Initial 3-layer architecture (regime + momentum + execution)
- **v2**: Added volatility regime filter + MA20 BTC response
- **v1**: Baseline breakout + trailing stop framework

---

**Prompt author**: Jesse / Vance
**Framework**: Freqtrade IStrategy v3 + Standalone CCXT engine
**Target audience**: Quant engineers implementing skills & strategies
**Last updated**: 2026-03-20
