from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from itb.common import load_config

DEFAULT_CONFIG = {
    "venue": "binance",
    "symbol": "BTCUSDT",
    "freq": "1h",
    "time_column": "time",
    "data_folder": "C:/DATA_ITB",
    "train": True,
    "label_horizon": 24,
    "features_horizon": 672,
    "train_length": 0,
    "predict_length": 168,
    "append_overlap_records": 5,
    "data_sources": [
        {"folder": "BTCUSDT", "file": "klines", "column_prefix": ""},
        {"folder": "EURUSD", "file": "klines", "column_prefix": ""},
    ],
    "feature_sets": [
        {"generator": "talib", "config": {"columns": ["close"], "functions": ["SMA"], "windows": [1, 3, 6]}}
    ],
    "label_sets": [
        {"generator": "highlow2", "config": {"columns": ["close", "high", "low"], "function": "high", "thresholds": [3.0], "horizon": 24, "names": ["high_30"]}}
    ],
    "train_features": ["close_SMA_1", "close_SMA_3", "close_SMA_6"],
    "labels": ["high_30"],
    "algorithms": [{"name": "svc", "algo": "svc", "params": {"is_scale": True}, "train": {"C": 1.0}}],
    "simulate_model": {"direction": "long", "grid": {"buy_signal_threshold": [0.01], "sell_signal_threshold": [-0.01]}},
    "collector": {"depth": False, "stream": False},
    "telegram": {"bot_token": "", "chat_id": ""},
    "mt5": {"login": "", "password": "", "server": "", "path": ""},
}


@dataclass
class App:
    config: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_CONFIG))
    event_loop: Any = None
    scheduler: Any = None
    analyzer: Any = None
    status_flags: dict[str, bool] = field(default_factory=lambda: {
        "error_status": False,
        "server_status": False,
        "account_status": True,
        "trade_state_status": True,
    })
    latest_df: Any = None
    orders: list[dict[str, Any]] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    account_info: dict[str, Any] = field(default_factory=dict)
    model_store: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config_file(cls, path: str | Path) -> "App":
        return cls(config=load_config(path))

    def load_config(self, path: str | Path) -> None:
        self.config = {**DEFAULT_CONFIG, **load_config(path)}

    def data_provider_problems_exist(self) -> bool:
        return any(self.status_flags.get(k, False) for k in ("error_status",))

    def problems_exist(self) -> bool:
        return any(self.status_flags.values())


def write_default_config(path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
