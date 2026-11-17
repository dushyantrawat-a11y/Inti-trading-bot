"""Generate a reviewable market list from Binance and/or an MT5 terminal.

New symbols are disabled in generated YAML until a human reviews thresholds.
"""
import argparse
import os
import yaml
import requests

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5=None

BASE="https://api.binance.com"
def binance_symbols(quotes):
    r=requests.get(BASE+"/api/v3/exchangeInfo",timeout=20); r.raise_for_status()
    return [s["symbol"] for s in r.json()["symbols"] if s.get("status")=="TRADING" and s.get("isSpotTradingAllowed",True) and s.get("quoteAsset") in quotes]
def mt5_symbols():
    if mt5 is None: raise RuntimeError("Install MetaTrader5 and run on Windows")
    opts={}
    if os.getenv("MT5_PATH"):opts["path"]=os.getenv("MT5_PATH")
    if not mt5.initialize(**opts):raise RuntimeError(f"MT5 initialization failed: {mt5.last_error()}")
    try:return [s.name for s in (mt5.symbols_get() or []) if s.visible]
    finally:mt5.shutdown()
def main():
    p=argparse.ArgumentParser(); p.add_argument("--provider",choices=["mt5","binance","both"],default="both"); p.add_argument("--quotes",default="USDT,USDC"); p.add_argument("--limit",type=int,default=100); p.add_argument("--output",default="config.discovered.yaml"); a=p.parse_args()
    quotes=set(a.quotes.upper().split(",")); markets=[]
    if a.provider in ("binance","both"):
        for s in binance_symbols(quotes)[:max(1,a.limit)]:markets.append({"type":"binance","enabled":False,"symbol":s,"interval":"15m","min_spread_pct":0.001,"min_volume_usd":500000,"min_flow_imbalance":0.05,"trend_fast_ema":12,"trend_slow_ema":26,"atr_period":14,"stop_atr_multiple":2,"reward_risk_ratio":1.5})
    if a.provider in ("mt5","both"):
        for s in mt5_symbols()[:max(1,a.limit)]:markets.append({"type":"mt5","enabled":False,"symbol":s,"timeframe":"M15","max_spread_points":1500,"min_tick_volume":10,"min_flow_imbalance":0.05,"trend_fast_ema":12,"trend_slow_ema":26,"atr_period":14,"stop_atr_multiple":2,"reward_risk_ratio":1.5})
    cfg={"paper":{"enabled":True,"starting_balance":10000,"risk_per_trade_pct":0.25,"max_daily_loss_pct":2,"max_drawdown_pct":10,"max_total_risk_pct":1,"max_notional_pct":100,"max_open_positions":3,"state_file":"paper_state.json"},"poll_seconds":30,"markets":markets}
    with open(a.output,"w",encoding="utf-8") as f:yaml.safe_dump(cfg,f,sort_keys=False)
    print(f"Wrote {len(markets)} disabled candidates to {a.output}; inspect symbols and thresholds, then enable chosen markets manually.")
if __name__=="__main__":main()
