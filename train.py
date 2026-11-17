from __future__ import annotations

from pathlib import Path

import click
import pandas as pd

from itb.common import load_config, write_csv


def highlow_label(df: pd.DataFrame, cfg: dict) -> pd.Series:
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    horizon = int(cfg.get("horizon", 24))
    out = []
    for i in range(len(df)):
        future_high = high.iloc[i + 1 : i + 1 + horizon].max() if i + 1 + horizon <= len(df) else float("nan")
        future_low = low.iloc[i + 1 : i + 1 + horizon].min() if i + 1 + horizon <= len(df) else float("nan")
        if pd.isna(future_high) or pd.isna(future_low):
            out.append(float("nan"))
            continue
        if cfg.get("function") == "high":
            val = (future_high - close.iloc[i]) / close.iloc[i] if close.iloc[i] else 0.0
        else:
            val = (close.iloc[i] - future_low) / close.iloc[i] if close.iloc[i] else 0.0
        out.append(val)
    return pd.Series(out, name=cfg.get("names", ["label"])[0])


def generate_labels(cfg: dict, df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for label_set in cfg.get("label_sets", []):
        label_cfg = label_set.get("config", {})
        label_name = (label_cfg.get("names") or ["label"])[0]
        out[label_name] = highlow_label(out, label_cfg)
    return out


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    df = pd.read_csv(Path(cfg.get("data_folder", ".")) / "features.csv")
    out = generate_labels(cfg, df)
    label_path = Path(cfg.get("data_folder", ".")) / "matrix.csv"
    write_csv(out, label_path)
    label_names = [c for c in out.columns if c not in {"time"} and c not in {"open", "high", "low", "close", "volume"}]
    (Path(cfg.get("data_folder", ".")) / "label_names.txt").write_text("\n".join(label_names), encoding="utf-8")
    print(f"Labels written to {label_path}; {len(label_names)} labels")


if __name__ == "__main__":
    cli()
