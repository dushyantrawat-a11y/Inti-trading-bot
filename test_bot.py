"""Trading bot for MetaTrader 5 and Binance markets.

The bot defaults to paper trading. It can scan MT5 symbols and Binance pairs,
check liquidity and volume, and decide whether to open a paper position based on
trend + flow + risk rules.
"""
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
import requests
import yaml

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

STOP = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", mode="a", encoding="utf-8")],
    )


def read_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def normalize_symbol_key(symbol: Any) -> str:
    return str(symbol).strip().upper()


def load_config(path: str) -> dict[str, Any]:
    cfg = read_yaml(path)
    paper = cfg.get("paper", {})
    if paper.get("enabled") is not True:
        raise ValueError("Paper mode must be enabled. Live trading is disabled by default.")
    if float(paper.get("starting_balance", 0)) <= 0:
        raise ValueError("paper.starting_balance must be > 0")
    if not (0 < float(paper.get("risk_per_trade_pct", 0)) <= 2.0):
        raise ValueError("paper.risk_per_trade_pct must be between 0 and 2 percent")
    markets = cfg.get("markets", [])
    if not markets:
        raise ValueError("At least one market entry is required in config.markets")
    valid_types = {"mt5", "binance"}
    for item in markets:
        if str(item.get("type", "")).lower() not in valid_types:
            raise ValueError(f"Unsupported market type: {item.get('type')!r}")
        if item.get("enabled") is not True:
            continue
        if not item.get("symbol"):
            raise ValueError("Each enabled market requires a symbol")
    return cfg


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class MarketSnapshot:
    market_type: str
    symbol: str
    price: float
    spread: float
    volume: float
    liquidity: float
    flow: float | None
    trend: float
    atr: float
    buy_signal: bool
    sell_signal: bool
    details: dict[str, Any]


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
        self.balance = float(self.cfg.get("starting_balance", 10000.0))
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
            self.day = str(saved.get("day", self.day))
            self.day_start_balance = float(saved.get("day_start_balance", self.balance))
            positions = saved.get("positions", {})
            self.positions = {k: Position(**v) for k, v in positions.items()}
            logging.info("Loaded paper state: balance %.2f; open positions=%d", self.balance, len(self.positions))
        except Exception as exc:  # pragma: no cover
            logging.warning("Could not load paper state: %s", exc)

    def save(self) -> None:
        payload = {
            "balance": self.balance,
            "day": self.day,
            "day_start_balance": self.day_start_balance,
            "positions": {k: asdict(v) for k, v in self.positions.items()},
        }
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def daily_loss_limit_hit(self) -> bool:
        today = utc_now().date().isoformat()
        if today != self.day:
            self.day = today
            self.day_start_balance = self.balance
            self.save()
        max_loss = self.day_start_balance * float(self.cfg.get("max_daily_loss_pct", 2.0)) / 100.0
        return (self.day_start_balance - self.balance) >= max_loss

    def size_position(self, price: float, stop: float, info: dict[str, Any]) -> tuple[float, float]:
        risk_cash = self.balance * float(self.cfg.get("risk_per_trade_pct", 0.5)) / 100.0
        if price <= 0 or stop <= 0 or price == stop:
            return 0.0, risk_cash
        risk_per_unit = abs(price - stop)
        if risk_per_unit <= 0:
            return 0.0, risk_cash
        # For MT5, info may expose volume step / minimum; for Binance, risk per unit is value-per-coin.
        step = float(info.get("volume_step", 0.01))
        minimum = float(info.get("volume_min", step))
        maximum = float(info.get("volume_max", 1_000_000.0))
        raw_units = risk_cash / risk_per_unit
        volume = min(maximum, max(minimum, math.floor(raw_units / max(step, 1e-9)) * max(step, 1e-9)))
        if volume < minimum:
            return 0.0, risk_cash
        return float(volume), risk_cash

    def open(self, symbol: str, side: str, entry: float, stop: float, target: float, info: dict[str, Any]) -> None:
        if symbol in self.positions or len(self.positions) >= int(self.cfg.get("max_open_positions", 3)):
            return
        volume, risk_cash = self.size_position(entry, stop, info)
        if volume <= 0:
            logging.warning("%s: insufficient sizing, no paper trade", symbol)
            return
        self.positions[symbol] = Position(symbol, side, entry, stop, target, volume, risk_cash, utc_now().isoformat())
        logging.info("PAPER OPEN %s %s entry=%.8f stop=%.8f target=%.8f volume=%.8f risk=%.2f", symbol, side, entry, stop, target, volume, risk_cash)
        self._record("OPEN", symbol, side, entry, 0.0, volume, "signal")
        self.save()

    def manage(self, symbol: str, price: float, side: str, stop: float, target: float) -> None:
        pos = self.positions.get(symbol)
        if pos is None:
            return
        reason = None
        if pos.side == "BUY" and price <= pos.stop:
            reason = "stop_loss"
        elif pos.side == "BUY" and price >= pos.target:
            reason = "take_profit"
        elif pos.side == "SELL" and price >= pos.stop:
            reason = "stop_loss"
        elif pos.side == "SELL" and price <= pos.target:
            reason = "take_profit"
        if reason is None:
            return
        pnl = (price - pos.entry) * pos.volume if pos.side == "BUY" else (pos.entry - price) * pos.volume
        self.balance += pnl
        logging.info("PAPER CLOSE %s %s reason=%s price=%.8f pnl=%.2f balance=%.2f", symbol, pos.side, reason, price, pnl, self.balance)
        self._record("CLOSE", symbol, pos.side, price, pnl, pos.volume, reason)
        del self.positions[symbol]
        self.save()

    @staticmethod
    def _record(event: str, symbol: str, side: str, price: float, pnl: float, volume: float, note: str) -> None:
        exists = Path("trades.csv").exists()
        frame = pd.DataFrame([
            {
                "time_utc": utc_now().isoformat(),
                "event": event,
                "symbol": symbol,
                "side": side,
                "price": price,
                "pnl": pnl,
                "volume": volume,
                "note": note,
            }
        ])
        frame.to_csv("trades.csv", mode="a", header=not exists, index=False)


#### MT5 helpers

MT5_TIMEFRAMES = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


def init_mt5(cfg: dict[str, Any]) -> None:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package not installed. Install requirements first.")
    options: dict[str, Any] = {"timeout": int(cfg.get("mt5", {}).get("timeout_ms", 10000))}
    terminal_path = os.getenv("MT5_PATH")
    if terminal_path:
        options["path"] = terminal_path
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")
    if login and password and server:
        options.update(login=int(login), password=password, server=server)
    if not mt5.initialize(**options):
        raise RuntimeError(f"MT5 failed to initialize: {mt5.last_error()}")
    account = mt5.account_info()
    if account is None:
        raise RuntimeError(f"MT5 account info unavailable: {mt5.last_error()}")
    logging.info("MT5 connected to account %s server %s", account.login, account.server)


def mt5_snapshot(symbol: str, market_cfg: dict[str, Any]) -> MarketSnapshot | None:
    timeframe_name = str(market_cfg.get("timeframe", "M15")).upper()
    time_const = MT5_TIMEFRAMES.get(timeframe_name)
    if time_const is None:
        raise ValueError(f"Unsupported MT5 timeframe: {timeframe_name}")
    if not mt5.symbol_select(symbol, True):
        logging.warning("MT5 symbol unavailable: %s", symbol)
        return None
    rates = mt5.copy_rates_from_pos(symbol, getattr(mt5, time_const), 0, int(market_cfg.get("bars", 250)))
    info = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if rates is None or len(rates) < 25 or info is None or tick is None:
        return None
    frame = pd.DataFrame(rates)
    close = frame["close"].astype(float)
    fast = close.ewm(span=int(market_cfg.get("trend_fast_ema", 12)), adjust=False).mean().iloc[-1]
    slow = close.ewm(span=int(market_cfg.get("trend_slow_ema", 26)), adjust=False).mean().iloc[-1]
    prev = close.shift(1)
    tr = pd.concat([
        (frame["high"] - frame["low"]).abs(),
        (frame["high"] - prev).abs(),
        (frame["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(int(market_cfg.get("atr_period", 14))).mean().iloc[-1])
    spread = (float(tick.ask) - float(tick.bid)) / max(float(info.point or 1.0), 1e-9)
    tick_volume = float(frame["tick_volume"].iloc[-2]) if len(frame) > 1 else 0.0
    liquidity = float(market_cfg.get("min_liquidity_score", 0.15))
    flow = None
    try:
        recent = mt5.copy_ticks_from(symbol, datetime.now(), 500, mt5.COPY_TICKS_ALL)
        if recent is not None and len(recent):
            buy = sum(1 for i in recent if int(i["flags"]) & getattr(mt5, "TICK_FLAG_BUY", 8))
            sell = sum(1 for i in recent if int(i["flags"]) & getattr(mt5, "TICK_FLAG_SELL", 16))
            total = buy + sell
            if total:
                flow = (buy - sell) / total
    except Exception as exc:  # pragma: no cover
        logging.debug("MT5 tick flow unavailable for %s: %s", symbol, exc)
    return MarketSnapshot(
        market_type="mt5",
        symbol=symbol,
        price=float(tick.ask),
        spread=float(spread),
        volume=float(tick_volume),
        liquidity=float(liquidity),
        flow=float(flow) if flow is not None else None,
        trend=float(fast - slow),
        atr=float(atr),
        buy_signal=fast > slow,
        sell_signal=fast < slow,
        details={"bid": float(tick.bid), "ask": float(tick.ask), "tick_volume": tick_volume, "atr": atr},
    )


#### Binance helpers

BINANCE_BASE = "https://api.binance.com"


def binance_get(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{BINANCE_BASE}{endpoint}"
    response = requests.get(url, params=params or {}, timeout=10)
    response.raise_for_status()
    return response.json()


def normalize_klines(raw: list[list[Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"])
    for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def binance_snapshot(symbol: str, market_cfg: dict[str, Any]) -> MarketSnapshot | None:
    interval = str(market_cfg.get("interval", "15m"))
    try:
        klines = binance_get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": 200})
    except Exception as exc:  # pragma: no cover
        logging.warning("Binance kline fetch failed for %s: %s", symbol, exc)
        return None
    try:
        depth = binance_get("/api/v3/depth", {"symbol": symbol, "limit": 20})
    except Exception as exc:  # pragma: no cover
        logging.warning("Binance depth fetch failed for %s: %s", symbol, exc)
        return None
    info = binance_get("/api/v3/ticker/24hr", {"symbol": symbol})
    frame = normalize_klines(klines)
    if frame.empty:
        return None
    close = frame["close"].astype(float)
    fast = close.ewm(span=int(market_cfg.get("trend_fast_ema", 12)), adjust=False).mean().iloc[-1]
    slow = close.ewm(span=int(market_cfg.get("trend_slow_ema", 26)), adjust=False).mean().iloc[-1]
    prev = close.shift(1)
    tr = pd.concat([
        (frame["high"] - frame["low"]).abs(),
        (frame["high"] - prev).abs(),
        (frame["low"] - prev).abs(),
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(int(market_cfg.get("atr_period", 14))).mean().iloc[-1])
    bids = depth.get("bids", [])
    asks = depth.get("asks", [])
    bid_qty = sum(float(qty) for _, qty in bids[:10])
    ask_qty = sum(float(qty) for _, qty in asks[:10])
    total_depth = bid_qty + ask_qty
    spread_pct = 0.0
    if bids and asks:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        spread_pct = (best_ask - best_bid) / max((best_ask + best_bid) / 2, 1e-9)
    liquidity = 0.0 if total_depth <= 0 else (bid_qty - ask_qty) / total_depth
    volume_usd = float(info.get("quoteVolume", 0))
    price = float(info.get("lastPrice", close.iloc[-1]))
    return MarketSnapshot(
        market_type="binance",
        symbol=symbol,
        price=price,
        spread=spread_pct,
        volume=volume_usd,
        liquidity=abs(liquidity),
        flow=float(liquidity),
        trend=float(fast - slow),
        atr=float(atr),
        buy_signal=fast > slow and liquidity > 0,
        sell_signal=fast < slow and liquidity < 0,
        details={"depth_bid_qty": bid_qty, "depth_ask_qty": ask_qty, "volume_usd": volume_usd, "spread_pct": spread_pct, "atr": atr},
    )


def evaluate_market(snapshot: MarketSnapshot, market_cfg: dict[str, Any], broker: PaperBroker) -> None:
    if snapshot.market_type == "mt5":
        min_spread = float(market_cfg.get("min_spread_points", 0.0))
        min_tick = float(market_cfg.get("min_tick_volume", 0.0))
        min_liquidity = float(market_cfg.get("min_liquidity_score", 0.0))
        if snapshot.spread > min_spread:
            logging.info("SKIP %s: spread %.2f exceeds min %.2f", snapshot.symbol, snapshot.spread, min_spread)
            return
        if snapshot.volume < min_tick:
            logging.info("SKIP %s: tick volume %.2f < %.2f", snapshot.symbol, snapshot.volume, min_tick)
            return
        if snapshot.flow is None or abs(snapshot.flow) < min_liquidity:
            logging.info("SKIP %s: flow %.4f below liquidity threshold %.2f", snapshot.symbol, snapshot.flow if snapshot.flow is not None else 0.0, min_liquidity)
            return
    elif snapshot.market_type == "binance":
        min_spread = float(market_cfg.get("min_spread_pct", 0.0))
        min_volume = float(market_cfg.get("min_volume_usd", 0.0))
        min_depth = float(market_cfg.get("min_book_liquidity", 0.0))
        if snapshot.spread > min_spread:
            logging.info("SKIP %s: spread %.6f exceeds min %.6f", snapshot.symbol, snapshot.spread, min_spread)
            return
        if snapshot.volume < min_volume:
            logging.info("SKIP %s: volume %.2f < %.2f", snapshot.symbol, snapshot.volume, min_volume)
            return
        if abs(float(snapshot.flow or 0.0)) < min_depth:
            logging.info("SKIP %s: book imbalance %.4f below threshold %.2f", snapshot.symbol, snapshot.flow or 0.0, min_depth)
            return

    if broker.daily_loss_limit_hit():
        logging.warning("Daily loss limit reached; no new paper trades")
        return
    if snapshot.symbol in broker.positions:
        return

    side = None
    if snapshot.buy_signal and (snapshot.flow is None or snapshot.flow > 0):
        side = "BUY"
    elif snapshot.sell_signal and (snapshot.flow is None or snapshot.flow < 0):
        side = "SELL"
    if side is None:
        return

    atr = max(float(snapshot.atr), 1e-9)
    sl_mult = float(market_cfg.get("stop_atr_multiple", 2.0))
    rr = float(market_cfg.get("reward_risk_ratio", 1.5))
    stop_distance = atr * sl_mult
    target_distance = stop_distance * rr

    if side == "BUY":
        entry = snapshot.price
        stop = entry - stop_distance
        target = entry + target_distance
    else:
        entry = snapshot.price
        stop = entry + stop_distance
        target = entry - target_distance

    broker.open(snapshot.symbol, side, entry, stop, target, {"volume_step": 0.01, "volume_min": 0.01, "volume_max": 1_000_000.0})


def run(config_path: str) -> None:
    cfg = load_config(config_path)
    paper = cfg["paper"]
    live_cfg = cfg.get("live", {})
    if live_cfg.get("enabled") is True:
        logging.warning("Live trading enabled in config. This bot is still in a guarded test mode.")
        # Live execution path may be added later. Default is paper trading only.
    broker = PaperBroker(cfg)
    if "mt5" in {m.get("type") for m in cfg.get("markets", []) if m.get("enabled") is True}:
        init_mt5(cfg)
    try:
        while not STOP:
            for market_cfg in cfg.get("markets", []):
                if market_cfg.get("enabled") is not True:
                    continue
                symbol = normalize_symbol_key(market_cfg.get("symbol"))
                market_type = str(market_cfg.get("type", "")).lower()
                try:
                    if market_type == "mt5":
                        snapshot = mt5_snapshot(symbol, market_cfg)
                    elif market_type == "binance":
                        snapshot = binance_snapshot(symbol, market_cfg)
                    else:
                        continue
                    if snapshot is None:
                        continue
                    logging.info(
                        "%s %s: price=%.8f spread=%.8f volume=%.2f flow=%s trend=%.6f atr=%.6f",
                        snapshot.market_type.upper(),
                        snapshot.symbol,
                        snapshot.price,
                        snapshot.spread,
                        snapshot.volume,
                        "n/a" if snapshot.flow is None else f"{snapshot.flow:+.4f}",
                        snapshot.trend,
                        snapshot.atr,
                    )
                    evaluate_market(snapshot, market_cfg, broker)
                    for pos in list(broker.positions.values()):
                        if snapshot.market_type == "mt5":
                            # If the current symbol matches, evaluate close conditions using the latest bid/ask.
                            if pos.symbol != snapshot.symbol:
                                continue
                            if snapshot.details.get("bid") is not None and snapshot.details.get("ask") is not None:
                                broker.manage(snapshot.symbol, snapshot.details["bid"], "BUY", pos.stop, pos.target)
                                broker.manage(snapshot.symbol, snapshot.details["ask"], "SELL", pos.stop, pos.target)
                        else:
                            if pos.symbol == snapshot.symbol:
                                price = snapshot.price
                                broker.manage(snapshot.symbol, price, pos.side, pos.stop, pos.target)
                except Exception:
                    logging.exception("Error scanning market %s:%s", market_type, symbol)
            time.sleep(30)
    finally:
        broker.save()
        if mt5 is not None:
            mt5.shutdown()
        logging.info("Bot stopped. No live order path was enabled.")


def request_stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper-trading bot for MT5 and Binance")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML config file")
    args = parser.parse_args()
    setup_logging()
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    try:
        run(args.config)
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
