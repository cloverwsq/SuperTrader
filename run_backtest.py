#!/usr/bin/env python3
"""
SuperTrader — Multi-Strategy Crypto Trading System
Main backtest runner for Roostoo Hackathon

Usage:
  python run_backtest.py                    # Full run: download + backtest all
  python run_backtest.py --skip-download    # Skip data download, use cached
  python run_backtest.py --strategy breakout_stable  # Run single strategy
  python run_backtest.py --pairs BTC/USDT ETH/USDT   # Custom pairs
  python run_backtest.py --synthetic               # Use synthetic data (no API needed)

Architecture:
  - Downloads 1m OHLCV from Binance
  - Resamples to signal timeframes (5m/15m/30m/1h) per strategy
  - Runs LONG-ONLY backtests with realistic fees
  - Computes Sharpe/Sortino/Calmar/Composite Score
  - Generates comparison report
"""

import sys
import os
import json
import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from tabulate import tabulate

# Project imports
sys.path.insert(0, str(Path(__file__).parent))

from data.binance_downloader import download_ohlcv, load_ohlcv, resample_ohlcv, clean_data
from data.synthetic import generate_universe
from data.universe import (
    get_universe, MAJORS, MEME_COINS, MID_TIER, classify_pair, compute_pair_stats
)
from backtests.engine import (
    run_backtest, compute_metrics, format_results_table, print_metrics, BacktestConfig
)
from strategies import breakout_stable, breakout_meme, momentum_scalp, bear_longonly


# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════

DEFAULT_SINCE = "2025-01-01"
DEFAULT_UNTIL = "2025-03-15"

# Which pairs to use per strategy
STRATEGY_PAIRS = {
    "breakout_stable": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"],
    "breakout_meme": ["DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT", "FLOKI/USDT"],
    "momentum_scalp": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT"],
    "bear_longonly": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "ADA/USDT"],
}

STRATEGY_MODULES = {
    "breakout_stable": breakout_stable,
    "breakout_meme": breakout_meme,
    "momentum_scalp": momentum_scalp,
    "bear_longonly": bear_longonly,
}


# ═══════════════════════════════════════════════════════════════
# DATA DOWNLOAD
# ═══════════════════════════════════════════════════════════════

def download_all_data(pairs: list[str], since: str, until: str) -> dict[str, pd.DataFrame]:
    """Download 1m data for all pairs."""
    print(f"\n{'='*60}")
    print(f"  DOWNLOADING 1m DATA: {len(pairs)} pairs")
    print(f"  Period: {since} -> {until}")
    print(f"{'='*60}")

    data = {}
    for pair in pairs:
        try:
            df = download_ohlcv(pair, "1m", since, until)
            if not df.empty:
                df = clean_data(df)
                data[pair] = df
                print(f"  {pair}: {len(df):,} rows")
        except Exception as e:
            print(f"  {pair}: FAILED ({e})")

    print(f"\n  Total: {len(data)} pairs downloaded")
    return data


def load_cached_data(pairs: list[str]) -> dict[str, pd.DataFrame]:
    """Load cached 1m data."""
    data = {}
    for pair in pairs:
        df = load_ohlcv(pair, "1m")
        if not df.empty:
            data[pair] = df
    return data


# ═══════════════════════════════════════════════════════════════
# STRATEGY RUNNERS
# ═══════════════════════════════════════════════════════════════

def run_strategy(
    strategy_name: str,
    all_data: dict[str, pd.DataFrame],
    params: dict | None = None,
) -> dict:
    """Run a single strategy across its assigned pairs."""
    module = STRATEGY_MODULES[strategy_name]
    pairs = STRATEGY_PAIRS[strategy_name]
    config = module.get_default_config()

    print(f"\n{'─'*60}")
    print(f"  Running: {strategy_name}")
    print(f"  Pairs: {', '.join(pairs)}")
    print(f"{'─'*60}")

    signals_by_symbol = {}
    for pair in pairs:
        if pair not in all_data:
            print(f"    {pair}: no data, skipping")
            continue

        df_1m = all_data[pair].copy()
        try:
            df_signals = module.generate_signals(df_1m, params)
            n_entry = (df_signals["entry_signal"] == 1).sum()
            n_exit = (df_signals["exit_signal"] == 1).sum()
            print(f"    {pair}: {n_entry} entries, {n_exit} exits")
            signals_by_symbol[pair] = df_signals
        except Exception as e:
            print(f"    {pair}: signal error ({e})")

    if not signals_by_symbol:
        return {"error": "No valid signals generated"}

    bt_result = run_backtest(signals_by_symbol, config)
    metrics = compute_metrics(bt_result)
    return metrics


# ═══════════════════════════════════════════════════════════════
# UNIVERSE ANALYSIS
# ═══════════════════════════════════════════════════════════════

def analyze_universe(all_data: dict[str, pd.DataFrame]):
    """Compute and display universe statistics."""
    print(f"\n{'='*60}")
    print("  UNIVERSE ANALYSIS")
    print(f"{'='*60}")

    stats = compute_pair_stats(all_data)
    if stats.empty:
        print("  No stats computed")
        return

    print(tabulate(
        stats[["symbol", "classification", "realized_vol", "avg_volume_usd", "burst_freq"]].head(20),
        headers="keys",
        tablefmt="simple",
        showindex=False,
        floatfmt=(".0f", ".0f", ".3f", ",.0f", ".4f"),
    ))


# ═══════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════

def generate_report(all_metrics: dict[str, dict], output_dir: Path):
    """Generate comparison report and save results."""
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("  STRATEGY COMPARISON REPORT")
    print(f"{'='*60}")

    # Print individual results
    for name, m in all_metrics.items():
        print_metrics(m, name)

    # Comparison table
    table = format_results_table(all_metrics)
    if not table.empty:
        print(f"\n{'='*60}")
        print("  RANKING (by Composite Score)")
        print(f"{'='*60}")
        print(tabulate(table, headers="keys", tablefmt="grid", showindex=False))

    # ── Answer research questions ──
    print(f"\n{'='*60}")
    print("  RESEARCH ANSWERS")
    print(f"{'='*60}")

    valid = {k: v for k, v in all_metrics.items() if "error" not in v}
    if valid:
        # Q1: Which strategy performs best?
        best_composite = max(valid, key=lambda k: valid[k]["composite_score"])
        print(f"\n  Q1. Best strategy overall: {best_composite}")
        print(f"      Composite Score: {valid[best_composite]['composite_score']:.3f}")

        # Q2: Does scalping beat fees?
        if "momentum_scalp" in valid:
            m = valid["momentum_scalp"]
            beats_fees = m["net_return"] > 0
            print(f"\n  Q2. Momentum scalping after fees: {'YES' if beats_fees else 'NO'}")
            print(f"      Net return: {m['net_return']:.2%}, Fees paid: ${m['total_fees']:.2f}")

        # Q3: Best Composite Score
        print(f"\n  Q3. Highest Composite Score: {best_composite} ({valid[best_composite]['composite_score']:.3f})")

        # Q4: Lowest drawdown
        best_dd = min(valid, key=lambda k: valid[k]["max_drawdown"])
        print(f"\n  Q4. Lowest drawdown: {best_dd}")
        print(f"      Max DD: {valid[best_dd]['max_drawdown']:.2%}")
        print(f"      Calmar: {valid[best_dd]['calmar']:.3f}")
        print(f"      Sortino: {valid[best_dd]['sortino']:.3f}")

        # Q6: Robustness
        best_return = max(valid, key=lambda k: valid[k]["net_return"])
        print(f"\n  Q5. Best return: {best_return} ({valid[best_return]['net_return']:.2%})")

    # ── Deployment recommendation ──
    print(f"\n{'='*60}")
    print("  DEPLOYMENT RECOMMENDATION")
    print(f"{'='*60}")

    if valid:
        ranked = sorted(valid.items(), key=lambda x: x[1]["composite_score"], reverse=True)
        print("\n  Recommended shortlist (top strategies by Composite Score):")
        for i, (name, m) in enumerate(ranked[:3], 1):
            print(f"    {i}. {name}: Composite={m['composite_score']:.3f}, "
                  f"Return={m['net_return']:.2%}, MaxDD={m['max_drawdown']:.2%}")

        print("\n  Caveats:")
        print("    - Results based on historical Binance data, past performance != future")
        print("    - Live execution will have different latency/slippage")
        print("    - Recommend paper-trading before competition deployment")
        print("    - Monitor regime changes and switch strategies accordingly")

    # ── Save results ──
    results_json = {}
    for name, m in all_metrics.items():
        # Remove non-serializable items
        clean = {k: v for k, v in m.items() if k not in ["equity_curve", "drawdown_curve"]}
        # Convert numpy types
        for k, v in clean.items():
            if isinstance(v, (np.floating, np.integer)):
                clean[k] = float(v)
            elif isinstance(v, dict):
                clean[k] = {str(kk): float(vv) if isinstance(vv, (np.floating, np.integer)) else vv
                            for kk, vv in v.items()}
        results_json[name] = clean

    results_path = output_dir / "backtest_results.json"
    with open(results_path, "w") as f:
        json.dump(results_json, f, indent=2, default=str)
    print(f"\n  Results saved to: {results_path}")

    if not table.empty:
        table_path = output_dir / "strategy_comparison.csv"
        table.to_csv(table_path, index=False)
        print(f"  Comparison table saved to: {table_path}")

    # Save equity curves
    for name, m in all_metrics.items():
        if "equity_curve" in m and not m["equity_curve"].empty:
            eq_path = output_dir / f"equity_{name}.csv"
            m["equity_curve"].to_csv(eq_path, header=["equity"])

    return table


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="SuperTrader Backtest Runner")
    parser.add_argument("--skip-download", action="store_true", help="Use cached data")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic data (no API)")
    parser.add_argument("--synthetic-days", type=int, default=30, help="Days of synthetic data")
    parser.add_argument("--strategy", type=str, help="Run single strategy")
    parser.add_argument("--since", type=str, default=DEFAULT_SINCE)
    parser.add_argument("--until", type=str, default=DEFAULT_UNTIL)
    parser.add_argument("--pairs", nargs="+", help="Override pairs")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  SuperTrader — Multi-Strategy Crypto Trading System     ║")
    print("║  Roostoo Hackathon | LONG-ONLY | Composite Score Opt   ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"  Period: {args.since} → {args.until}")
    print(f"  Fees: 0.05% maker (0.10% round-trip)")

    # Determine all unique pairs needed
    if args.pairs:
        all_pairs = args.pairs
        for k in STRATEGY_PAIRS:
            STRATEGY_PAIRS[k] = args.pairs
    else:
        all_pairs = list(set(p for pairs in STRATEGY_PAIRS.values() for p in pairs))

    print(f"  Pairs: {len(all_pairs)} unique")

    # Download or load data
    if args.synthetic:
        print(f"\n  Generating synthetic data ({args.synthetic_days} days)...")
        all_data = generate_universe(days=args.synthetic_days, seed=42)
        # Filter to needed pairs
        all_data = {k: v for k, v in all_data.items() if k in all_pairs}
        print(f"  Generated {len(all_data)} pairs")
    elif args.skip_download:
        print("\n  Loading cached data...")
        all_data = load_cached_data(all_pairs)
        if not all_data:
            print("  ERROR: No cached data found. Run without --skip-download first.")
            return
    else:
        all_data = download_all_data(all_pairs, args.since, args.until)
        if not all_data:
            print("  WARNING: API download failed, falling back to synthetic data...")
            all_data = generate_universe(days=30, seed=42)
            all_data = {k: v for k, v in all_data.items() if k in all_pairs}

    # Universe analysis
    analyze_universe(all_data)

    # Run strategies
    strategies_to_run = (
        [args.strategy] if args.strategy
        else list(STRATEGY_MODULES.keys())
    )

    all_metrics = {}
    for strat_name in strategies_to_run:
        if strat_name not in STRATEGY_MODULES:
            print(f"  Unknown strategy: {strat_name}")
            continue
        try:
            metrics = run_strategy(strat_name, all_data)
            all_metrics[strat_name] = metrics
        except Exception as e:
            print(f"  {strat_name}: ERROR ({e})")
            import traceback
            traceback.print_exc()
            all_metrics[strat_name] = {"error": str(e)}

    # Generate report
    output_dir = Path(__file__).parent / "reports"
    generate_report(all_metrics, output_dir)

    print("\n\nDone.")


if __name__ == "__main__":
    main()
