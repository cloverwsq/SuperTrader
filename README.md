# SuperTrader

Freqtrade workspace for running `SampleStrategy` in dry-run / paper-trading mode.

## Active files

- `docker-compose.yml`: starts the Freqtrade bot container.
- `paper_monitor.ps1`: polls live logs and the SQLite trade DB during paper trading.
- `user_data/config.json`: Freqtrade runtime configuration.
- `user_data/strategies/sample_strategy.py`: active strategy used by the bot.
- `user_data/logs/freqtrade.log`: main bot runtime log.
- `user_data/logs/paper_monitor.log`: compact monitoring snapshots.
- `user_data/tradesv3.sqlite`: dry-run trade database.
- `user_data/backtest_results/`: exported backtest result JSON files.

## Removed duplicates

- redundant experimental backtest scripts were removed in favor of the live Freqtrade workflow
- empty placeholder files were removed
- generated Python bytecode cache was removed
