"""Paper-trading market scanner for MT5 and Binance. No real orders are submitted."""
from __future__ import annotations
import argparse, csv, json, logging, os, signal, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import pandas as pd
import requests
import yaml
from core import RiskEngine, RiskLimits, backtest, indicators
try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None
STOP = False
BASE = "https://api.binance.com"
TF = {"M1":"TIMEFRAME_M1", "M5":"TIMEFRAME_M5", "M15":"TIMEFRAME_M15", "M30":"TIMEFRAME_M30", "H1":"TIMEFRAME_H1", "H4":"TIMEFRAME_H4", "D1":"TIMEFRAME_D1"}


def now(): return datetime.now(timezone.utc)
def log_setup(): logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")])
def load_config(path):
    with open(path, encoding="utf-8") as f: cfg=yaml.safe_load(f) or {}
    paper=cfg.get("paper", {})
    if paper.get("enabled") is not True: raise ValueError("Paper mode must stay enabled; live execution is not implemented")
    if not paper.get("starting_balance", 0)>0: raise ValueError("paper.starting_balance must be positive")
    risk=paper.get("risk_per_trade_pct", 0)
    if not 0<risk<=1: raise ValueError("risk_per_trade_pct must be > 0 and <= 1")
    markets=cfg.get("markets", [])
    if not any(m.get("enabled") is True for m in markets): raise ValueError("Enable at least one reviewed market in config.yaml")
    if any(m.get("type") not in {"mt5","binance"} for m in markets): raise ValueError("Market type must be mt5 or binance")
    return cfg

def append_csv(path, row):
    p=Path(path); exists=p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=list(row));
        if not exists: w.writeheader()
        w.writerow(row)

def init_mt5(cfg):
    if mt5 is None: raise RuntimeError("Install MetaTrader5 on Windows and start/login to the MT5 terminal")
    opts={"timeout":int(cfg.get("mt5",{}).get("timeout_ms",10000))}
    if os.getenv("MT5_PATH"): opts["path"]=os.getenv("MT5_PATH")
    if os.getenv("MT5_LOGIN") and os.getenv("MT5_PASSWORD") and os.getenv("MT5_SERVER"):
        opts.update(login=int(os.environ["MT5_LOGIN"]), password=os.environ["MT5_PASSWORD"], server=os.environ["MT5_SERVER"])
    if not mt5.initialize(**opts): raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

def binance_candles(symbol, interval, limit=500):
    r=requests.get(BASE+"/api/v3/klines", params={"symbol":symbol,"interval":interval,"limit":limit}, timeout=15); r.raise_for_status()
    return pd.DataFrame(r.json(),columns=["time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]).assign(**{}) .pipe(_numeric)
def _numeric(df):
    for c in ["open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    return df

def fetch_binance(m):
    df=binance_candles(m["symbol"],m.get("interval","15m"),250)
    if len(df)<40: return None
    # Ignore the still-forming last candle.
    df=df.iloc[:-1].reset_index(drop=True)
    book=requests.get(BASE+"/api/v3/depth",params={"symbol":m["symbol"],"limit":20},timeout=10); book.raise_for_status(); book=book.json()
    ticker=requests.get(BASE+"/api/v3/ticker/24hr",params={"symbol":m["symbol"]},timeout=10); ticker.raise_for_status(); ticker=ticker.json()
    bids,asks=book.get("bids",[]),book.get("asks",[])
    bidq=sum(float(x[1]) for x in bids[:10]); askq=sum(float(x[1]) for x in asks[:10]); total=bidq+askq
    flow=(bidq-askq)/total if total else None
    spread=(float(asks[0][0])-float(bids[0][0]))/max(float(bids[0][0]),1e-12) if asks and bids else float("inf")
    enriched=indicators(df,int(m.get("trend_fast_ema",12)),int(m.get("trend_slow_ema",26)),int(m.get("atr_period",14)))
    return {"df":enriched,"price":float(ticker["lastPrice"]),"spread":spread,"volume_usd":float(ticker.get("quoteVolume",0)),"flow":flow,"liquidity":bidq+askq,"details":{"bid":float(bids[0][0]),"ask":float(asks[0][0])} if bids and asks else {}}

def fetch_mt5(m):
    if not mt5.symbol_select(m["symbol"],True): return None
    key=str(m.get("timeframe","M15")).upper()
    if key not in TF: raise ValueError(f"Unsupported MT5 timeframe: {key}")
    rates=mt5.copy_rates_from_pos(m["symbol"],getattr(mt5,TF[key]),0,int(m.get("bars",300)))
    info=mt5.symbol_info(m["symbol"]); tick=mt5.symbol_info_tick(m["symbol"])
    if rates is None or info is None or tick is None or len(rates)<40: return None
    df=pd.DataFrame(rates).iloc[:-1].reset_index(drop=True)
    enriched=indicators(df,int(m.get("trend_fast_ema",12)),int(m.get("trend_slow_ema",26)),int(m.get("atr_period",14)))
    spread=(float(tick.ask)-float(tick.bid))/max(float(info.point),1e-12)
    flow=None
    try:
        if mt5.market_book_add(m["symbol"]):
            book=mt5.market_book_get(m["symbol"]) or []
            b=sum(float(x.volume) for x in book if x.type==mt5.BOOK_TYPE_BUY); a=sum(float(x.volume) for x in book if x.type==mt5.BOOK_TYPE_SELL)
            if b+a: flow=(b-a)/(b+a)
            mt5.market_book_release(m["symbol"])
    except Exception: pass
    return {"df":enriched,"price":float(tick.ask),"spread":spread,"volume_usd":float(df.tick_volume.iloc[-1]),"flow":flow,"liquidity":float(df.tick_volume.iloc[-1]),"details":{"bid":float(tick.bid),"ask":float(tick.ask),"info":info}}

class PaperAccount:
    def __init__(self,cfg):
        self.cfg=cfg["paper"]; self.path=Path(self.cfg.get("state_file","paper_state.json")); self.balance=float(self.cfg["starting_balance"]); self.positions={}; self.day=now().date().isoformat(); self.day_start=self.balance; self.peak=self.balance; self._load()
        self.risk=RiskEngine(RiskLimits(risk_per_trade_pct=float(self.cfg["risk_per_trade_pct"]),max_daily_loss_pct=float(self.cfg.get("max_daily_loss_pct",2)),max_drawdown_pct=float(self.cfg.get("max_drawdown_pct",10)),max_total_risk_pct=float(self.cfg.get("max_total_risk_pct",1.5)),max_notional_pct=float(self.cfg.get("max_notional_pct",100)),max_open_positions=int(self.cfg.get("max_open_positions",3))))
    def _load(self):
        if self.path.exists():
            d=json.loads(self.path.read_text(encoding="utf-8")); self.balance=float(d.get("balance",self.balance)); self.positions=d.get("positions",{}); self.day=d.get("day",self.day); self.day_start=float(d.get("day_start",self.balance)); self.peak=float(d.get("peak",self.balance))
    def save(self):
        data={"balance":self.balance,"positions":self.positions,"day":self.day,"day_start":self.day_start,"peak":self.peak}; tmp=self.path.with_suffix(".tmp"); tmp.write_text(json.dumps(data,indent=2),encoding="utf-8"); tmp.replace(self.path)
    def update_day(self):
        today=now().date().isoformat()
        if today!=self.day: self.day=today; self.day_start=self.balance; self.save()
    def equity_controls(self):
        self.update_day(); self.peak=max(self.peak,self.balance)
        open_risk=sum(float(p["risk_cash"]) for p in self.positions.values())
        return self.risk.can_open(self.balance,self.day_start,self.peak,open_risk,len(self.positions))
    def close_check(self,key,price):
        p=self.positions.get(key)
        if not p:return
        hit=(price<=p["stop"] if p["side"]=="BUY" else price>=p["stop"]); target=(price>=p["target"] if p["side"]=="BUY" else price<=p["target"])
        if not (hit or target):return
        exit_price=p["stop"] if hit else p["target"]; reason="stop_loss" if hit else "take_profit"
        pnl=(exit_price-p["entry"] if p["side"]=="BUY" else p["entry"]-exit_price)*p["units"]*p["unit_multiplier"]
        pnl-= (p["entry"]+exit_price)*p["units"]*float(self.cfg.get("fee_rate",0.001))
        self.balance+=pnl; self._event("CLOSE",key,p["side"],exit_price,p["units"],pnl,reason); del self.positions[key]; self.peak=max(self.peak,self.balance); self.save()
    def open(self,key,side,price,atr,m,flow):
        if key in self.positions:return
        ok,reason=self.equity_controls()
        if not ok: logging.info("Risk gate blocked %s: %s",key,reason); return
        stop_dist=atr*float(m.get("stop_atr_multiple",2)); rr=float(m.get("reward_risk_ratio",1.5))
        unit_multiplier=1.0
        if m["type"]=="mt5":
            info=None
            # mt5 contract value approximation converted to account currency by broker metadata.
            # Refuse sizing if tick value is unavailable rather than guess.
            info=_latest_mt5_info.get(key)
            if info is None or not info.trade_tick_size or not info.trade_tick_value: logging.warning("No MT5 tick-value sizing for %s; skip",key); return
            unit_multiplier=float(info.trade_tick_value)/float(info.trade_tick_size)
        stop=price-stop_dist if side=="BUY" else price+stop_dist; target=price+stop_dist*rr if side=="BUY" else price-stop_dist*rr
        units,risk_cash=self.risk.size(self.balance,price,stop,unit_pnl_per_price=unit_multiplier)
        if units<=0:return
        pos={"symbol":m["symbol"],"market":m["type"],"side":side,"entry":price,"stop":stop,"target":target,"units":units,"risk_cash":risk_cash,"unit_multiplier":unit_multiplier,"opened_at":now().isoformat()}
        self.positions[key]=pos; logging.info("PAPER %s %s %.8f SL %.8f TP %.8f size %.8f",side,key,price,stop,target,units); self._event("OPEN",key,side,price,units,0,"signal"); self.save()
    @staticmethod
    def _event(event,key,side,price,units,pnl,note):
        append_csv("trades.csv",{"time_utc":now().isoformat(),"event":event,"market":key,"side":side,"price":price,"units":units,"pnl":pnl,"note":note})

def _signal(m, data, account):
    df=data["df"]
    if len(df)<3:return
    cur=df.iloc[-1]; prev=df.iloc[-2]; action="HOLD"
    if cur.signal==1:action="BUY"
    elif cur.signal==-1:action="SELL"
    flow=data["flow"]
    if flow is not None and abs(flow)<float(m.get("min_flow_imbalance",0.05)): action="HOLD"
    if action=="BUY" and flow is not None and flow<=0: action="HOLD"
    if action=="SELL" and flow is not None and flow>=0: action="HOLD"
    record={"time_utc":now().isoformat(),"market":f"{m['type']}:{m['symbol']}","price":data["price"],"signal":action,"flow":flow if flow is not None else "","spread":data["spread"],"volume":data["volume_usd"],"atr":float(cur.atr) if pd.notna(cur.atr) else "","reason":"EMA crossover; book imbalance is a rough proxy"}
    append_csv("signals.csv",record)
    logging.info("SIGNAL %s %s price=%.8f flow=%s",record["market"],action,data["price"],flow)
    key=record["market"]
    account.close_check(key,data["details"].get("bid",data["price"]) if account.positions.get(key,{}).get("side")=="BUY" else data["details"].get("ask",data["price"]))
    if action in ("BUY","SELL") and pd.notna(cur.atr) and cur.atr>0:
        if m["type"]=="mt5": _latest_mt5_info[key]=data["details"].get("info")
        account.open(key,action,data["price"],float(cur.atr),m,flow)

_latest_mt5_info={}
def run(cfg):
    account=PaperAccount(cfg)
    if any(m.get("enabled") and m["type"]=="mt5" for m in cfg["markets"]):init_mt5(cfg)
    try:
        while not STOP:
            for m in cfg["markets"]:
                if not m.get("enabled"):continue
                try:
                    data=fetch_mt5(m) if m["type"]=="mt5" else fetch_binance(m)
                    if data is None:logging.warning("No data for %s:%s",m["type"],m["symbol"]); continue
                    spread_limit=float(m.get("max_spread_points",float("inf"))) if m["type"]=="mt5" else float(m.get("max_spread_pct",float("inf")))
                    if data["spread"]>spread_limit: logging.info("Skip %s: spread gate",m["symbol"]); continue
                    volume_min=float(m.get("min_tick_volume",0)) if m["type"]=="mt5" else float(m.get("min_volume_usd",0))
                    if data["volume_usd"]<volume_min: logging.info("Skip %s: volume gate",m["symbol"]); continue
                    _signal(m,data,account)
                except Exception:logging.exception("Error scanning %s:%s",m["type"],m["symbol"])
            time.sleep(max(5,int(cfg.get("poll_seconds",30))))
    finally:
        account.save()
        if mt5 is not None:mt5.shutdown()

def load_history(provider,symbol,config,limit):
    if provider=="binance":
        df=binance_candles(symbol,config.get("interval","1h"),min(1000,int(limit)))
        return df.iloc[:-1].rename(columns={"open_time":"time"})
    if mt5 is None:raise RuntimeError("Install MetaTrader5 on Windows to retrieve MT5 history")
    if not mt5.initialize():raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    name=str(config.get("timeframe","H1")).upper()
    if name not in TF:raise ValueError("Unsupported MT5 timeframe")
    if not mt5.symbol_select(symbol,True):raise RuntimeError(f"MT5 symbol not available: {symbol}")
    raw=mt5.copy_rates_from_pos(symbol,getattr(mt5,TF[name]),1,int(limit))
    if raw is None:raise RuntimeError(f"No MT5 historical rates for {symbol}")
    return pd.DataFrame(raw).rename(columns={"time":"time"})

def do_backtest(cfg,provider,symbol,limit,output):
    item=next((x for x in cfg["markets"] if x["type"]==provider and x["symbol"]==symbol),{})
    frame=load_history(provider,symbol,item,limit)
    result=backtest(frame,initial_cash=float(cfg["paper"]["starting_balance"]),risk_pct=float(cfg["paper"]["risk_per_trade_pct"]),fast=int(item.get("trend_fast_ema",12)),slow=int(item.get("trend_slow_ema",26)),atr_period=int(item.get("atr_period",14)),stop_atr=float(item.get("stop_atr_multiple",2)),reward_risk=float(item.get("reward_risk_ratio",1.5)),fee_rate=float(cfg["paper"].get("fee_rate",0.001)),slippage=float(cfg["backtest"].get("slippage",0.0005)),max_daily_loss_pct=float(cfg["paper"].get("max_daily_loss_pct",2)),max_drawdown_pct=float(cfg["paper"].get("max_drawdown_pct",10)),max_notional_pct=float(cfg["paper"].get("max_notional_pct",100)))
    Path(output).write_text(json.dumps(result,indent=2,default=str),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in {"trades","equity_curve"}},indent=2)); print(f"Full results: {output}")

def request_stop(_sig,_frame):
    global STOP; STOP=True

def main():
    p=argparse.ArgumentParser(description="Paper trading and historical backtesting for MT5/Binance")
    p.add_argument("--config",default="config.yaml"); p.add_argument("--backtest",action="store_true"); p.add_argument("--provider",choices=["binance","mt5"]); p.add_argument("--symbol"); p.add_argument("--bars",type=int,default=1000); p.add_argument("--output",default="backtest_results.json")
    a=p.parse_args(); log_setup()
    try:
        cfg=load_config(a.config)
        if a.backtest:
            if not a.provider or not a.symbol: p.error("--backtest needs --provider and --symbol")
            do_backtest(cfg,a.provider,a.symbol,a.bars,a.output)
        else:
            signal.signal(signal.SIGINT,request_stop); run(cfg)
    except Exception as e: logging.exception("Failed: %s",e); return 1
    return 0
if __name__=="__main__":sys.exit(main())
