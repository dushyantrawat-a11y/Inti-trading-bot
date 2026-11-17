"""Yahoo Finance signal scanner and long-only paper trader.

Examples:
  python trading_bot.py --symbol RELIANCE.NS --mode backtest
  python trading_bot.py --symbol RELIANCE.NS --mode live

Despite the CLI mode name, `live` means one current-data paper-trading step. This
script has no real broker connection and never submits orders.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

STATE_FILE = Path(os.getenv("PAPER_STATE_FILE", "paper_state.json"))
DEFAULT_CAPITAL = 100_000.0
RISK_PER_TRADE = 0.01
FEE_RATE = 0.001  # paper fill assumption per side; tune to the market/broker
SLIPPAGE_RATE = 0.0005


def get_data(symbol: str, period: str = "2y", interval: str = "1d") -> pd.DataFrame:
    """Fetch candles and normalize yfinance's varying index/column formats."""
    raw = yf.download(symbol, period=period, interval=interval, auto_adjust=False,
                      progress=False, group_by="column", threads=False)
    if raw is None or raw.empty:
        raise ValueError(f"No Yahoo Finance data returned for {symbol}; check symbol/period/interval")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.columns = [str(c).strip().title() for c in raw.columns]
    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(raw.columns):
        raise ValueError(f"Yahoo data missing required columns: {sorted(required - set(raw.columns))}")
    raw.index = pd.to_datetime(raw.index)
    raw = raw[~raw.index.duplicated(keep="last")].sort_index()
    return raw.dropna(subset=["Open", "High", "Low", "Close"])


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA 20/50, RSI 14, ATR 14 and closed-bar entry signals."""
    out = df.copy().sort_index()
    close = pd.to_numeric(out["Close"], errors="coerce")
    high = pd.to_numeric(out["High"], errors="coerce")
    low = pd.to_numeric(out["Low"], errors="coerce")
    out["ema_fast"] = close.ewm(span=20, adjust=False, min_periods=20).mean()
    out["ema_slow"] = close.ewm(span=50, adjust=False, min_periods=50).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = 100 - (100 / (1 + rs))
    out.loc[(loss == 0) & (gain > 0), "rsi"] = 100.0
    out.loc[(gain == 0) & (loss == 0), "rsi"] = 50.0
    prev_close = close.shift(1)
    true_range = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    out["atr"] = true_range.rolling(14, min_periods=14).mean()
    crossed_up = (out.ema_fast > out.ema_slow) & (out.ema_fast.shift(1) <= out.ema_slow.shift(1))
    crossed_down = (out.ema_fast < out.ema_slow) & (out.ema_fast.shift(1) >= out.ema_slow.shift(1))
    out["buy"] = crossed_up & (out.rsi > 50) & (out.rsi < 70) & out.atr.notna()
    out["sell"] = crossed_down & (out.rsi > 30) & out.atr.notna()
    return out


def backtest(df: pd.DataFrame, capital: float = DEFAULT_CAPITAL,
             risk: float = RISK_PER_TRADE, fee_rate: float = FEE_RATE,
             slippage: float = SLIPPAGE_RATE) -> tuple[pd.DataFrame, pd.Series]:
    """Long-only next-open paper backtest. On same-bar SL/TP touches, SL wins."""
    if capital <= 0 or not 0 < risk <= 0.02 or fee_rate < 0 or slippage < 0:
        raise ValueError("Invalid capital, risk (must be <=2%), fees, or slippage")
    required = {"Open", "High", "Low", "Close", "buy", "sell", "atr"}
    if not required.issubset(df.columns):
        raise ValueError(f"Backtest data missing columns: {sorted(required - set(df.columns))}")
    cash = float(capital)
    position: dict[str, float] | None = None
    trades: list[dict[str, Any]] = []
    equity_values: list[float] = []
    equity_index: list[Any] = []

    for i in range(len(df)):
        row = df.iloc[i]
        date = df.index[i]
        if position is not None:
            exit_price, reason = None, None
            if float(row.Low) <= position["sl"]:
                exit_price, reason = position["sl"], "stop-loss"
            elif float(row.High) >= position["tp"]:
                exit_price, reason = position["tp"], "take-profit"
            elif bool(row.sell):
                exit_price, reason = float(row.Close), "sell-signal"
            if exit_price is not None:
                fill = float(exit_price) * (1 - slippage)
                proceeds = position["qty"] * fill
                fee = proceeds * fee_rate
                cash += proceeds - fee
                pnl = (fill - position["entry"]) * position["qty"] - position["entry_fee"] - fee
                trades.append({"entry_time": position["entry_time"], "exit_time": date,
                    "entry": position["entry"], "exit": fill, "qty": position["qty"],
                    "pnl": pnl, "reason": reason})
                position = None

        # A signal is known at this candle's close; execute at the following open.
        if position is None and i + 1 < len(df) and bool(row.buy) and pd.notna(row.atr) and float(row.atr) > 0:
            next_open = float(df.iloc[i + 1].Open)
            entry = next_open * (1 + slippage)
            sl = entry - 2 * float(row.atr)
            tp = entry + 3 * float(row.atr)
            risk_budget = cash * risk
            qty_by_risk = math.floor(risk_budget / max(entry - sl, 1e-12))
            qty_by_cash = math.floor(cash / (entry * (1 + fee_rate)))
            qty = max(0, min(qty_by_risk, qty_by_cash))
            if qty > 0:
                entry_fee = qty * entry * fee_rate
                cash -= qty * entry + entry_fee
                position = {"qty": float(qty), "entry": entry, "sl": sl, "tp": tp,
                            "entry_fee": entry_fee, "entry_time": df.index[i + 1]}

        marked = cash + (position["qty"] * float(row.Close) if position else 0.0)
        equity_values.append(marked)
        equity_index.append(date)

    if position is not None:
        date = df.index[-1]
        fill = float(df.Close.iloc[-1]) * (1 - slippage)
        proceeds = position["qty"] * fill
        fee = proceeds * fee_rate
        cash += proceeds - fee
        pnl = (fill - position["entry"]) * position["qty"] - position["entry_fee"] - fee
        trades.append({"entry_time": position["entry_time"], "exit_time": date,
            "entry": position["entry"], "exit": fill, "qty": position["qty"],
            "pnl": pnl, "reason": "end-of-data"})
        if equity_values:
            equity_values[-1] = cash

    trade_frame = pd.DataFrame(trades, columns=["entry_time", "exit_time", "entry", "exit", "qty", "pnl", "reason"])
    equity = pd.Series(equity_values, index=pd.Index(equity_index), name="equity", dtype=float)
    return trade_frame, equity


def report(trades: pd.DataFrame, equity: pd.Series, capital: float = DEFAULT_CAPITAL) -> None:
    if trades.empty:
        final = float(equity.iloc[-1]) if not equity.empty else capital
        print(f"No trades generated. Final equity: {final:,.2f}")
        return
    wins = (trades.pnl > 0).mean() * 100
    dd = ((equity / equity.cummax()) - 1).min() * 100 if not equity.empty else 0.0
    final_equity = float(equity.iloc[-1]) if not equity.empty else capital + trades.pnl.sum()
    print(f"Trades: {len(trades)} | Win rate: {wins:.1f}% | Net P&L: {trades.pnl.sum():,.2f} "
          f"({(final_equity / capital - 1) * 100:.1f}%) | Max drawdown: {dd:.1f}%")


def plot_chart(df: pd.DataFrame, trades: pd.DataFrame | None, symbol: str, path: str = "chart.png") -> None:
    if df.empty:
        raise ValueError("Cannot chart an empty data frame")
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    ax.plot(df.index, df.Close, label="Close", color="black", lw=1)
    ax.plot(df.index, df.ema_fast, label="EMA20", color="blue", lw=1)
    ax.plot(df.index, df.ema_slow, label="EMA50", color="orange", lw=1)
    buys, sells = df[df.buy.fillna(False)], df[df.sell.fillna(False)]
    ax.scatter(buys.index, buys.Low * 0.99, marker="^", color="green", s=70, label="BUY signal")
    ax.scatter(sells.index, sells.High * 1.01, marker="v", color="red", s=70, label="SELL signal")
    if trades is not None and not trades.empty:
        ax.scatter(pd.to_datetime(trades.entry_time), trades.entry, marker="o", facecolors="none", edgecolors="green", s=100, label="Entry")
        ax.scatter(pd.to_datetime(trades.exit_time), trades.exit, marker="x", color="red", s=60, label="Exit")
    ax.set_title(f"{symbol} — paper signals and trades")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.2)
    ax2.plot(df.index, df.rsi, color="purple", label="RSI 14")
    ax2.axhline(70, ls="--", c="red")
    ax2.axhline(30, ls="--", c="green")
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("RSI")
    ax2.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print(f"Chart saved -> {path}")


class PaperBroker:
    """Simple long-only paper account with atomic local state persistence."""
    def __init__(self, capital: float = DEFAULT_CAPITAL, state_file: Path = STATE_FILE):
        self.path = Path(state_file)
        if self.path.exists():
            self.s = json.loads(self.path.read_text(encoding="utf-8"))
            self.s.setdefault("log", [])
            self.s.setdefault("pos", None)
            self.s.setdefault("cash", float(capital))
        else:
            self.s = {"cash": float(capital), "pos": None, "log": []}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.s, handle, indent=2, default=str)
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def buy(self, symbol: str, price: float, qty: int, sl: float, tp: float, fee_rate: float = FEE_RATE) -> None:
        if self.s.get("pos") is not None:
            raise ValueError("Paper account already has an open position")
        if qty <= 0 or price <= 0 or not (0 < sl < price < tp):
            raise ValueError("Invalid paper order quantity, price, stop or target")
        cost = qty * price
        fee = cost * fee_rate
        if cost + fee > float(self.s["cash"]):
            raise ValueError("Paper order exceeds available cash")
        self.s["cash"] -= cost + fee
        self.s["pos"] = {"symbol": symbol, "qty": int(qty), "entry": float(price),
                          "sl": float(sl), "tp": float(tp), "entry_fee": fee}
        self.s["log"].append(f"BUY {qty} {symbol} @ {price:.2f} SL {sl:.2f} TP {tp:.2f}")
        self.save()

    def sell(self, price: float, reason: str, fee_rate: float = FEE_RATE) -> None:
        pos = self.s.get("pos")
        if not pos:
            return
        gross = int(pos["qty"]) * float(price)
        fee = gross * fee_rate
        self.s["cash"] += gross - fee
        pnl = (float(price) - float(pos["entry"])) * int(pos["qty"]) - float(pos.get("entry_fee", 0)) - fee
        self.s["log"].append(f"SELL {pos['qty']} {pos['symbol']} @ {price:.2f} ({reason}) P&L {pnl:.2f}")
        self.s["pos"] = None
        self.save()


def live_step(df: pd.DataFrame, symbol: str, risk: float = RISK_PER_TRADE,
              state_file: Path = STATE_FILE) -> None:
    """One latest-bar paper step. No live broker execution is performed."""
    if df.empty:
        raise ValueError("No market data available")
    broker = PaperBroker(state_file=state_file)
    last = df.iloc[-1]
    trend = "UP" if last.ema_fast > last.ema_slow else "DOWN"
    print(f"\n{symbol} close {last.Close:.2f} | trend {trend} | RSI {last.rsi:.1f} | ATR {last.atr:.2f}")
    pos = broker.s.get("pos")
    if pos and pos.get("symbol") != symbol:
        print(f"Paper account already holds {pos.get('symbol')}; refusing to manage it as {symbol}.")
        return
    if pos:
        if float(last.Low) <= float(pos["sl"]):
            broker.sell(float(pos["sl"]), "stop-loss")
        elif float(last.High) >= float(pos["tp"]):
            broker.sell(float(pos["tp"]), "take-profit")
        elif bool(last.sell):
            broker.sell(float(last.Close), "sell-signal")
    elif bool(last.buy) and pd.notna(last.atr) and float(last.atr) > 0:
        entry = float(last.Close) * (1 + SLIPPAGE_RATE)
        sl, tp = entry - 2 * float(last.atr), entry + 3 * float(last.atr)
        risk_cash = float(broker.s["cash"]) * risk
        qty = int(min(risk_cash / max(entry - sl, 1e-12), float(broker.s["cash"]) / (entry * (1 + FEE_RATE))))
        if qty > 0:
            broker.buy(symbol, entry, qty, sl, tp)
        else:
            print("Signal found, but risk/cash sizing rounds to zero shares.")
    else:
        print("No signal. Waiting.")
    for line in broker.s["log"][-5:]:
        print(" ", line)
    print(f"Cash: {broker.s['cash']:,.2f} | Open position: {broker.s['pos']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Yahoo Finance signals, charts and long-only paper trading")
    parser.add_argument("--symbol", default="RELIANCE.NS")
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--mode", choices=["backtest", "live"], default="backtest",
                        help="live performs a single paper-trading step; it never submits real orders")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--risk", type=float, default=RISK_PER_TRADE)
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    parser.add_argument("--chart", default="chart.png")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if not 0 < args.risk <= 0.02:
            parser.error("--risk must be greater than 0 and no more than 0.02 (2%)")
        data = add_indicators(get_data(args.symbol, args.period, args.interval))
        if args.mode == "backtest":
            trades, equity = backtest(data, capital=args.capital, risk=args.risk)
            report(trades, equity, args.capital)
            if not trades.empty:
                trades.to_csv("backtest_trades.csv", index=False)
            plot_chart(data, trades, args.symbol, args.chart)
        else:
            live_step(data, args.symbol, args.risk, args.state_file)
            plot_chart(data.tail(150), None, args.symbol, args.chart)
    except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        logging.error("Could not run: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
