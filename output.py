from __future__ import annotations

from itertools import product
from pathlib import Path

import click
import pandas as pd

from itb.common import load_config


def evaluate_thresholds(df: pd.DataFrame, buy_thr: float, sell_thr: float, direction: str = "long") -> dict:
    pos = 0
    pnl = 0.0
    trades = 0
    for i in range(1, len(df)):
        prev_close = float(df.iloc[i - 1]["close"])
        curr_close = float(df.iloc[i]["close"])
        signal = df.iloc[i].get("buy_signal", 0) - df.iloc[i].get("sell_signal", 0)
        if direction == "long":
            if pos == 0 and signal > 0:
                pos = 1
                entry = prev_close
            elif pos == 1 and signal < 0:
                pnl += curr_close - entry
                pos = 0
                trades += 1
            elif pos == 0 and signal < 0:
                pos = -1
                entry = prev_close
            elif pos == -1 and signal > 0:
                pnl += entry - curr_close
                pos = 0
                trades += 1
        else:
            if pos == 0 and signal < 0:
                pos = 1
                entry = prev_close
                # inverted short logic for flexibility
            elif pos == 1 and signal > 0:
                pnl += entry - curr_close
                pos = 0
                trades += 1
    return {"pnl": pnl, "trades": trades}


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    df = pd.read_csv(Path(cfg.get("data_folder", ".")) / "signals.csv")
    params = cfg.get("simulate_model", {}).get("grid", {})
    buy_list = params.get("buy_signal_threshold", [0.08])
    sell_list = params.get("sell_signal_threshold", [-0.08])
    results = []
    for buy_thr, sell_thr in product(buy_list, sell_list):
        outcome = evaluate_thresholds(df, float(buy_thr), float(sell_thr), cfg.get("simulate_model", {}).get("direction", "long"))
        results.append({"buy_threshold": buy_thr, "sell_threshold": sell_thr, **outcome})
    results = sorted(results, key=lambda x: x["pnl"], reverse=True)
    out_path = Path(cfg.get("data_folder", ".")) / "simulation.csv"
    pd.DataFrame(results).to_csv(out_path, index=False)
    print(f"Simulation saved to {out_path}")


if __name__ == "__main__":
    cli()
