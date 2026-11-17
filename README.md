# Trading bot for both MT5 and crypto markets

This project is a market scanner and paper-trading bot designed for two sources:

- MetaTrader 5 (forex/indices/CFDs)
- Binance spot markets (crypto)

It evaluates a configurable market allowlist, checks spread and liquidity, scores buyer/seller pressure from tick flow or order-book depth, then computes a position using a risk budget and a stop-loss / take-profit plan. The default mode is paper trading; live execution is disabled unless you explicitly turn it on.

## Features

- Supports both MetaTrader 5 and Binance
- Allows multiple symbols from both markets in the same config
- Checks spread, volume, and liquidity before entering a trade
- Uses order-book pressure or tick imbalance as a buyer/seller signal
- Sizes positions from account risk, not a fixed lot size
- Uses stop-loss and reward/risk logic
- Stores paper-trade state locally
- Logs decisions to `bot.log` and `trades.csv`

## Requirements

- Python 3.10+
- Windows + MetaTrader 5 terminal for MT5 trading
- Internet access for Binance REST market data
- Demo account recommended for testing

## Install

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.yaml config.yaml
python bot.py --config config.yaml
```

## Configuration

Use `config.example.yaml` as a reference.

Example:

```yaml
paper:
  enabled: true
  starting_balance: 10000.0
  risk_per_trade_pct: 0.5
  max_daily_loss_pct: 2.0
  max_open_positions: 3
  state_file: paper_state.json

live:
  enabled: false
  api_base: https://api.binance.com

markets:
  - type: mt5
    enabled: true
    symbol: EURUSD
    timeframe: M15
    min_spread_points: 1500
    min_tick_volume: 10
    min_liquidity_score: 0.15
    trend_fast_ema: 12
    trend_slow_ema: 26
    atr_period: 14
    stop_atr_multiple: 2.0
    reward_risk_ratio: 1.5

  - type: binance
    enabled: true
    symbol: BTCUSDT
    interval: 15m
    min_spread_pct: 0.0008
    min_volume_usd: 500000
    min_book_liquidity: 0.15
    trend_fast_ema: 12
    trend_slow_ema: 26
    atr_period: 14
    stop_atr_multiple: 2.0
    reward_risk_ratio: 1.5
```

## Safety

- Live trading is disabled by default.
- The bot does not submit real orders until you explicitly enable a live path.
- Paper trading is meant for testing and learning only.
- Spread, book pressure, and volume are proxies; they cannot guarantee winning trades.
- Never commit real account credentials. Store them in environment variables or a local secrets file.

## Files produced

- `bot.log` – runtime logs
- `trades.csv` – trade history
- `paper_state.json` – paper-trading state
