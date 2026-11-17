"""Paper-trading MT5 market scanner. No live order execution is implemented."""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

TIMEFRAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}
STOP = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")],
    )


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    paper = cfg.get("paper", {})
    if paper.get("enabled") is not True:
        raise ValueError("Paper mode must be enabled. This application does not support live orders.")
    if not paper.get("starting_balance", 0) > 0:
        raise ValueError("paper.starting_balance must be positive")
    if not 0 < paper.get("risk_per_trade_pct", 0) <= 2:
        raise ValueError("risk_per_trade_pct must be > 0 and <= 2 percent")
    scanner = cfg.get("scanner", {})
    symbols = scanner.get("symbols", [])
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("Configure a non-empty, duplicate-free scanner.symbols allowlist")
    if scanner.get("ema_fast", 0) >= scanner.get("ema_slow", 0):
        raise ValueError("ema_fast must be smaller than ema_slow")
    if scanner.get("stop_atr_multiple", 0) <= 0 or scanner.get("reward_risk_ratio", 0) <= 0:
        raise ValueError("ATR stop and reward/risk values must be positive")
    return cfg


def init_mt5(cfg: dict[str, Any]) -> None:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package missing; run pip install -r requirements.txt")
    options: dict[str, Any] = {"timeout": int(cfg.get("mt5", {}).get("timeout_ms", 10000))}
    terminal_path = os.getenv("MT5_PATH")
    if terminal_path:
        options["path"] = terminal_path
    login, password, server = (os.getenv(k) for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"))
    if login and password and server:
        options.update(login=int(login), password=password, server=server)
    if not mt5.initialize(**options):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    account = mt5.account_info()
    if account is None:
        raise RuntimeError(f"MT5 account info unavailable: {mt5.last_error()}")
    logging.info("Connected to MT5 account %s on %s; only paper trades will be made", account.login, account.server)


def rates_frame(symbol: str, scanner: dict[str, Any]) -> tuple[pd.DataFrame, Any, Any] | None:
    timeframe_name = str(scanner.get("timeframe", "M15")).upper()
    timeframe_const = TIMEFRAMES.get(timeframe_name)
    if timeframe_const is None:
        raise ValueError(f"Unsupported timeframe: {timeframe_name}")
    timeframe = getattr(mt5, timeframe_const)
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, int(scanner.get("bars", 250)))
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if rates is None or len(rates) < max(int(scanner["ema_slow"]), int(scanner["atr_period"])) + 3 or info is None or tick is None:
        logging.warning("%s: insufficient price/symbol data", symbol)
        return None
    frame = pd.DataFrame(rates)
    return frame, info, tick


def indicators(frame: pd.DataFrame, scanner: dict[str, Any]) -> dict[str, float]:
    close = frame["close"].astype(float)
    fast = close.ewm(span=int(scanner["ema_fast"]), adjust=False).mean()
    slow = close.ewm(span=int(scanner["ema_slow"]), adjust=False).mean()
    prev_close = close.shift(1)
    tr = pd.concat([
        (frame["high"] - frame["low"]).abs(),
        (frame["high"] - prev_close).abs(),
        (frame["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(int(scanner["atr_period"])).mean().iloc[-1]
    return {"fast": float(fast.iloc[-1]), "slow": float(slow.iloc[-1]), "atr": float(atr), "close": float(close.iloc[-1])}


def tick_flow(symbol: str, cfg: dict[str, Any]) -> tuple[float | None, float]:
    """Returns signed buy/sell tick imbalance (-1..1), and optional book total volume."""
    imbalance: float | None = None
    depth_total = 0.0
    if cfg.get("mt5", {}).get("market_depth_enabled", True):
        try:
            if mt5.market_book_add(symbol):
                book = mt5.market_book_get(symbol) or []
                bids = sum(float(x.volume) for x in book if x.type == mt5.BOOK_TYPE_BUY)
                asks = sum(float(x.volume) for x in book if x.type == mt5.BOOK_TYPE_SELL)
                depth_total = bids + asks
                if depth_total > 0:
                    imbalance = (bids - asks) / depth_total
                mt5.market_book_release(symbol)
        except Exception as exc:
            logging.debug("%s: market depth unavailable: %s", symbol, exc)
    # Tick flags may be broker-dependent. Treat unknown flags as neutral rather than inferring direction.
    try:
        latest = mt5.copy_ticks_from(symbol, datetime.now(), 500, mt5.COPY_TICKS_ALL)
        buy_flag = getattr(mt5, "TICK_FLAG_BUY", 8)
        sell_flag = getattr(mt5, "TICK_FLAG_SELL", 16)
        if latest is not None and len(latest):
            buy = sum(1 for row in latest if int(row["flags"]) & buy_flag)
            sell = sum(1 for row in latest if int(row["flags"]) & sell_flag)
            total = buy + sell
            if total:
                tick_imbalance = (buy - sell) / total
                # Prefer actual depth when available; otherwise use tick flags as a rough proxy.
                if imbalance is None:
                    imbalance = tick_imbalance
    except Exception as exc:
        logging.debug("%s: tick flow unavailable: %s", symbol, exc)
    return imbalance, depth_total


@dataclass
class Position:
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    volume: float
    risk_cash: float
    opened_at: str


class PaperBroker:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg["paper"]
        self.path = Path(self.cfg.get("state_file", "paper_state.json"))
        self.balance = float(self.cfg["starting_balance"])
        self.positions: dict[str, Position] = {}
        self.day = utc_now().date().isoformat()
        self.day_start_balance = self.balance
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.balance = float(saved.get("balance", self.balance))
            self.day = saved.get("day", self.day)
            self.day_start_balance = float(saved.get("day_start_balance", self.balance))
            self.positions = {k: Position(**v) for k, v in saved.get("positions", {}).items()}
            logging.info("Loaded paper state: balance %.2f, %d open positions", self.balance, len(self.positions))
        except (ValueError, TypeError, OSError) as exc:
            raise RuntimeError(f"Cannot read paper state {self.path}: {exc}") from exc

    def save(self) -> None:
        data = {"balance": self.balance, "day": self.day, "day_start_balance": self.day_start_balance,
                "positions": {k: asdict(v) for k, v in self.positions.items()}}
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def daily_loss_limit_hit(self) -> bool:
        today = utc_now().date().isoformat()
        if today != self.day:
            self.day, self.day_start_balance = today, self.balance
            self.save()
        max_loss = self.day_start_balance * float(self.cfg.get("max_daily_loss_pct", 2.0)) / 100
        return self.day_start_balance - self.balance >= max_loss

    def size(self, symbol_info: Any, entry: float, stop: float) -> tuple[float, float]:
        risk_cash = self.balance * float(self.cfg["risk_per_trade_pct"]) / 100
        tick_size = float(symbol_info.trade_tick_size or symbol_info.point or 0)
        tick_value = float(symbol_info.trade_tick_value or 0)
        if tick_size <= 0 or tick_value <= 0 or entry == stop:
            return 0.0, risk_cash
        cash_per_lot = abs(entry - stop) / tick_size * tick_value
        if cash_per_lot <= 0:
            return 0.0, risk_cash
        raw = risk_cash / cash_per_lot
        step = float(symbol_info.volume_step or 0.01)
        minimum = float(symbol_info.volume_min or step)
        maximum = float(symbol_info.volume_max or raw)
        volume = min(maximum, math.floor(raw / step) * step)
        if volume < minimum:
            return 0.0, risk_cash
        return round(volume, 8), risk_cash

    def open(self, symbol: str, side: str, entry: float, stop: float, target: float, info: Any) -> None:
        if symbol in self.positions or len(self.positions) >= int(self.cfg.get("max_open_positions", 3)):
            return
        volume, risk = self.size(info, entry, stop)
        if volume <= 0:
            logging.warning("%s: calculated size below broker minimum; no paper trade", symbol)
            return
        self.positions[symbol] = Position(symbol, side, entry, stop, target, volume, risk, utc_now().isoformat())
        logging.info("PAPER OPEN %s %s entry=%.8f SL=%.8f TP=%.8f volume=%s risk=%.2f", symbol, side, entry, stop, target, volume, risk)
        self._record("OPEN", symbol, side, entry, 0.0, volume, "signal")
        self.save()

    def manage(self, symbol: str, bid: float, ask: float, info: Any) -> None:
        p = self.positions.get(symbol)
        if p is None:
            return
        # Long exits execute at bid, short exits at ask; conservative fills omit slippage/fees.
        price = bid if p.side == "BUY" else ask
        reason = None
        if p.side == "BUY" and price <= p.stop:
            reason = "stop_loss"
        elif p.side == "BUY" and price >= p.target:
            reason = "take_profit"
        elif p.side == "SELL" and price >= p.stop:
            reason = "stop_loss"
        elif p.side == "SELL" and price <= p.target:
            reason = "take_profit"
        if not reason:
            return
        tick_size = float(info.trade_tick_size or info.point or 0)
        tick_value = float(info.trade_tick_value or 0)
        pnl = ((price - p.entry) if p.side == "BUY" else (p.entry - price)) / tick_size * tick_value * p.volume if tick_size > 0 else 0
        self.balance += pnl
        logging.info("PAPER CLOSE %s %s %s exit=%.8f P/L=%.2f balance=%.2f", symbol, p.side, reason, price, pnl, self.balance)
        self._record("CLOSE", symbol, p.side, price, pnl, p.volume, reason)
        del self.positions[symbol]
        self.save()

    @staticmethod
    def _record(event: str, symbol: str, side: str, price: float, pnl: float, volume: float, note: str) -> None:
        file_exists = Path("trades.csv").exists()
        pd.DataFrame([{"time_utc": utc_now().isoformat(), "event": event, "symbol": symbol,
                       "side": side, "price": price, "pnl": pnl, "volume": volume, "note": note}]).to_csv(
            "trades.csv", mode="a", header=not file_exists, index=False)


def scan_symbol(symbol: str, cfg: dict[str, Any], broker: PaperBroker) -> None:
    scanner = cfg["scanner"]
    if not mt5.symbol_select(symbol, True):
        logging.warning("%s: unavailable from broker (%s)", symbol, mt5.last_error())
        return
    result = rates_frame(symbol, scanner)
    if result is None:
        return
    frame, info, tick = result
    broker.manage(symbol, float(tick.bid), float(tick.ask), info)
    spread_points = (float(tick.ask) - float(tick.bid)) / float(info.point or 1)
    tick_volume = float(frame["tick_volume"].iloc[-2]) if len(frame) > 1 else 0
    if spread_points > float(scanner.get("max_spread_points", float("inf"))):
        logging.info("%s skipped: spread %.1f points exceeds limit", symbol, spread_points)
        return
    if tick_volume < float(scanner.get("min_tick_volume", 0)):
        logging.info("%s skipped: tick volume %.0f below minimum", symbol, tick_volume)
        return
    flow, depth = tick_flow(symbol, cfg)
    if cfg.get("mt5", {}).get("require_market_depth", False) and depth <= 0:
        logging.info("%s skipped: required market depth unavailable", symbol)
        return
    min_depth = float(scanner.get("min_depth_volume", 0))
    if min_depth > 0 and depth < min_depth:
        logging.info("%s skipped: depth %.2f below minimum %.2f", symbol, depth, min_depth)
        return
    ind = indicators(frame.iloc[:-1], scanner)  # omit the still-forming candle from indicator decisions
    if not math.isfinite(ind["atr"]) or ind["atr"] <= 0:
        logging.info("%s skipped: ATR unavailable", symbol)
        return
    logging.info("%s close=%.8f spread=%.1f tickVol=%.0f flow=%s depth=%.2f EMA(%d/%d)=%.8f/%.8f ATR=%.8f",
                 symbol, ind["close"], spread_points, tick_volume,
                 "n/a" if flow is None else f"{flow:+.2f}", depth,
                 int(scanner["ema_fast"]), int(scanner["ema_slow"]), ind["fast"], ind["slow"], ind["atr"])
    if broker.daily_loss_limit_hit() or symbol in broker.positions:
        return
    min_imbalance = float(scanner.get("min_buy_sell_imbalance", 0.1))
    if flow is None or abs(flow) < min_imbalance:
        return
    side = "BUY" if ind["fast"] > ind["slow"] and flow > 0 else "SELL" if ind["fast"] < ind["slow"] and flow < 0 else None
    if side is None:
        return
    distance = ind["atr"] * float(scanner["stop_atr_multiple"])
    rr = float(scanner["reward_risk_ratio"])
    if side == "BUY":
        entry = float(tick.ask)
        broker.open(symbol, side, entry, entry - distance, entry + distance * rr, info)
    else:
        entry = float(tick.bid)
        broker.open(symbol, side, entry, entry + distance, entry - distance * rr, info)


def request_stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def run(config_path: str) -> None:
    cfg = load_config(config_path)
    init_mt5(cfg)
    broker = PaperBroker(cfg)
    scanner = cfg["scanner"]
    try:
        while not STOP:
            for symbol in scanner["symbols"]:
                if STOP:
                    break
                try:
                    scan_symbol(str(symbol), cfg, broker)
                except Exception:
                    logging.exception("Error while scanning %s", symbol)
            time.sleep(max(1, int(scanner.get("poll_seconds", 30))))
    finally:
        broker.save()
        mt5.shutdown()
        logging.info("Stopped safely; no live orders were submitted")


def main() -> int:
    parser = argparse.ArgumentParser(description="MT5 market scanner and paper trader (no live orders)")
    parser.add_argument("--config", default="config.yaml", help="YAML config path (default: config.yaml)")
    args = parser.parse_args()
    setup_logging()
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    try:
        run(args.config)
    except Exception as exc:
        logging.error("Fatal: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
