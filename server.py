from __future__ import annotations

import json
from pathlib import Path

import click
import pandas as pd

from itb.common import load_config


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    df = pd.read_csv(Path(cfg.get("data_folder", ".")) / "signals.csv")
    outputs = []
    for gen in cfg.get("output_sets", []):
        outputs.append({"generator": gen.get("generator"), "config": gen.get("config", {})})
    payload = {"rows": len(df), "signals": outputs, "sample": df.tail(5).to_dict(orient="records")}
    out_path = Path(cfg.get("data_folder", ".")) / "output.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Output summary written to {out_path}")


if __name__ == "__main__":
    cli()
