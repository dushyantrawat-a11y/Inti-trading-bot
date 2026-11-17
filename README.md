# Trading bot upgrade: signals, backtests, risk controls, auto-discovery and dashboard

This repository includes an ITB pipeline scaffold and a standalone Yahoo Finance example in `trading_bot.py`. The standalone script creates EMA20/EMA50 crossover signals filtered by RSI, uses ATR-based stop/target levels, makes a historical backtest, and writes a chart. It is long-only and **paper only**; it has no live brokerage integration.

## Yahoo signal/backtest example

Install the optional dependencies:

```shell
python -m pip install -r requirements-yahoo-bot.txt
```

Backtest:

```shell
python trading_bot.py --symbol RELIANCE.NS --mode backtest --period 2y --interval 1d
```

One current-data paper step (the option is named `live` for compatibility, but never sends real orders):

```shell
python trading_bot.py --symbol RELIANCE.NS --mode live --period 2y --interval 1d
```

The backtest enters on the next bar's open after a closed-bar signal. It models configured fee and slippage assumptions, share-sized risk (default 1%), 2×ATR stop and 3×ATR target; if both stop and target touch in one candle, stop-loss is assumed first. Results are saved to `backtest_trades.csv`, and chart to `chart.png`. Paper account state is local in `paper_state.json`; use `--state-file` to select another state file.

Yahoo Finance data/availability may be delayed or incomplete. This strategy and its backtest are educational, are not financial advice, and cannot guarantee returns. Simulated performance is not live performance. Review market-specific lot sizes, taxes, costs, gaps, and execution behavior before relying on any result.

## Existing ITB / MT5 + Binance scaffold

The existing pipeline and scanner material is described below. MT5 scanning requires a configured Windows terminal; market candidates must be reviewed before enabling. The project is experimental, paper-oriented, and does not guarantee profits.

- `bot.py`: MT5/Binance paper scanner and backtest CLI
- `core.py`: signal and risk/backtest utilities
- `autoconfig.py`: market candidate discovery
- `dashboard.py`: local read-only Streamlit monitor
- `itb/`: ITB pipeline and trainable-feature scaffolding

Never commit broker passwords, exchange keys, or Telegram tokens.
