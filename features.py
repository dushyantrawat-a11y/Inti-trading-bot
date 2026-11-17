from __future__ import annotations

from pathlib import Path

import click
import pandas as pd

from itb.common import ensure_cols, load_config, make_time_index, write_csv


def merge_sources(cfg: dict) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for src in cfg.get("data_sources", []):
        path = Path(cfg.get("data_folder", ".")) / src.get("folder", "") / f"{src.get('file')}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if df.empty:
            continue
        prefix = src.get("column_prefix", "")
        if prefix:
            df = df.rename(columns={c: f"{prefix}{c}" for c in df.columns if c != "time"})
        df = df.rename(columns={"time": "time"})
        frames.append(df)
    if not frames:
        raise FileNotFoundError("No source files found to merge")
    merged = frames[0]
    for other in frames[1:]:
        merged = merged.merge(other, on="time", how="outer")
    merged = merged.sort_values("time").reset_index(drop=True)
    if cfg.get("train_length"):
        merged = merged.iloc[: int(cfg["train_length"])]
    else:
        keep = int(cfg.get("predict_length", 168)) + int(cfg.get("features_horizon", 672))
        merged = merged.iloc[-keep:]
    merged = merged.set_index(pd.to_datetime(merged["time"]))
    merged = merged.drop(columns=["time"])
    return merged


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    merged = merge_sources(cfg)
    out_path = Path(cfg.get("data_folder", ".")) / "merged.csv"
    write_csv(merged.reset_index().rename(columns={"index": "time"}), out_path)
    print(f"Merged data written to {out_path}")


if __name__ == "__main__":
    cli()
