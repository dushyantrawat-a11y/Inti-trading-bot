from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Venue(Enum):
    YAHOO = "YAHOO"
    BINANCE = "BINANCE"
    MT5 = "MT5"


@dataclass
class AccountBalances:
    btc: float = 0.0
    usdt: float = 0.0


@dataclass
class MT5AccountInfo:
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    leverage: float = 1.0
    currency: str = "USD"
