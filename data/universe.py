"""
Universe classification for tradable crypto pairs.
Classifies pairs into: majors, meme/high-beta, mid-tier.
"""

import numpy as np
import pandas as pd
from pathlib import Path

# ── Hardcoded classification based on Binance ecosystem ──────────────

MAJORS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT", "LTC/USDT",
]

MEME_COINS = [
    "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT", "FLOKI/USDT",
    "BONK/USDT", "MEME/USDT", "1000SATS/USDT", "TURBO/USDT", "NEIRO/USDT",
]

MID_TIER = [
    "NEAR/USDT", "UNI/USDT", "ATOM/USDT", "OP/USDT", "ARB/USDT",
    "APT/USDT", "INJ/USDT", "SUI/USDT", "TIA/USDT", "FTM/USDT",
    "SEI/USDT", "FET/USDT", "MATIC/USDT", "ALGO/USDT", "FIL/USDT",
]

# Full universe
ALL_PAIRS = MAJORS + MEME_COINS + MID_TIER

# Denylist: stablecoins and tokens with known issues
DENYLIST = ["USDC/USDT", "BUSD/USDT", "TUSD/USDT", "DAI/USDT", "FDUSD/USDT"]


def classify_pair(symbol: str) -> str:
    """Classify a pair into major/meme/mid-tier."""
    if symbol in MAJORS:
        return "major"
    elif symbol in MEME_COINS:
        return "meme"
    elif symbol in MID_TIER:
        return "mid_tier"
    return "unknown"


def compute_pair_stats(ohlcv_data: dict[str, pd.DataFrame], window: int = 20) -> pd.DataFrame:
    """
    Compute classification metrics for each pair from OHLCV data.
    Returns DataFrame with: symbol, volatility, avg_volume, burst_freq, classification.
    """
    records = []
    for symbol, df in ohlcv_data.items():
        if df.empty or len(df) < window * 2:
            continue

        close = df["close"].values
        volume = df["volume"].values

        # Realized volatility (annualized from 1m)
        returns = np.diff(np.log(close))
        realized_vol = np.std(returns) * np.sqrt(525600)  # minutes per year

        # Average volume (USD proxy)
        avg_vol = np.mean(close * volume)

        # Price burst frequency: how often does 5m return exceed 1%
        if len(close) >= 5:
            returns_5m = close[5:] / close[:-5] - 1
            burst_freq = np.mean(np.abs(returns_5m) > 0.01)
        else:
            burst_freq = 0

        # Mean reversion tendency: autocorrelation of returns
        if len(returns) > 10:
            ret_series = pd.Series(returns)
            autocorr = ret_series.autocorr(lag=1)
        else:
            autocorr = 0

        records.append({
            "symbol": symbol,
            "classification": classify_pair(symbol),
            "realized_vol": realized_vol,
            "avg_volume_usd": avg_vol,
            "burst_freq": burst_freq,
            "mean_reversion": autocorr,
        })

    stats_df = pd.DataFrame(records)
    if not stats_df.empty:
        stats_df = stats_df.sort_values("realized_vol", ascending=False)
    return stats_df


def get_universe(category: str = "all") -> list[str]:
    """Get list of pairs by category."""
    if category == "major":
        return MAJORS.copy()
    elif category == "meme":
        return MEME_COINS.copy()
    elif category == "mid_tier":
        return MID_TIER.copy()
    elif category == "all":
        return ALL_PAIRS.copy()
    elif category == "stable_liquid":
        return MAJORS.copy()
    return ALL_PAIRS.copy()


def filter_by_volume(stats_df: pd.DataFrame, min_volume_usd: float = 100_000) -> list[str]:
    """Filter pairs by minimum average volume."""
    mask = stats_df["avg_volume_usd"] >= min_volume_usd
    return stats_df.loc[mask, "symbol"].tolist()


def filter_by_volatility(stats_df: pd.DataFrame, min_vol: float = 0.3, max_vol: float = 5.0) -> list[str]:
    """Filter pairs by volatility range."""
    mask = (stats_df["realized_vol"] >= min_vol) & (stats_df["realized_vol"] <= max_vol)
    return stats_df.loc[mask, "symbol"].tolist()


if __name__ == "__main__":
    print("Universe Classification:")
    print(f"  Majors ({len(MAJORS)}): {', '.join(MAJORS[:5])}...")
    print(f"  Meme ({len(MEME_COINS)}): {', '.join(MEME_COINS[:5])}...")
    print(f"  Mid-tier ({len(MID_TIER)}): {', '.join(MID_TIER[:5])}...")
    print(f"  Total: {len(ALL_PAIRS)} pairs")
