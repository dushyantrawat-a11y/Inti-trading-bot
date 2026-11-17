from __future__ import annotations

import json
from pathlib import Path

import click
import pandas as pd

from itb.common import load_config, safe_model_output


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    df = pd.read_csv(Path(cfg.get("data_folder", ".")) / "matrix.csv")
    model_dir = Path(cfg.get("data_folder", ".")) / "models"
    preds = df.copy()
    preds["prediction"] = 0.0
    for label in cfg.get("labels", []):
        model_path = model_dir / f"{label}.json"
        if not model_path.exists():
            continue
        meta = json.loads(model_path.read_text(encoding="utf-8"))
        features = meta.get("features", [])
        if not features:
            continue
        X = preds[features].fillna(method="ffill").fillna(0)
        preds[f"{label}_pred"] = X.mean(axis=1)
    out_path = Path(cfg.get("data_folder", ".")) / "predictions.csv"
    preds.to_csv(out_path, index=False)
    print(f"Predictions saved to {out_path}")


if __name__ == "__main__":
    cli()
