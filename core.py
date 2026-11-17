"""Shared signal, risk and historical backtest utilities (paper trading only)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


def indicators(frame: pd.DataFrame, fast: int = 12, slow: int = 26, atr_period: int = 14) -> pd.DataFrame:
    if not {"high", "low", "close"}.issubset(frame.columns):
        raise ValueError("Historical candles must contain high, low and close columns")
    if fast < 2 or slow <= fast or atr_period < 2:
        raise ValueError("Require 2 <= fast < slow and ATR period >= 2")
    out = frame.copy().reset_index(drop=True)
    close = pd.to_numeric(out.close)
    out["ema_fast"] = close.ewm(span=fast, adjust=False).mean()
    out["ema_slow"] = close.ewm(span=slow, adjust=False).mean()
    prev = close.shift(1)
    tr = pd.concat([(out.high - out.low).abs(), (out.high - prev).abs(), (out.low - prev).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(atr_period).mean()
    out["signal"] = 0
    cross_up = (out.ema_fast > out.ema_slow) & (out.ema_fast.shift(1) <= out.ema_slow.shift(1))
    cross_dn = (out.ema_fast < out.ema_slow) & (out.ema_fast.shift(1) >= out.ema_slow.shift(1))
    out.loc[cross_up, "signal"] = 1
    out.loc[cross_dn, "signal"] = -1
    return out


@dataclass
class RiskLimits:
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_drawdown_pct: float = 10.0
    max_total_risk_pct: float = 1.5
    max_notional_pct: float = 100.0
    max_open_positions: int = 3


class RiskEngine:
    """Conservative account-level controls for the local paper simulator."""
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def size(self, equity: float, entry: float, stop: float, *, risk_multiplier: float = 1.0,
             unit_pnl_per_price: float = 1.0) -> tuple[float, float]:
        if equity <= 0 or entry <= 0 or entry == stop or unit_pnl_per_price <= 0:
            return 0.0, 0.0
        risk_cash = equity * self.limits.risk_per_trade_pct / 100 * max(0.0, min(1.0, risk_multiplier))
        risk_per_unit = abs(entry - stop) * unit_pnl_per_price
        units = min(risk_cash / risk_per_unit, equity * self.limits.max_notional_pct / 100 / entry)
        return max(0.0, units), max(0.0, min(risk_cash, units * risk_per_unit))

    def can_open(self, equity: float, day_start_equity: float, peak_equity: float,
                 open_risk: float, open_count: int) -> tuple[bool, str]:
        if open_count >= self.limits.max_open_positions:
            return False, "maximum open positions reached"
        if day_start_equity > 0 and equity <= day_start_equity * (1 - self.limits.max_daily_loss_pct / 100):
            return False, "daily loss limit reached"
        if peak_equity > 0 and equity <= peak_equity * (1 - self.limits.max_drawdown_pct / 100):
            return False, "maximum drawdown limit reached"
        if equity > 0 and open_risk >= equity * self.limits.max_total_risk_pct / 100:
            return False, "total open risk limit reached"
        return True, "ok"


def backtest(frame: pd.DataFrame, *, initial_cash: float = 10_000.0, risk_pct: float = 0.5,
             fast: int = 12, slow: int = 26, atr_period: int = 14, stop_atr: float = 2.0,
             reward_risk: float = 1.5, fee_rate: float = 0.001, slippage: float = 0.0005,
             max_daily_loss_pct: float = 2.0, max_drawdown_pct: float = 10.0,
             max_notional_pct: float = 100.0) -> dict[str, Any]:
    """Bar-by-bar, next-open execution. If SL and TP touch in one candle, SL wins."""
    if initial_cash <= 0 or not 0 < risk_pct <= 2 or stop_atr <= 0 or reward_risk <= 0:
        raise ValueError("Invalid starting cash, risk, stop or reward/risk parameters")
    data = indicators(frame, fast, slow, atr_period)
    if "open" not in data:
        raise ValueError("Historical candles must contain open, high, low and close")
    cash = float(initial_cash)
    peak = cash
    day_start = cash
    active_day = None
    trades: list[dict[str, Any]] = []
    position = None
    equity_curve = []
    for i in range(1, len(data)):
        row = data.iloc[i]
        date_key = str(row.get("time", row.get("open_time", i)))[:10]
        if date_key != active_day:
            active_day, day_start = date_key, cash
        if position:
            exit_price, reason = None, None
            if position["side"] == "BUY":
                if float(row.low) <= position["stop"]:
                    exit_price, reason = position["stop"], "stop_loss"
                elif float(row.high) >= position["target"]:
                    exit_price, reason = position["target"], "take_profit"
            else:
                if float(row.high) >= position["stop"]:
                    exit_price, reason = position["stop"], "stop_loss"
                elif float(row.low) <= position["target"]:
                    exit_price, reason = position["target"], "take_profit"
            if exit_price is not None:
                exit_price *= (1 - slippage) if position["side"] == "BUY" else (1 + slippage)
                gross = ((exit_price - position["entry"]) if position["side"] == "BUY" else (position["entry"] - exit_price)) * position["units"]
                fees = (position["entry"] + exit_price) * position["units"] * fee_rate
                pnl = gross - fees
                cash += pnl
                trades.append({"side": position["side"], "entry": position["entry"], "exit": exit_price,
                               "units": position["units"], "pnl": pnl, "reason": reason, "index": i})
                position = None
        peak = max(peak, cash)
        equity_curve.append({"index": i, "equity": cash})
        if position is not None or cash <= day_start * (1 - max_daily_loss_pct / 100) or cash <= peak * (1 - max_drawdown_pct / 100):
            continue
        direction = int(data.signal.iloc[i - 1])  # signal known at prior close; enter this bar's open
        atr = float(data.atr.iloc[i - 1])
        if direction == 0 or not pd.notna(atr) or atr <= 0:
            continue
        entry = float(row.open) * (1 + slippage if direction > 0 else 1 - slippage)
        distance = atr * stop_atr
        stop = entry - distance if direction > 0 else entry + distance
        target = entry + distance * reward_risk if direction > 0 else entry - distance * reward_risk
        risk_cash = cash * risk_pct / 100
        units = min(risk_cash / distance, cash * max_notional_pct / 100 / entry)
        if units <= 0:
            continue
        position = {"side": "BUY" if direction > 0 else "SELL", "entry": entry, "stop": stop,
                    "target": target, "units": units}
    if position:
        last = float(data.close.iloc[-1])
        gross = ((last - position["entry"]) if position["side"] == "BUY" else (position["entry"] - last)) * position["units"]
        pnl = gross - (position["entry"] + last) * position["units"] * fee_rate
        cash += pnl
        trades.append({"side": position["side"], "entry": position["entry"], "exit": last,
                       "units": position["units"], "pnl": pnl, "reason": "end_of_data", "index": len(data) - 1})
    wins = sum(t["pnl"] > 0 for t in trades)
    total_return = (cash / initial_cash - 1) * 100
    return {"initial_cash": initial_cash, "final_equity": cash, "return_pct": total_return,
            "trade_count": len(trades), "win_rate_pct": 100 * wins / len(trades) if trades else 0.0,
            "max_drawdown_pct": _max_drawdown(equity_curve, initial_cash), "trades": trades,
            "equity_curve": equity_curve}


def _max_drawdown(curve: list[dict[str, float]], initial: float) -> float:
    peak, worst = initial, 0.0
    for point in curve:
        equity = point["equity"]
        peak = max(peak, equity)
        if peak:
            worst = max(worst, (peak - equity) / peak * 100)
    return worst
