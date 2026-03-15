"""
CMN-Short (CryptoMomentumNeutral — Bear Market Short-Only)
==========================================================
v2 changes vs original CMN:
  - SHORT-ONLY mode: N_LONG=0, all capital deployed to short weakest N coins
  - Added beta factor: high-beta coins selected as preferred shorts
  - Reads from freqtrade feather files when available (USE_FREQTRADE_DATA=True)
  - Falls back to Binance public API if files not found
  - Updated robustness sweep: short-only configs

Why short-only beats long/short in bear markets:
  - Long leg always loses in uniform bear (correlation → 1)
  - Short leg earned +53.58% in 2024 bear when standing alone
  - Simpler, more capital-efficient, no beta-neutralization complexity

Bear market periods tested:
  - Period A: 2024-03-15 → 2024-08-01  (BTC 73K → 53K, -27%)
  - Period B: 2025-01-20 → 2025-03-10  (BTC 109K → 79K, -27%)
"""

import os
import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from tabulate import tabulate   # pip install tabulate


# ══════════════════════════════════════════════════════════════════
# 0. CONFIG
# ══════════════════════════════════════════════════════════════════

UNIVERSE = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT", "UNIUSDT",
    "ATOMUSDT", "LTCUSDT", "NEARUSDT", "OPUSDT", "ARBUSDT",
    "APTUSDT", "INJUSDT", "SUIUSDT", "TIAUSDT", "FTMUSDT",
]

BEAR_PERIODS = {
    "2024_bear": ("2024-03-15", "2024-08-01"),
    "2025_bear": ("2025-01-20", "2025-03-10"),
}

TIMEFRAME      = "1h"
LOOKBACK_EXTRA = 150          # extra bars for indicator warmup
N_LONG         = 0            # SHORT-ONLY: no long positions
N_SHORT        = 10           # short weakest 10 coins
FEE_BPS        = 5.0          # 0.05% per side
SLIPPAGE_BPS   = 5.0
INITIAL_CAP    = 100_000
REBAL_EVERY    = 4            # rebalance every 4h bars (optimal from sweep)

# Set to True to read from freqtrade feather files first
USE_FREQTRADE_DATA = True
FREQTRADE_DATA_DIR = "/freqtrade/user_data/data/binance/futures"   # inside Docker
# When running outside Docker, use this local path:
FREQTRADE_DATA_DIR_LOCAL = os.path.join(
    os.path.dirname(__file__), "user_data", "data", "binance", "futures"
)

# Map Binance symbol → freqtrade file prefix
FT_NAME_MAP = {
    "BTCUSDT":  "BTC_USDT_USDT",
    "ETHUSDT":  "ETH_USDT_USDT",
    "BNBUSDT":  "BNB_USDT_USDT",
    "SOLUSDT":  "SOL_USDT_USDT",
    "XRPUSDT":  "XRP_USDT_USDT",
    "ADAUSDT":  "ADA_USDT_USDT",
    "AVAXUSDT": "AVAX_USDT_USDT",
    "DOTUSDT":  "DOT_USDT_USDT",
    "LINKUSDT": "LINK_USDT_USDT",
    "UNIUSDT":  "UNI_USDT_USDT",
    "ATOMUSDT": "ATOM_USDT_USDT",
    "LTCUSDT":  "LTC_USDT_USDT",
    "NEARUSDT": "NEAR_USDT_USDT",
    "OPUSDT":   "OP_USDT_USDT",
    "ARBUSDT":  "ARB_USDT_USDT",
    "APTUSDT":  "APT_USDT_USDT",
    "INJUSDT":  "INJ_USDT_USDT",
    "SUIUSDT":  "SUI_USDT_USDT",
    "TIAUSDT":  "TIA_USDT_USDT",
    "FTMUSDT":  "FTM_USDT_USDT",   # or S_USDT_USDT if rebranded
}


# ══════════════════════════════════════════════════════════════════
# 1A. DATA — freqtrade feather files
# ══════════════════════════════════════════════════════════════════

def load_from_freqtrade(symbols: list, start: str, end: str,
                        lookback_extra: int = 150) -> dict:
    """Read OHLCV from freqtrade feather files. Returns None if unavailable."""
    # Try both Docker and local paths
    data_dir = None
    for candidate in [FREQTRADE_DATA_DIR, FREQTRADE_DATA_DIR_LOCAL]:
        if os.path.isdir(candidate):
            data_dir = candidate
            break

    if data_dir is None:
        return None

    start_dt = (pd.Timestamp(start, tz="UTC")
                - pd.Timedelta(hours=lookback_extra))
    end_dt   = pd.Timestamp(end, tz="UTC")

    prices_d  = {}
    volumes_d = {}
    funding_d = {}

    print(f"\nLoading from freqtrade data [{start} → {end}]...")
    loaded = 0
    for sym in symbols:
        ft_name = FT_NAME_MAP.get(sym)
        if ft_name is None:
            continue

        ohlcv_path = os.path.join(data_dir, f"{ft_name}-1h-futures.feather")
        if not os.path.exists(ohlcv_path):
            print(f"  {sym} ✗ (missing: {ohlcv_path})")
            continue

        df = pd.read_feather(ohlcv_path)
        # freqtrade stores 'date' as datetime or int (ms)
        if df["date"].dtype == object or str(df["date"].dtype).startswith("datetime"):
            df["date"] = pd.to_datetime(df["date"], utc=True)
        else:
            df["date"] = pd.to_datetime(df["date"], unit="ms", utc=True)
        df = df.set_index("date").sort_index()
        df = df[(df.index >= start_dt) & (df.index < end_dt)]

        if df.empty or len(df) < 50:
            print(f"  {sym} ✗ (insufficient data: {len(df)} bars)")
            continue

        ticker = sym.replace("USDT", "")
        prices_d[ticker]  = df["close"]
        volumes_d[ticker] = df["close"] * df["volume"]   # convert vol to USD

        # Try funding rate file
        fund_path = os.path.join(data_dir, f"{ft_name}-1h-funding_rate.feather")
        if os.path.exists(fund_path):
            fdf = pd.read_feather(fund_path)
            if fdf["date"].dtype == object or str(fdf["date"].dtype).startswith("datetime"):
                fdf["date"] = pd.to_datetime(fdf["date"], utc=True)
            else:
                fdf["date"] = pd.to_datetime(fdf["date"], unit="ms", utc=True)
            fdf = fdf.set_index("date").sort_index()
            fdf = fdf[(fdf.index >= start_dt) & (fdf.index < end_dt)]
            if not fdf.empty and "open" in fdf.columns:
                funding_d[ticker] = fdf["open"]

        print(f"  {sym} ✓ ({len(df)} bars)")
        loaded += 1

    if loaded < 5:
        print(f"  Only {loaded} coins loaded from freqtrade data, falling back to API.")
        return None

    prices_df  = pd.DataFrame(prices_d).sort_index()
    volumes_df = pd.DataFrame(volumes_d).sort_index()

    if funding_d:
        fund_raw = pd.DataFrame(funding_d).sort_index()
        funding_df = fund_raw.reindex(prices_df.index, method="ffill").fillna(0.0)
    else:
        funding_df = pd.DataFrame(0.0, index=prices_df.index,
                                  columns=prices_df.columns)

    return {
        "prices":         prices_df,
        "volumes":        volumes_df,
        "funding":        funding_df,
        "backtest_start": start,
    }


# ══════════════════════════════════════════════════════════════════
# 1B. DATA — Binance Public API fallback
# ══════════════════════════════════════════════════════════════════

BASE_URL = "https://fapi.binance.com"

def ts_to_ms(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)

def fetch_klines(symbol: str, interval: str,
                 start_ms: int, end_ms: int) -> pd.DataFrame:
    all_rows = []
    current  = start_ms
    limit    = 1500

    while current < end_ms:
        url = f"{BASE_URL}/fapi/v1/klines"
        params = {
            "symbol": symbol, "interval": interval,
            "startTime": current, "endTime": end_ms, "limit": limit,
        }
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"  [WARN] {symbol}: HTTP {resp.status_code}")
            break
        rows = resp.json()
        if not rows:
            break
        all_rows.extend(rows)
        current = rows[-1][0] + 1
        if len(rows) < limit:
            break
        time.sleep(0.1)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_vol","n_trades",
        "taker_buy_vol","taker_buy_quote","ignore"
    ])
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    for col in ["open","high","low","close","volume","quote_vol"]:
        df[col] = df[col].astype(float)
    df["volume_usd"] = df["quote_vol"]
    return df[["open","high","low","close","volume","volume_usd"]]


def fetch_funding_history(symbol: str,
                          start_ms: int, end_ms: int) -> pd.Series:
    all_rows = []
    current  = start_ms
    while current < end_ms:
        url = f"{BASE_URL}/fapi/v1/fundingRate"
        params = {"symbol": symbol, "startTime": current,
                  "endTime": end_ms, "limit": 1000}
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code != 200:
            break
        rows = resp.json()
        if not rows:
            break
        all_rows.extend(rows)
        current = rows[-1]["fundingTime"] + 1
        if len(rows) < 1000:
            break
        time.sleep(0.1)

    if not all_rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(all_rows)
    df["timestamp"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df["fundingRate"].astype(float)


def load_from_api(symbols: list, start: str, end: str,
                  lookback_extra: int = 150) -> dict:
    start_dt  = pd.Timestamp(start, tz="UTC")
    warmup_dt = start_dt - pd.Timedelta(hours=lookback_extra)
    warmup_str = warmup_dt.strftime("%Y-%m-%d")
    start_ms  = ts_to_ms(warmup_str)
    end_ms    = ts_to_ms(end)

    prices_d  = {}
    volumes_d = {}
    funding_d = {}

    print(f"\nDownloading {len(symbols)} symbols via API [{warmup_str} → {end}]...")
    for sym in symbols:
        print(f"  {sym}", end=" ", flush=True)
        df = fetch_klines(sym, TIMEFRAME, start_ms, end_ms)
        if df.empty:
            print("✗ (no data)")
            continue
        ticker = sym.replace("USDT", "")
        prices_d[ticker]  = df["close"]
        volumes_d[ticker] = df["volume_usd"]
        fr = fetch_funding_history(sym, start_ms, end_ms)
        if not fr.empty:
            funding_d[ticker] = fr
        print(f"✓ ({len(df)} bars)")
        time.sleep(0.15)

    prices_df  = pd.DataFrame(prices_d).sort_index()
    volumes_df = pd.DataFrame(volumes_d).sort_index()

    if funding_d:
        fund_raw = pd.DataFrame(funding_d).sort_index()
        funding_df = fund_raw.reindex(prices_df.index, method="ffill").fillna(0.0)
    else:
        funding_df = pd.DataFrame(0.0, index=prices_df.index,
                                  columns=prices_df.columns)

    return {
        "prices":         prices_df,
        "volumes":        volumes_df,
        "funding":        funding_df,
        "backtest_start": start,
    }


def load_data(symbols: list, start: str, end: str,
              lookback_extra: int = 150) -> dict:
    """Try freqtrade feather files first, fall back to Binance API."""
    if USE_FREQTRADE_DATA:
        result = load_from_freqtrade(symbols, start, end, lookback_extra)
        if result is not None:
            n = len(result["prices"].columns)
            b = len(result["prices"])
            print(f"\n  Loaded: {n} coins × {b} bars")
            print(f"  Date range: {result['prices'].index[0]} → "
                  f"{result['prices'].index[-1]}")
            return result
    return load_from_api(symbols, start, end, lookback_extra)


# ══════════════════════════════════════════════════════════════════
# 2. FACTOR LIBRARY
# ══════════════════════════════════════════════════════════════════

def winsorize(df, q=0.05):
    return df.apply(lambda r: r.clip(r.quantile(q), r.quantile(1-q)), axis=1)

def zscore_cross(df):
    return df.apply(lambda r: (r - r.mean()) / (r.std() + 1e-9), axis=1)

def f_momentum(prices, window):
    return prices.pct_change(window)

def f_rs_btc(prices, window):
    mom = f_momentum(prices, window)
    if "BTC" not in mom.columns:
        return mom.subtract(mom.mean(axis=1), axis=0)
    return mom.subtract(mom["BTC"], axis=0)

def f_volume_pressure(prices, volumes, window=12):
    direction = prices.pct_change().apply(np.sign)
    num = (direction * volumes).rolling(window).sum()
    den = volumes.rolling(window).sum()
    return num / (den + 1e-9)

def f_funding_signal(funding, window=8):
    """Negative: high positive funding → short signal (crowded longs will unwind)."""
    return -funding.rolling(window).mean()

def f_vol_regime(prices, short=4, long=24):
    lr = np.log(prices / prices.shift(1))
    return lr.rolling(short).std() / (lr.rolling(long).std() + 1e-9)

def f_stretch_penalty(prices, window=72):
    z = (prices - prices.rolling(window).mean()) / (prices.rolling(window).std() + 1e-9)
    return -z.abs()

def f_beta_vs_mkt(prices, window=48):
    """
    High rolling beta vs equal-weight market → coin falls MORE in bear market.
    Returns NEGATIVE so high beta → lower alpha score → selected as SHORT.
    Especially useful in 2025 when all coins fall together.
    """
    rets = prices.pct_change()
    mkt  = rets.mean(axis=1)
    betas = {}
    for col in rets.columns:
        cov = rets[col].rolling(window).cov(mkt)
        var = mkt.rolling(window).var()
        betas[col] = cov / (var + 1e-9)
    return -pd.DataFrame(betas, index=prices.index)   # negated: high beta → lower alpha


def compute_alpha(prices, volumes, funding, weights=None) -> pd.DataFrame:
    """
    Composite alpha score. Higher = stronger candidate.
    In SHORT-ONLY mode, we select LOWEST alpha coins as shorts.
    Factors designed so weak/falling coins get LOW alpha.
    """
    w = weights or {
        "mom_4h":  0.20,
        "mom_24h": 0.25,   # most predictive in bear
        "rs_btc":  0.20,
        "vp":      0.10,
        "funding": 0.10,   # crowded longs → short
        "stretch": 0.05,
        "beta":    0.10,   # high beta → falls more → better short (negated above)
    }

    factors = {
        "mom_4h":  zscore_cross(winsorize(f_momentum(prices, 4))),
        "mom_24h": zscore_cross(winsorize(f_momentum(prices, 24))),
        "rs_btc":  zscore_cross(winsorize(f_rs_btc(prices, 24))),
        "vp":      zscore_cross(winsorize(f_volume_pressure(prices, volumes))),
        "funding": zscore_cross(winsorize(f_funding_signal(funding))),
        "stretch": zscore_cross(winsorize(f_stretch_penalty(prices))),
        "beta":    zscore_cross(winsorize(f_beta_vs_mkt(prices))),
    }

    regime = f_vol_regime(prices).clip(0.7, 1.5)

    alpha = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for name, fdf in factors.items():
        fw   = w[name]
        mult = regime if name in ("mom_4h", "mom_24h") else 1.0
        alpha += fw * fdf.multiply(mult) if isinstance(mult, pd.DataFrame) \
                 else fw * fdf

    return zscore_cross(alpha)


# ══════════════════════════════════════════════════════════════════
# 3. PORTFOLIO CONSTRUCTION
# ══════════════════════════════════════════════════════════════════

def build_weights(alpha_row: pd.Series,
                  rv_row: pd.Series,
                  n_long: int, n_short: int,
                  max_pos: float = 0.25) -> pd.Series:
    """
    SHORT-ONLY mode (n_long=0): select bottom-N alpha coins as shorts.
    All capital deployed to short positions (sum of |weights| = 1.0).
    """
    valid  = alpha_row.dropna().sort_values(ascending=False)

    longs  = valid.head(n_long).index.tolist() if n_long > 0 else []
    shorts = valid.tail(n_short).index.tolist()

    # Remove any overlap (shouldn't happen with n_long=0)
    overlap = set(longs) & set(shorts)
    longs   = [t for t in longs  if t not in overlap]
    shorts  = [t for t in shorts if t not in overlap]

    def inv_vol_w(tickers):
        vols = {t: max(rv_row.get(t, 0.02), 1e-4) for t in tickers}
        inv  = {t: 1.0 / v for t, v in vols.items()}
        tot  = sum(inv.values()) + 1e-9
        # Cap max position, then renormalize
        raw  = {t: min(v / tot, max_pos) for t, v in inv.items()}
        s    = sum(raw.values()) + 1e-9
        return {t: v / s for t, v in raw.items()}

    result = {}
    if longs:
        for t, w in inv_vol_w(longs).items():
            result[t] = +w
    if shorts:
        for t, w in inv_vol_w(shorts).items():
            result[t] = result.get(t, 0.0) - w

    return pd.Series(result)


# ══════════════════════════════════════════════════════════════════
# 4. BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════

def run_backtest(data: dict,
                 n_long: int = N_LONG,
                 n_short: int = N_SHORT,
                 fee_bps: float = FEE_BPS,
                 slippage_bps: float = SLIPPAGE_BPS,
                 initial_cap: float = INITIAL_CAP,
                 rebal_every: int = REBAL_EVERY,
                 label: str = "") -> dict:

    prices  = data["prices"].copy()
    volumes = data["volumes"].reindex(columns=prices.columns).fillna(0)
    funding = data["funding"].reindex(columns=prices.columns).fillna(0)
    bt_start = pd.Timestamp(data["backtest_start"], tz="UTC")

    total_cost_rate = (fee_bps + slippage_bps) / 10_000

    valid_cols = prices.columns[prices.isna().mean() < 0.05].tolist()
    prices  = prices[valid_cols].ffill()
    volumes = volumes[valid_cols].ffill().fillna(0)
    funding = funding.reindex(columns=valid_cols).ffill().fillna(0)

    returns = prices.pct_change()

    print(f"\n  [{label}] Computing alpha factors...")
    alpha = compute_alpha(prices, volumes, funding)
    rv    = returns.rolling(24).std()

    all_index = prices.index
    bt_index  = all_index[all_index >= bt_start]

    nav       = initial_cap
    prev_w    = pd.Series(dtype=float)

    nav_series    = []
    long_ret_ser  = []
    short_ret_ser = []
    fund_ser      = []
    turn_ser      = []
    net_ret_ser   = []

    print(f"  [{label}] Running simulation "
          f"({len(bt_index)} bars, rebal every {rebal_every}h)...")

    bt_list = list(bt_index)
    for i, ts in enumerate(bt_list):
        if ts not in alpha.index:
            continue

        if i % rebal_every == 0:
            a_row  = alpha.loc[ts].dropna()
            rv_row = rv.loc[ts].dropna() if ts in rv.index else pd.Series()

            if len(a_row) >= max(n_long + n_short, 1):
                new_w = build_weights(a_row, rv_row, n_long, n_short)
            else:
                new_w = prev_w.copy()
        else:
            new_w = prev_w.copy()

        if i + 1 >= len(bt_list):
            break
        next_ts = bt_list[i + 1]
        if next_ts not in returns.index:
            break

        ret_row  = returns.loc[next_ts]
        gross    = 0.0
        long_pnl = 0.0
        shrt_pnl = 0.0

        for ticker, w in new_w.items():
            if ticker not in ret_row.index or np.isnan(ret_row[ticker]):
                continue
            r   = ret_row[ticker]
            pnl = w * r
            gross += pnl
            if w > 0:
                long_pnl += pnl
            else:
                shrt_pnl += pnl

        # Funding P&L (short positions receive positive funding)
        fund_pnl = 0.0
        if next_ts in funding.index:
            fr_row = funding.loc[next_ts]
            for ticker, w in new_w.items():
                if ticker in fr_row.index:
                    fund_pnl += -w * fr_row[ticker]

        # Turnover cost
        all_t    = set(new_w.index) | set(prev_w.index)
        turnover = sum(abs(new_w.get(t, 0.0) - prev_w.get(t, 0.0))
                       for t in all_t) / 2.0 if not prev_w.empty \
                   else new_w.abs().sum() / 2.0
        cost     = turnover * total_cost_rate
        net      = gross + fund_pnl - cost

        nav = nav * (1 + net)
        nav_series.append(nav)
        long_ret_ser.append(long_pnl)
        short_ret_ser.append(shrt_pnl)
        fund_ser.append(fund_pnl)
        turn_ser.append(turnover)
        net_ret_ser.append(net)

        prev_w = new_w.copy()

    idx = bt_list[1: len(nav_series) + 1]
    return {
        "label":       label,
        "nav":         pd.Series(nav_series, index=idx),
        "net_ret":     pd.Series(net_ret_ser, index=idx),
        "long_ret":    pd.Series(long_ret_ser, index=idx),
        "short_ret":   pd.Series(short_ret_ser, index=idx),
        "funding_ret": pd.Series(fund_ser, index=idx),
        "turnover":    pd.Series(turn_ser, index=idx),
        "btc_ret":     returns["BTC"].reindex(idx) if "BTC" in returns else None,
    }


# ══════════════════════════════════════════════════════════════════
# 5. PERFORMANCE METRICS
# ══════════════════════════════════════════════════════════════════

ANN = 8760   # hours per year

def compute_metrics(result: dict) -> dict:
    r    = result["net_ret"].dropna()
    nav  = result["nav"].dropna()
    lret = result["long_ret"].dropna()
    sret = result["short_ret"].dropna()
    fund = result["funding_ret"].dropna()
    turn = result["turnover"].dropna()

    if len(r) < 10:
        return {"error": "Too few bars"}

    total_ret = nav.iloc[-1] / INITIAL_CAP - 1
    ann_ret   = (1 + total_ret) ** (ANN / len(r)) - 1
    vol       = r.std() * np.sqrt(ANN)
    sharpe    = ann_ret / (vol + 1e-9)
    downside  = r[r < 0].std() * np.sqrt(ANN)
    sortino   = ann_ret / (downside + 1e-9)

    peak   = nav.cummax()
    dd     = (nav - peak) / peak
    max_dd = dd.min()
    calmar = ann_ret / (abs(max_dd) + 1e-9)

    win_rate  = (r > 0).mean()
    profit_f  = abs(r[r > 0].sum() / (r[r < 0].sum() + 1e-9))

    long_total  = (1 + lret).prod() - 1
    short_total = (1 + sret).prod() - 1
    fund_total  = fund.sum()
    avg_turn    = turn.mean()

    btc_ret      = result.get("btc_ret")
    btc_total    = (1 + btc_ret.dropna()).prod() - 1 if btc_ret is not None else None
    alpha_vs_btc = total_ret - btc_total if btc_total is not None else None

    return {
        "total_return":  total_ret,
        "ann_return":    ann_ret,
        "volatility":    vol,
        "sharpe":        sharpe,
        "sortino":       sortino,
        "calmar":        calmar,
        "max_drawdown":  max_dd,
        "win_rate":      win_rate,
        "profit_factor": profit_f,
        "long_total":    long_total,
        "short_total":   short_total,
        "funding_total": fund_total,
        "avg_turnover":  avg_turn,
        "btc_total":     btc_total,
        "alpha_vs_btc":  alpha_vs_btc,
        "n_bars":        len(r),
        "final_nav":     nav.iloc[-1],
    }


def print_report(metrics: dict, label: str):
    print(f"\n{'═'*60}")
    print(f"  RESULTS: {label}")
    print(f"{'═'*60}")
    rows = [
        ["Final NAV",        f"${metrics.get('final_nav', 0):,.0f}"],
        ["Total Return",     f"{metrics.get('total_return', 0):.2%}"],
        ["Ann. Return",      f"{metrics.get('ann_return', 0):.2%}"],
        ["Volatility (ann)", f"{metrics.get('volatility', 0):.2%}"],
        ["Sharpe Ratio",     f"{metrics.get('sharpe', 0):.3f}"],
        ["Sortino Ratio",    f"{metrics.get('sortino', 0):.3f}"],
        ["Calmar Ratio",     f"{metrics.get('calmar', 0):.3f}"],
        ["Max Drawdown",     f"{metrics.get('max_drawdown', 0):.2%}"],
        ["Win Rate",         f"{metrics.get('win_rate', 0):.2%}"],
        ["Profit Factor",    f"{metrics.get('profit_factor', 0):.3f}"],
        ["── Long Leg",      f"{metrics.get('long_total', 0):.2%}"],
        ["── Short Leg",     f"{metrics.get('short_total', 0):.2%}"],
        ["── Funding",       f"{metrics.get('funding_total', 0):.4%}"],
        ["Avg Turnover/bar", f"{metrics.get('avg_turnover', 0):.3f}"],
        ["── BTC Return",    f"{metrics.get('btc_total', 0) or 0:.2%}"],
        ["Alpha vs BTC",     f"{metrics.get('alpha_vs_btc', 0) or 0:.2%}"],
        ["Bars",             str(metrics.get('n_bars', 0))],
    ]
    print(tabulate(rows, tablefmt="simple", colalign=("left", "right")))


# ══════════════════════════════════════════════════════════════════
# 6. ROBUSTNESS SWEEP
# ══════════════════════════════════════════════════════════════════

def robustness_sweep(data: dict, label: str) -> pd.DataFrame:
    """Short-only configurations sweep."""
    configs = [
        {"n_long": 0, "n_short": 5,  "rebal": 1, "fee": 5},    # short-only 5
        {"n_long": 0, "n_short": 7,  "rebal": 1, "fee": 5},    # short-only 7
        {"n_long": 0, "n_short": 10, "rebal": 1, "fee": 5},    # short-only 10 (base)
        {"n_long": 0, "n_short": 10, "rebal": 4, "fee": 5},    # slow rebal
        {"n_long": 0, "n_short": 10, "rebal": 1, "fee": 10},   # higher fees
        {"n_long": 0, "n_short": 15, "rebal": 1, "fee": 5},    # wider net
    ]

    rows = []
    for cfg in configs:
        try:
            r = run_backtest(
                data,
                n_long=cfg["n_long"], n_short=cfg["n_short"],
                rebal_every=cfg["rebal"],
                fee_bps=cfg["fee"],
                label="sweep",
            )
            m = compute_metrics(r)
            rows.append({
                "N_short": cfg["n_short"],
                "rebal_h": cfg["rebal"],
                "fee_bps": cfg["fee"],
                "total_ret": f"{m['total_return']:.2%}",
                "sharpe":    f"{m['sharpe']:.3f}",
                "max_dd":    f"{m['max_drawdown']:.2%}",
                "short":     f"{m['short_total']:.2%}",
                "vs_btc":    f"{(m.get('alpha_vs_btc') or 0):.2%}",
            })
        except Exception as e:
            rows.append({"error": str(e)})

    df = pd.DataFrame(rows)
    print(f"\n{'─'*60}")
    print(f"  ROBUSTNESS SWEEP: {label}")
    print(f"{'─'*60}")
    print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
    return df


# ══════════════════════════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════╗")
    print("║  CMN-Short — Bear Market Short-Only Backtest  v2     ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"  Mode: SHORT-ONLY  N_SHORT={N_SHORT}  N_LONG={N_LONG}")
    print(f"  Fees: {FEE_BPS}bps + {SLIPPAGE_BPS}bps slippage")

    all_results = {}

    for period_name, (start, end) in BEAR_PERIODS.items():
        print(f"\n\n{'▓'*60}")
        print(f"  PERIOD: {period_name}  [{start} → {end}]")
        print(f"{'▓'*60}")

        data = load_data(UNIVERSE, start, end, lookback_extra=LOOKBACK_EXTRA)

        n_coins = len(data["prices"].columns)
        n_bars  = len(data["prices"])
        if not USE_FREQTRADE_DATA or data.get("source") == "api":
            print(f"\n  Loaded: {n_coins} coins × {n_bars} bars")
            print(f"  Date range: {data['prices'].index[0]} → "
                  f"{data['prices'].index[-1]}")

        if n_coins < N_SHORT:
            print(f"  ✗ Not enough coins ({n_coins} < {N_SHORT}). Skipping.")
            continue

        result  = run_backtest(
            data, n_long=N_LONG, n_short=N_SHORT,
            fee_bps=FEE_BPS, slippage_bps=SLIPPAGE_BPS,
            label=period_name,
        )
        metrics = compute_metrics(result)
        print_report(metrics, period_name)

        robustness_sweep(data, period_name)

        all_results[period_name] = {"result": result, "metrics": metrics}

    if len(all_results) > 1:
        print(f"\n\n{'═'*60}")
        print("  CROSS-PERIOD SUMMARY")
        print(f"{'═'*60}")
        rows = []
        for name, d in all_results.items():
            m = d["metrics"]
            rows.append([
                name,
                f"{m['total_return']:.2%}",
                f"{m['sharpe']:.3f}",
                f"{m['max_drawdown']:.2%}",
                f"{m['short_total']:.2%}",
                f"{(m.get('alpha_vs_btc') or 0):.2%}",
            ])
        print(tabulate(rows,
            headers=["Period", "Return", "Sharpe", "MaxDD", "Short Leg", "vs BTC"],
            tablefmt="simple"))

    print("\n\nDone.")
