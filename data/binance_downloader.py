"""
Binance OHLCV data downloader using ccxt.
Downloads 1-minute candles, supports incremental updates,
resampling to higher timeframes, and local caching in parquet.
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd

DATA_RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
DATA_PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"


def get_exchange():
    return ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })


def download_ohlcv(
    symbol: str,
    timeframe: str = "1m",
    since: str = "2025-01-01",
    until: str | None = None,
    data_dir: Path | None = None,
) -> pd.DataFrame:
    """Download OHLCV data from Binance and save to parquet."""
    exchange = get_exchange()
    data_dir = data_dir or DATA_RAW_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    since_ts = int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    until_ts = (
        int(datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        if until
        else int(datetime.now(timezone.utc).timestamp() * 1000)
    )

    safe_symbol = symbol.replace("/", "_")
    parquet_path = data_dir / f"{safe_symbol}_{timeframe}.parquet"

    # Incremental update: load existing data and continue from last timestamp
    if parquet_path.exists():
        existing = pd.read_parquet(parquet_path)
        if not existing.empty:
            last_ts = int(existing["timestamp"].iloc[-1].timestamp() * 1000)
            if last_ts >= until_ts:
                print(f"  {symbol} already up to date ({len(existing)} rows)")
                return existing
            since_ts = last_ts + 60000  # next minute
            print(f"  {symbol} incremental from {pd.Timestamp(since_ts, unit='ms', tz='UTC')}")
    else:
        existing = pd.DataFrame()

    all_data = []
    current = since_ts
    limit = 1000

    print(f"  Downloading {symbol} {timeframe} from {pd.Timestamp(since_ts, unit='ms', tz='UTC')}")

    while current < until_ts:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=current, limit=limit)
        except Exception as e:
            print(f"    Error fetching {symbol}: {e}")
            time.sleep(2)
            continue

        if not ohlcv:
            break

        all_data.extend(ohlcv)
        current = ohlcv[-1][0] + 60000  # next minute after last candle

        if len(ohlcv) < limit:
            break

        time.sleep(exchange.rateLimit / 1000)

    if not all_data:
        print(f"    No new data for {symbol}")
        return existing if not existing.empty else pd.DataFrame()

    df = pd.DataFrame(all_data, columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
    df = df.drop(columns=["timestamp_ms"])
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # Merge with existing
    if not existing.empty:
        df = pd.concat([existing, df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    df.to_parquet(parquet_path, index=False)
    print(f"    Saved {len(df)} rows to {parquet_path.name}")
    return df


def load_ohlcv(symbol: str, timeframe: str = "1m", data_dir: Path | None = None) -> pd.DataFrame:
    """Load cached OHLCV data from parquet."""
    data_dir = data_dir or DATA_RAW_DIR
    safe_symbol = symbol.replace("/", "_")
    parquet_path = data_dir / f"{safe_symbol}_{timeframe}.parquet"
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    return pd.DataFrame()


def resample_ohlcv(df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Resample 1m OHLCV to higher timeframe (3m, 5m, 15m, 1h, etc)."""
    if df.empty:
        return df

    tf_map = {
        "1m": "1min", "3m": "3min", "5m": "5min",
        "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D",
    }
    freq = tf_map.get(target_tf, target_tf)

    df_indexed = df.set_index("timestamp").sort_index()

    resampled = df_indexed.resample(freq).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()

    resampled = resampled.reset_index()
    return resampled


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean OHLCV data: remove duplicates, fix missing timestamps, forward-fill gaps."""
    if df.empty:
        return df

    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # Forward-fill small gaps (up to 5 minutes)
    df = df.set_index("timestamp")
    full_idx = pd.date_range(df.index.min(), df.index.max(), freq="1min", tz="UTC")
    df = df.reindex(full_idx)
    df = df.ffill(limit=5)
    df = df.dropna()
    df = df.reset_index().rename(columns={"index": "timestamp"})

    return df


def download_universe(
    symbols: list[str],
    timeframe: str = "1m",
    since: str = "2025-01-01",
    until: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Download OHLCV for a list of symbols."""
    results = {}
    for sym in symbols:
        try:
            df = download_ohlcv(sym, timeframe, since, until)
            if not df.empty:
                results[sym] = df
        except Exception as e:
            print(f"  Failed {sym}: {e}")
    return results


def export_for_freqtrade(df: pd.DataFrame, symbol: str, timeframe: str = "5m"):
    """Export data in Freqtrade-compatible format (feather)."""
    out_dir = DATA_PROCESSED_DIR / "freqtrade"
    out_dir.mkdir(parents=True, exist_ok=True)

    ft_name = symbol.replace("/", "_").replace(":", "_")
    path = out_dir / f"{ft_name}-{timeframe}-spot.feather"

    export_df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    export_df = export_df.rename(columns={"timestamp": "date"})
    export_df.to_feather(path)
    print(f"  Exported {path.name}")


if __name__ == "__main__":
    # Quick test
    symbols = ["BTC/USDT", "ETH/USDT"]
    for sym in symbols:
        df = download_ohlcv(sym, "1m", since="2025-03-01", until="2025-03-02")
        print(f"{sym}: {len(df)} rows")
        if not df.empty:
            resampled = resample_ohlcv(df, "5m")
            print(f"  5m resampled: {len(resampled)} rows")
