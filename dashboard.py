"""Local read-only Streamlit dashboard for signals, paper trades and backtests."""
from pathlib import Path
import json
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Paper Trading Monitor", page_icon="📊", layout="wide")
st.title("📊 MT5 + Crypto Paper Trading Monitor")
st.caption("Read-only local dashboard. It does not place or modify orders.")

def csv_data(name):
    p=Path(name)
    if not p.exists():return pd.DataFrame()
    try:return pd.read_csv(p).tail(1000)
    except (OSError,pd.errors.EmptyDataError):return pd.DataFrame()
state_path=Path("paper_state.json")
state={}
if state_path.exists():
    try:state=json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError,OSError):st.warning("Paper state file could not be read")
signals=csv_data("signals.csv"); trades=csv_data("trades.csv")
c1,c2,c3=st.columns(3)
c1.metric("Paper balance",f"{float(state.get('balance',0)):,.2f}")
c2.metric("Open positions",len(state.get("positions",{})))
c3.metric("Latest signal",str(signals.iloc[-1].get("signal","—")) if not signals.empty else "—")
if state.get("positions"):
    st.subheader("Open paper positions"); st.dataframe(pd.DataFrame(state["positions"].values()),use_container_width=True)
st.subheader("Recent buy/sell signals")
if signals.empty:st.info("No signals yet. Start bot.py to scan configured markets.")
else:st.dataframe(signals.iloc[::-1],use_container_width=True)
st.subheader("Paper trade events")
if trades.empty:st.info("No trades recorded yet.")
else:
    st.dataframe(trades.iloc[::-1],use_container_width=True)
    if "pnl" in trades:
        st.line_chart(pd.to_numeric(trades["pnl"],errors="coerce").fillna(0).cumsum())
result_path=Path("backtest_results.json")
if result_path.exists():
    st.subheader("Most recent backtest")
    try:
        result=json.loads(result_path.read_text(encoding="utf-8")); st.json({k:v for k,v in result.items() if k not in {"trades","equity_curve"}})
        curve=result.get("equity_curve",[])
        if curve:st.line_chart(pd.DataFrame(curve).set_index("index"))
    except (ValueError,OSError):st.warning("Backtest result file could not be read")
st.caption("Signals and backtests are experimental. Simulated fills omit many real-market risks; no profitability is implied.")
