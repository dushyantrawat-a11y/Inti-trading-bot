# Trading bot starter (paper trading only)

A Python/MetaTrader 5 market scanner that evaluates an explicit symbol allowlist, filters on spread and recent tick volume, estimates buyer/seller pressure from ticks (and optionally MT5 market depth), calculates ATR-based stop-loss/take-profit levels, sizes simulated positions from a risk budget, and manages paper trades.

**This project does not submit live orders.** It defaults to paper trading and contains no live-order execution path. Market-depth availability, tick flags, and symbol naming vary by broker. Tick buy/sell flags are only a rough flow proxy—not actual exchange-wide order flow or a prediction.

## Requirements

- Python 3.10+
- Windows with the MetaTrader 5 desktop terminal installed and logged into a demo account (or configured credentials in environment variables)
- A broker that exposes the configured symbols and, optionally, market depth

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.yaml config.yaml
python bot.py --config config.yaml
```

The terminal may already be logged in. To initialize MT5 explicitly, set `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER` in the process environment. Never commit real credentials. `MT5_PATH` can optionally point to the terminal executable.

## Configure

Edit `config.yaml`. `symbols` is an allowlist; use the exact names shown by your broker (for example, `BTCUSD` or `BTCUSDm`). Tune thresholds using historical/demo data. `paper.starting_balance` and `risk_per_trade_pct` affect only the simulator. The bot refuses to run if `paper.enabled` is false.

Market-depth checks are best-effort: when enabled, the bot requests the book and applies the configured minimum depth if the broker provides it. If depth is unavailable, the configured `require_market_depth` setting determines whether that symbol is skipped. Tick volume and buy/sell tick imbalance are not equivalent to centralized volume or an order book.

Paper positions and balance are saved to `paper_state.json`; decisions are appended to `bot.log` and `trades.csv`. These files are local runtime data and ignored by Git. Paper execution does not model slippage, commissions, funding, or guaranteed fills.

## Risk and safety

- Paper-only; there is no real-order endpoint or live-trading switch.
- Symbol allowlist, spread and volume gates, configurable ATR stops/targets, risk-based sizing, maximum open positions, and daily loss cutoff.
- Stops can gap in real markets; simulated outcomes are not live performance.
- Test on a demo terminal and review the configuration before relying on any output. This software is educational and does not provide financial advice or guarantee results.
