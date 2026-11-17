from __future__ import annotations

import json
from pathlib import Path

import click
import pandas as pd
import requests

from itb.common import load_config, write_csv


def download_binance_klines(symbol: str, interval: str = "1h", limit: int = 500) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    r = requests.get(url, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    cols = ["time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    return df[["time", "open", "high", "low", "close", "volume"]].dropna()


def download_yahoo_prices(symbol: str, interval: str = "1h", period: str = "730d") -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Install yfinance to use Yahoo downloader") from exc
    ticker = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)
    if ticker.empty:
        return pd.DataFrame()
    df = ticker.reset_index().rename(columns={"Date": "time"})
    df["time"] = pd.to_datetime(df["time"]) if "time" in df.columns else pd.to_datetime(df.index)
    return df[["time", "Open", "High", "Low", "Close", "Volume"]].rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})


def download_venue_data(cfg: dict, source: dict) -> pd.DataFrame:
    folder = source.get("folder")
    file = source.get("file")
    venue = cfg.get("venue", "binance").lower()
    if venue == "binance":
        symbol = cfg.get("symbol", "BTCUSDT")
        df = download_binance_klines(symbol, cfg.get("freq", "1h"))
    elif venue == "yahoo":
        symbol = cfg.get("symbol", "BTC-USD")
        df = download_yahoo_prices(symbol, cfg.get("freq", "1h"))
    else:
        df = pd.DataFrame({"time": [], "open": [], "high": [], "low": [], "close": [], "volume": []})
    if df.empty:
        return df
    out_path = Path(cfg.get("data_folder", ".")) / folder / f"{file}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return df


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    for source in cfg.get("data_sources", []):
        df = download_venue_data(cfg, source)
        print(f"Downloaded {len(df)} rows for {source.get('folder')} -> {source.get('file')}")


if __name__ == "__main__":
    cli()
