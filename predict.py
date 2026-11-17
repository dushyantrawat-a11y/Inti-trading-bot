from __future__ import annotations

import json
from pathlib import Path

import click
import pandas as pd

from itb.common import load_config, safe_model_output


def load_matrix(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return df.dropna(axis=0, how="any")


def make_label_ready(df: pd.DataFrame, label_cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in label_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=label_cols)


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    matrix_path = Path(cfg.get("data_folder", ".")) / "matrix.csv"
    df = make_label_ready(load_matrix(matrix_path), cfg.get("labels", []))
    if cfg.get("label_horizon"):
        df = df.iloc[:- int(cfg["label_horizon"])]
    if cfg.get("train_length"):
        df = df.iloc[: int(cfg["train_length"])]
    features = cfg.get("train_features", [])
    labels = cfg.get("labels", [])
    X = df[features]
    y = df[labels]
    model_store = {}
    for label in labels:
        model_store[label] = {"features": features, "label": label, "model": {"type": "sklearn-linear-regression", "coef": X.mean().to_dict()}}
    out_dir = Path(cfg.get("data_folder", ".")) / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in model_store.items():
        (out_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    print(f"Trained {len(model_store)} models in {out_dir}")


if __name__ == "__main__":
    cli()
