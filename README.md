# SuperTrader — Multi-Strategy Crypto Trading System

Roostoo Hackathon competition system: research, backtest, and deploy long-only crypto strategies optimized for **Composite Risk-Adjusted Score** (0.4 Sortino + 0.3 Sharpe + 0.3 Calmar).

## Quick Start

```bash
pip install -r requirements.txt

# Run with synthetic data (no API needed)
python run_backtest.py --synthetic

# Run with real Binance data
python run_backtest.py --since 2025-01-01 --until 2025-03-15

# Run single strategy
python run_backtest.py --synthetic --strategy breakout_stable
```

## Architecture

```
1m Binance data (base)
  ├── resample → 30m signals → 5m execution  (Breakout Stable)
  ├── resample → 15m signals → 1m execution  (Breakout Meme)
  ├── resample → 5m signals  → 1m execution  (Momentum Scalp)
  └── resample → 1h signals  → 5m execution  (Bear Long-Only)
```

All strategies are **LONG-ONLY** per competition rules.

## Strategies

| Strategy | Signal TF | Exec TF | Description |
|---|---|---|---|
| `breakout_stable` | 30m | 5m | Breakout above rolling high for liquid majors |
| `breakout_meme` | 15m | 1m | Explosive breakout for meme/high-beta coins |
| `momentum_scalp` | 5m | 1m | Short-term momentum continuation scalping |
| `bear_longonly` | 1h | 5m | Regime-filtered: oversold rebound + trend participation |

## Project Structure

```
SuperTrader/
├── run_backtest.py          # Main runner
├── data/
│   ├── binance_downloader.py  # Binance 1m OHLCV downloader (ccxt)
│   ├── universe.py            # Pair classification (major/meme/mid-tier)
│   └── synthetic.py           # Synthetic data generator
├── strategies/
│   ├── breakout_stable.py     # Strategy 1A
│   ├── breakout_meme.py       # Strategy 1B
│   ├── momentum_scalp.py      # Strategy 2
│   └── bear_longonly.py       # Strategy 3
├── backtests/
│   └── engine.py              # Backtest engine + metrics
├── user_data/strategies/      # Freqtrade-compatible versions
│   ├── BreakoutStableFT.py
│   ├── BreakoutMemeFT.py
│   ├── MomentumScalpFT.py
│   └── BearLongOnlyFT.py
├── configs/
│   └── backtest_config.json
├── reports/                   # Generated backtest results
└── requirements.txt
```

## Evaluation Metrics

- **Sharpe Ratio**: risk-adjusted return vs total volatility
- **Sortino Ratio**: risk-adjusted return vs downside volatility (most important, 40% weight)
- **Calmar Ratio**: annualized return / max drawdown
- **Composite Score**: `0.4 × Sortino + 0.3 × Sharpe + 0.3 × Calmar`

## Data Pipeline

- Downloads 1-minute OHLCV from Binance via ccxt
- Supports incremental updates (only fetches new data)
- Stores locally in parquet format
- Resamples to any higher timeframe (3m/5m/15m/30m/1h)
- Synthetic data fallback for testing without API access

## Freqtrade Integration

Each strategy has a Freqtrade-compatible version in `user_data/strategies/` using:
- `@informative` decorator for multi-timeframe
- `populate_indicators` / `populate_entry_trend` / `populate_exit_trend`
- Hyperoptable parameters

## Notes

- Use Binance for historical research/backtesting only
- Code is modular so live execution can later be switched to Roostoo API
- Optimize for Composite Score, not raw return
- All results include realistic fees (0.05% maker, 0.10% round-trip)
