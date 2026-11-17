from __future__ import annotations

from pathlib import Path

import click
import pandas as pd

from itb.common import load_config


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    df = pd.read_csv(Path(cfg.get("data_folder", ".")) / "matrix.csv")
    start = int(cfg.get("rolling_predict", {}).get("prediction_start", 0)) if cfg.get("rolling_predict") else 0
    size = int(cfg.get("rolling_predict", {}).get("prediction_size", 100)) if cfg.get("rolling_predict") else 100
    steps = int(cfg.get("rolling_predict", {}).get("prediction_steps", 1)) if cfg.get("rolling_predict") else 1
    if start == 0:
        start = len(df) - size
    out = []
    for step in range(steps):
        train_start = 0
        train_end = start + (step * size)
        if train_end >= len(df):
            break
        chunk = df.iloc[train_start: train_end]
        chunk["rolling_pred"] = chunk.get("close", pd.Series([0] * len(chunk))).rolling(3).mean()
        out.append(chunk)
    result = pd.concat(out, ignore_index=True) if out else df.iloc[[0]].copy()
    out_path = Path(cfg.get("data_folder", ".")) / "rolling_predictions.csv"
    result.to_csv(out_path, index=False)
    print(f"Rolling predictions saved to {out_path}")


if __name__ == "__main__":
    cli()
