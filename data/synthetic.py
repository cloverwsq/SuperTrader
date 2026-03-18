"""
Synthetic OHLCV data generator for backtesting when Binance API is unavailable.
Generates realistic crypto price action with various regimes.
"""

import numpy as np
import pandas as pd
from pathlib import Path


def generate_ohlcv(
    symbol: str = "BTC/USDT",
    days: int = 30,
    start_price: float = 80000,
    volatility: float = 0.0003,   # per-minute vol
    trend: float = 0.0,           # drift per minute
    seed: int | None = None,
) -> pd.DataFrame:
    """
    Generate realistic 1-minute OHLCV data.

    Args:
        symbol: pair name (for labeling)
        days: number of days
        start_price: initial price
        volatility: per-minute volatility (std of returns)
        trend: drift per minute
        seed: random seed for reproducibility
    """
    if seed is not None:
        np.random.seed(seed)

    n_bars = days * 24 * 60
    timestamps = pd.date_range(
        start="2025-01-01", periods=n_bars, freq="1min", tz="UTC"
    )

    # Generate returns with regime changes
    returns = np.random.normal(trend, volatility, n_bars)

    # Add occasional momentum bursts (more frequent for meme coins)
    burst_rate = 0.005 if volatility < 0.0005 else 0.015
    burst_mask = np.random.random(n_bars) < burst_rate
    burst_sizes = np.random.choice([3, 4, -2, -1.5], size=burst_mask.sum())
    returns[burst_mask] *= burst_sizes

    # Add volume-clustered momentum (bursts come in sequences)
    for i in range(len(returns)):
        if burst_mask[i] and i + 5 < len(returns):
            # Continuation bars after burst
            for j in range(1, min(6, len(returns) - i)):
                returns[i + j] += returns[i] * 0.3 * np.exp(-j * 0.5)

    prices_raw = start_price * np.exp(np.cumsum(returns))

    # Generate OHLCV from close prices
    data = []
    for i in range(n_bars):
        close = prices_raw[i]
        intra_vol = abs(returns[i]) * close * 2
        high = close + abs(np.random.normal(0, max(intra_vol, close * 0.0001)))
        low = close - abs(np.random.normal(0, max(intra_vol, close * 0.0001)))
        open_price = prices_raw[i - 1] if i > 0 else start_price

        # Ensure OHLC consistency
        high = max(high, open_price, close)
        low = min(low, open_price, close)

        # Volume correlated with absolute return
        base_vol = 100
        vol_mult = 1 + abs(returns[i]) / volatility
        volume = base_vol * vol_mult * (1 + np.random.exponential(0.5))

        data.append({
            "timestamp": timestamps[i],
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    return pd.DataFrame(data)


def generate_universe(days: int = 30, seed: int = 42) -> dict[str, pd.DataFrame]:
    """Generate synthetic data for a full universe of pairs."""
    np.random.seed(seed)

    configs = {
        # Majors: lower vol, slight uptrend
        "BTC/USDT":  {"start_price": 82000, "volatility": 0.00025, "trend": 0.000002},
        "ETH/USDT":  {"start_price": 3200,  "volatility": 0.00035, "trend": 0.000001},
        "SOL/USDT":  {"start_price": 140,   "volatility": 0.00045, "trend": 0.000003},
        "BNB/USDT":  {"start_price": 600,   "volatility": 0.00030, "trend": 0.000001},
        "XRP/USDT":  {"start_price": 2.3,   "volatility": 0.00040, "trend": 0.000000},
        "ADA/USDT":  {"start_price": 0.75,  "volatility": 0.00042, "trend": -0.000001},

        # Meme: higher vol, more bursts
        "DOGE/USDT": {"start_price": 0.25,  "volatility": 0.00060, "trend": 0.000005},
        "SHIB/USDT": {"start_price": 0.000022, "volatility": 0.00070, "trend": 0.000003},
        "PEPE/USDT": {"start_price": 0.000012, "volatility": 0.00080, "trend": 0.000008},
        "WIF/USDT":  {"start_price": 1.5,   "volatility": 0.00075, "trend": 0.000004},
        "FLOKI/USDT": {"start_price": 0.00015, "volatility": 0.00072, "trend": 0.000002},
    }

    data = {}
    for sym, cfg in configs.items():
        sym_seed = seed + hash(sym) % 10000
        df = generate_ohlcv(sym, days=days, seed=sym_seed, **cfg)
        data[sym] = df
        print(f"  Generated {sym}: {len(df):,} bars, "
              f"price {df['close'].iloc[0]:.6f} -> {df['close'].iloc[-1]:.6f}")

    return data


if __name__ == "__main__":
    print("Generating synthetic universe (30 days, 1m bars)...")
    data = generate_universe(days=30)
    print(f"\nTotal: {len(data)} pairs generated")

    # Save to parquet
    raw_dir = Path(__file__).parent.parent / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for sym, df in data.items():
        safe = sym.replace("/", "_")
        path = raw_dir / f"{safe}_1m.parquet"
        df.to_parquet(path, index=False)
        print(f"  Saved {path.name}")
