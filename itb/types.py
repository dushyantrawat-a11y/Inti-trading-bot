from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


def strip_json_comments(text: str) -> str:
    text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
    return text


def load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    cleaned = strip_json_comments(text)
    if not cleaned.strip():
        return {}
    return json.loads(cleaned)


def ensure_cols(df: pd.DataFrame, cols: list[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")


def write_csv(df: pd.DataFrame, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False)


def to_numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def make_time_index(df: pd.DataFrame, time_col: str = "time") -> pd.DataFrame:
    out = df.copy()
    out[time_col] = pd.to_datetime(out[time_col], errors="coerce")
    out = out.dropna(subset=[time_col]).sort_values(time_col)
    out = out.set_index(pd.DatetimeIndex(out[time_col]))
    out = out.drop(columns=[time_col])
    return out


def sma(df: pd.DataFrame, column: str, window: int) -> pd.Series:
    return pd.Series(to_numeric_series(df[column]).rolling(window, min_periods=1).mean(), name=f"{column}_SMA_{window}")


def linear_regression_slope(df: pd.DataFrame, column: str, window: int) -> pd.Series:
    values = to_numeric_series(df[column])
    idx = list(range(len(values)))
    slope = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        sub = values.iloc[start : i + 1]
        if len(sub) < 2:
            slope.append(float("nan"))
            continue
        x = pd.Series(range(len(sub)))
        y = sub.reset_index(drop=True)
        coeff = pd.Series(y).reset_index(drop=True)
        if len(coeff) >= 2:
            y_mean = y.mean()
            x_mean = x.mean()
            num = ((x - x_mean) * (y - y_mean)).sum()
            den = ((x - x_mean) ** 2).sum()
            slope_val = float(num / den) if den else 0.0
        else:
            slope_val = 0.0
        slope.append(slope_val)
    return pd.Series(slope, name=f"{column}_LINEARREG_SLOPE_{window}")


def stddev(df: pd.DataFrame, column: str, window: int) -> pd.Series:
    return pd.Series(to_numeric_series(df[column]).rolling(window, min_periods=1).std(ddof=0), name=f"{column}_STDDEV_{window}")


def compute_feature_set(df: pd.DataFrame, feature_cfg: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    generator = feature_cfg.get("generator")
    config = feature_cfg.get("config", {})
    columns = config.get("columns", [])
    if generator == "talib":
        funcs = config.get("functions", [])
        windows = config.get("windows", [])
        for col in columns:
            for fn in funcs:
                for w in windows:
                    if fn == "SMA":
                        out[f"{col}_SMA_{w}"] = sma(out, col, w)
                    elif fn == "LINEARREG_SLOPE":
                        out[f"{col}_LINEARREG_SLOPE_{w}"] = linear_regression_slope(out, col, w)
                    elif fn == "STDDEV":
                        out[f"{col}_STDDEV_{w}"] = stddev(out, col, w)
        return out
    if generator == "train_features":
        return out
    raise ValueError(f"Unsupported feature generator: {generator}")


def build_signal_from_prediction(pred: pd.DataFrame, buy_col: str = "buy_signal", sell_col: str = "sell_signal") -> pd.DataFrame:
    out = pred.copy()
    if buy_col in out.columns and sell_col in out.columns:
        return out
    out[buy_col] = (out["prediction"] > 0.5).astype(int)
    out[sell_col] = (out["prediction"] < -0.5).astype(int)
    return out


def safe_model_output(data: pd.DataFrame, label_col: str | None = None) -> pd.DataFrame:
    out = data.copy()
    if label_col and label_col in out.columns:
        out[label_col] = pd.to_numeric(out[label_col], errors="coerce")
    return out
