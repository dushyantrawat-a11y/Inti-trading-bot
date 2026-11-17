from __future__ import annotations

from pathlib import Path

import click
import pandas as pd

from itb.common import compute_feature_set, load_config, write_csv


def generate_features(cfg: dict, df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for feature_set in cfg.get("feature_sets", []):
        out = compute_feature_set(out, feature_set)
    out = out.replace([float("inf"), -float("inf")], float("nan"))
    return out


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    df = pd.read_csv(Path(cfg.get("data_folder", ".")) / "merged.csv")
    out = generate_features(cfg, df)
    feature_path = Path(cfg.get("data_folder", ".")) / "features.csv"
    write_csv(out, feature_path)
    feature_names = [c for c in out.columns if c not in {"time"}]
    (Path(cfg.get("data_folder", ".")) / "feature_names.txt").write_text("\n".join(feature_names), encoding="utf-8")
    print(f"Features written to {feature_path}; {len(feature_names)} columns")


if __name__ == "__main__":
    cli()
