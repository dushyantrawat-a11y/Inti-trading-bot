# Trading bot upgrade: signals, backtests, risk controls, auto-discovery and dashboard

This application scans reviewed MT5 and Binance symbols and simulates trades locally. **It does not place live orders.** Do not treat generated signals as financial advice.

## Setup

Python 3.10+:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.yaml config.yaml
```

MT5 scanning requires Windows, the MetaTrader 5 terminal, and a broker login. Binance public market data needs network access but no API keys. For MT5 credentials, use `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, and optionally `MT5_PATH` environment variables. Never commit secrets.

## Buy/sell signals and paper scanner

Review `config.yaml`, use exact broker symbol names, and set `enabled: true` only for markets you intend to scan. Run:

```powershell
python bot.py --config config.yaml
```

Signals are based on EMA crossovers; available order-book imbalance can act as a confirmation filter. MT5 order-book/tick data varies by broker, and Binance depth is a snapshot. These are imperfect proxies, not reliable predictions. Signals append to `signals.csv`; paper trade events go to `trades.csv` and state to `paper_state.json`.

## Historical backtesting

```powershell
python bot.py --config config.yaml --backtest --provider binance --symbol BTCUSDT --bars 1000
python bot.py --config config.yaml --backtest --provider mt5 --symbol EURUSD --bars 5000
```

Results are written to `backtest_results.json`. The backtest enters at the next bar's open after a closed-bar EMA cross, includes configured fees/slippage, and assumes the stop is hit first if both stop and target occur within one candle. Historical results are not predictive; validate with out-of-sample data and realistic costs. Binance endpoint limits restrict maximum requested bars per call.

## Risk controls

`core.RiskEngine` enforces per-trade risk budget, daily loss, peak-to-trough drawdown, maximum aggregate open risk, notional cap, and open-position limit. Conservative defaults are provided. MT5 position sizing is refused if the broker does not provide usable tick-value metadata. Sizing and P/L remain approximations, especially across account currencies and contract types.

## Auto-discover markets

```powershell
python autoconfig.py --provider both --quotes USDT,USDC --limit 100
```

This writes `config.discovered.yaml` with discovered candidates **disabled**. Review symbols, thresholds, permissions and costs; copy chosen entries into `config.yaml` and enable manually. MT5 discovery requires an initialized terminal. Discovery does not mean a market is suitable or liquid enough for a strategy.

## Dashboard

The local read-only Streamlit dashboard shows latest signals, paper positions, trade events, and latest backtest:

```powershell
streamlit run dashboard.py
```

Do not expose it to the public internet; it is intended for local use only. It reads the local CSV/JSON files and does not control the bot.

## Safety

Paper mode must remain enabled. Live order execution is not implemented. Paper execution does not fully model spread, partial fills, market impact, funding, exchange outages, or slippage. Stop losses can gap. This software is educational, not investment advice, and cannot guarantee returns.
