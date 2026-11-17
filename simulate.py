from __future__ import annotations

from pathlib import Path

import click
import pandas as pd

from itb.common import load_config


def apply_signal_rule(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    for signal_set in cfg.get("signal_sets", []):
        config = signal_set.get("config", {})
        if signal_set.get("generator") == "combine":
            cols = config.get("columns", [])
            if isinstance(cols, str):
                cols = [cols]
            values = [out[col].fillna(0.0) for col in cols]
            if config.get("combine") == "difference":
                out[config.get("names", "trade_score")] = values[0] - values[1] if len(values) > 1 else values[0]
            else:
                out[config.get("names", "trade_score")] = values[0] if len(values) > 0 else 0.0
        elif signal_set.get("generator") == "threshold_rule":
            col = config.get("columns", "trade_score")
            buy_thr = config.get("parameters", {}).get("buy_signal_threshold", 0.08)
            sell_thr = config.get("parameters", {}).get("sell_signal_threshold", -0.08)
            out["buy_signal"] = (out[col] >= buy_thr).astype(int)
            out["sell_signal"] = (out[col] <= sell_thr).astype(int)
    return out


@click.command()
@click.option("-c", "--config", "config_path", required=True, type=click.Path(exists=True))
def cli(config_path: str) -> None:
    cfg = load_config(config_path)
    df = pd.read_csv(Path(cfg.get("data_folder", ".")) / "predictions.csv")
    result = apply_signal_rule(df, cfg)
    out_path = Path(cfg.get("data_folder", ".")) / "signals.csv"
    result.to_csv(out_path, index=False)
    print(f"Signals saved to {out_path}")


if __name__ == "__main__":
    cli()
