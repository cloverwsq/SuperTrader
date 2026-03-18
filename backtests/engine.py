"""
Custom backtesting engine for LONG-ONLY crypto strategies.
Supports: multi-timeframe signals, fees, slippage, stop-loss, take-profit,
trailing stop, time stop, cooldown, max concurrent positions, position sizing.
Computes Sharpe/Sortino/Calmar/Composite Score per competition rules.

Architecture:
  - Signal generated on higher TF (e.g. 30m)
  - Execution simulated on lower TF (e.g. 5m or 1m)
  - All data originates from 1m base, resampled as needed
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    maker_fee: float = 0.0005       # 0.05%
    taker_fee: float = 0.0005       # 0.05%
    slippage: float = 0.0002        # 0.02%
    stop_loss: float = 0.03         # 3%
    take_profit: float = 0.05       # 5%
    trailing_stop: float = 0.0      # 0 = disabled
    trailing_activation: float = 0.01
    time_stop_bars: int = 0         # 0 = disabled (in execution TF bars)
    cooldown_bars: int = 2          # bars between trades (execution TF)
    max_positions: int = 3
    position_size_pct: float = 0.33
    risk_free_rate: float = 0.0


@dataclass
class Trade:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    size: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    holding_bars: int = 0
    fees_paid: float = 0.0
    peak_price: float = 0.0


def _close_trade(trade: Trade, exit_price: float, exit_time, reason: str,
                 cfg: BacktestConfig) -> float:
    """Close a trade. Returns capital freed (size + net_pnl)."""
    exit_price_adj = exit_price * (1 - cfg.slippage)
    exit_fee = trade.size * (exit_price_adj / trade.entry_price) * cfg.taker_fee
    gross_pnl = trade.size * (exit_price_adj / trade.entry_price - 1)
    net_pnl = gross_pnl - exit_fee

    trade.exit_time = exit_time
    trade.exit_price = exit_price_adj
    trade.pnl = net_pnl
    trade.pnl_pct = net_pnl / trade.size
    trade.exit_reason = reason
    trade.fees_paid += exit_fee
    return trade.size + net_pnl


def run_backtest(
    signals_by_symbol: dict[str, pd.DataFrame],
    config: BacktestConfig | None = None,
) -> dict:
    """
    Run LONG-ONLY backtest on signal DataFrames.

    Each DataFrame in signals_by_symbol must have columns:
      timestamp, open, high, low, close, volume,
      entry_signal (1=buy), exit_signal (1=sell)

    Returns dict with trades, equity_curve, metrics.
    """
    cfg = config or BacktestConfig()
    capital = cfg.initial_capital
    open_positions: dict[str, Trade] = {}
    all_trades: list[Trade] = []
    cooldown_until: dict[str, int] = {}

    # Pre-index all DataFrames by timestamp for O(1) lookups
    indexed: dict[str, pd.DataFrame] = {}
    for sym, df in signals_by_symbol.items():
        df_i = df.set_index("timestamp").sort_index()
        indexed[sym] = df_i

    # Build unified timeline
    all_timestamps = sorted(set(
        ts for df in indexed.values() for ts in df.index.tolist()
    ))

    equity_values = []
    equity_times = []

    for bar_idx, ts in enumerate(all_timestamps):
        # ── Check exits first ──
        to_close = []
        for sym, trade in open_positions.items():
            df_i = indexed.get(sym)
            if df_i is None or ts not in df_i.index:
                continue
            row = df_i.loc[ts]
            price = row["close"]
            low = row["low"]
            high = row["high"]
            trade.holding_bars += 1
            trade.peak_price = max(trade.peak_price, high)

            exit_reason = ""
            exit_price = price

            # Stop loss check (uses low of bar)
            if cfg.stop_loss > 0:
                sl_price = trade.entry_price * (1 - cfg.stop_loss)
                if low <= sl_price:
                    exit_reason = "stop_loss"
                    exit_price = sl_price

            # Take profit check (uses high of bar)
            if not exit_reason and cfg.take_profit > 0:
                tp_price = trade.entry_price * (1 + cfg.take_profit)
                if high >= tp_price:
                    exit_reason = "take_profit"
                    exit_price = tp_price

            # Trailing stop
            if not exit_reason and cfg.trailing_stop > 0:
                act_price = trade.entry_price * (1 + cfg.trailing_activation)
                if trade.peak_price >= act_price:
                    trail_price = trade.peak_price * (1 - cfg.trailing_stop)
                    if low <= trail_price:
                        exit_reason = "trailing_stop"
                        exit_price = trail_price

            # Time stop
            if not exit_reason and cfg.time_stop_bars > 0:
                if trade.holding_bars >= cfg.time_stop_bars:
                    exit_reason = "time_stop"
                    exit_price = price

            # Exit signal
            if not exit_reason and row.get("exit_signal", 0) == 1:
                exit_reason = "signal"
                exit_price = price

            if exit_reason:
                freed = _close_trade(trade, exit_price, ts, exit_reason, cfg)
                capital += freed
                to_close.append(sym)
                all_trades.append(trade)
                cooldown_until[sym] = bar_idx + cfg.cooldown_bars

        for sym in to_close:
            del open_positions[sym]

        # ── Check entries ──
        for sym, df_i in indexed.items():
            if sym in open_positions:
                continue
            if len(open_positions) >= cfg.max_positions:
                break
            if cooldown_until.get(sym, 0) > bar_idx:
                continue

            if ts not in df_i.index:
                continue
            row = df_i.loc[ts]

            if row.get("entry_signal", 0) != 1:
                continue

            entry_price = row["close"] * (1 + cfg.slippage)
            pos_size = min(capital * cfg.position_size_pct, capital * 0.95)
            if pos_size < 10:
                continue

            entry_fee = pos_size * cfg.maker_fee
            capital -= pos_size

            open_positions[sym] = Trade(
                symbol=sym,
                entry_time=ts,
                entry_price=entry_price,
                size=pos_size,
                peak_price=entry_price,
                fees_paid=entry_fee,
            )

        # ── Mark-to-market equity ──
        equity = capital
        for trade in open_positions.values():
            df_i = indexed.get(trade.symbol)
            if df_i is not None and ts in df_i.index:
                equity += trade.size * (df_i.loc[ts, "close"] / trade.entry_price)
            else:
                equity += trade.size

        equity_values.append(equity)
        equity_times.append(ts)

    # Close remaining at last price
    for sym, trade in open_positions.items():
        df_i = indexed.get(sym)
        if df_i is not None and not df_i.empty:
            freed = _close_trade(trade, df_i.iloc[-1]["close"], df_i.index[-1],
                                 "end_of_data", cfg)
            all_trades.append(trade)

    equity_series = pd.Series(equity_values, index=pd.DatetimeIndex(equity_times))

    return {
        "trades": all_trades,
        "equity_curve": equity_series,
        "config": cfg,
    }


# ═══════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════

def compute_metrics(bt_result: dict) -> dict:
    """Compute all competition-required performance metrics."""
    trades = bt_result["trades"]
    equity = bt_result["equity_curve"]
    cfg = bt_result["config"]

    if not trades or equity.empty:
        return {"error": "No trades or empty equity curve"}

    initial = cfg.initial_capital
    final = equity.iloc[-1]
    total_return = (final - initial) / initial

    # ── Daily returns for ratio computation ──
    daily_eq = equity.resample("1D").last().dropna()
    daily_ret = daily_eq.pct_change().dropna()

    if len(daily_ret) < 3:
        # Fallback to hourly
        hourly_eq = equity.resample("1h").last().dropna()
        returns = hourly_eq.pct_change().dropna()
        ann_factor = np.sqrt(365 * 24)
    else:
        returns = daily_ret
        ann_factor = np.sqrt(365)

    mean_ret = returns.mean()
    std_ret = returns.std() + 1e-10

    # Sharpe
    sharpe = (mean_ret / std_ret) * ann_factor

    # Sortino
    downside = returns[returns < 0]
    downside_std = downside.std() if len(downside) > 1 else 1e-10
    sortino = (mean_ret / (downside_std + 1e-10)) * ann_factor

    # Max Drawdown
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    max_dd = abs(drawdown.min()) if not drawdown.empty else 0

    # Calmar
    n_days = max((equity.index[-1] - equity.index[0]).days, 1)
    ann_return = (1 + total_return) ** (365 / n_days) - 1
    calmar = ann_return / (max_dd + 1e-10)

    # Composite Score (competition formula)
    composite = 0.4 * sortino + 0.3 * sharpe + 0.3 * calmar

    # ── Trade stats ──
    completed = [t for t in trades if t.exit_time is not None]
    winners = [t for t in completed if t.pnl > 0]
    win_rate = len(winners) / len(completed) if completed else 0

    pnls = [t.pnl_pct for t in completed]
    avg_ret = np.mean(pnls) if pnls else 0
    med_ret = np.median(pnls) if pnls else 0
    avg_hold = np.mean([t.holding_bars for t in completed]) if completed else 0
    total_fees = sum(t.fees_paid for t in completed)

    # PnL by symbol
    pnl_by_sym = {}
    for t in completed:
        pnl_by_sym[t.symbol] = pnl_by_sym.get(t.symbol, 0) + t.pnl

    # PnL by day
    pnl_by_day = {}
    for t in completed:
        if t.exit_time:
            d = str(t.exit_time.date())
            pnl_by_day[d] = pnl_by_day.get(d, 0) + t.pnl

    return {
        "total_return": total_return,
        "net_return": total_return,
        "ann_return": ann_return,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_dd,
        "composite_score": composite,
        "win_rate": win_rate,
        "num_trades": len(completed),
        "avg_return_per_trade": avg_ret,
        "median_return_per_trade": med_ret,
        "avg_holding_bars": avg_hold,
        "total_fees": total_fees,
        "final_equity": final,
        "pnl_by_symbol": pnl_by_sym,
        "pnl_by_day": pnl_by_day,
        "equity_curve": equity,
        "drawdown_curve": drawdown,
    }


def format_results_table(all_metrics: dict[str, dict]) -> pd.DataFrame:
    """Format into the required comparison table."""
    rows = []
    for name, m in all_metrics.items():
        if "error" in m:
            continue
        rows.append({
            "Strategy": name,
            "Net Return": f"{m['net_return']:.2%}",
            "Sharpe": f"{m['sharpe']:.3f}",
            "Sortino": f"{m['sortino']:.3f}",
            "Calmar": f"{m['calmar']:.3f}",
            "Max DD": f"{m['max_drawdown']:.2%}",
            "Trades": m["num_trades"],
            "Win Rate": f"{m['win_rate']:.2%}",
            "Composite": f"{m['composite_score']:.3f}",
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["_sort"] = [float(r.replace(",", "")) for r in df["Composite"]]
        df = df.sort_values("_sort", ascending=False).drop(columns=["_sort"])
    return df


def print_metrics(metrics: dict, label: str = ""):
    """Print metrics formatted."""
    if "error" in metrics:
        print(f"  {label}: {metrics['error']}")
        return

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    rows = [
        ("Net Return",       f"{metrics['net_return']:.2%}"),
        ("Ann. Return",      f"{metrics['ann_return']:.2%}"),
        ("Sharpe Ratio",     f"{metrics['sharpe']:.3f}"),
        ("Sortino Ratio",    f"{metrics['sortino']:.3f}"),
        ("Calmar Ratio",     f"{metrics['calmar']:.3f}"),
        ("Max Drawdown",     f"{metrics['max_drawdown']:.2%}"),
        ("COMPOSITE SCORE",  f"{metrics['composite_score']:.3f}"),
        ("Win Rate",         f"{metrics['win_rate']:.2%}"),
        ("Trades",           f"{metrics['num_trades']}"),
        ("Avg Ret/Trade",    f"{metrics['avg_return_per_trade']:.4%}"),
        ("Med Ret/Trade",    f"{metrics['median_return_per_trade']:.4%}"),
        ("Avg Hold (bars)",  f"{metrics['avg_holding_bars']:.1f}"),
        ("Total Fees",       f"${metrics['total_fees']:.2f}"),
        ("Final Equity",     f"${metrics['final_equity']:.2f}"),
    ]
    for k, v in rows:
        print(f"  {k:<20s} {v:>12s}")
