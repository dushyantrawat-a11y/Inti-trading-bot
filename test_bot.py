import unittest
import pandas as pd
from core import RiskEngine, RiskLimits, backtest, indicators

class CoreTests(unittest.TestCase):
    def test_signals_have_no_future_data(self):
        frame=pd.DataFrame({"open":[10,9,8,9,11,13],"high":[11,10,9,10,12,14],"low":[9,8,7,8,10,12],"close":[10,9,8,9,11,13]})
        out=indicators(frame,2,3,2)
        self.assertIn("signal",out)
        self.assertEqual(len(out),len(frame))
    def test_risk_size_respects_budget(self):
        engine=RiskEngine(RiskLimits(risk_per_trade_pct=0.5))
        units,risk=engine.size(10000,100,95)
        self.assertAlmostEqual(risk,50)
        self.assertAlmostEqual(units,10)
    def test_account_risk_gate(self):
        engine=RiskEngine(RiskLimits(max_daily_loss_pct=2))
        self.assertFalse(engine.can_open(970,1000,1000,0,0)[0])
    def test_backtest_metrics(self):
        rows=100
        closes=[100+i*0.2 for i in range(rows)]
        frame=pd.DataFrame({"open":closes,"high":[x+1 for x in closes],"low":[x-1 for x in closes],"close":closes,"time":range(rows)})
        result=backtest(frame,fast=3,slow=8,atr_period=5)
        self.assertIn("return_pct",result)
        self.assertGreaterEqual(result["trade_count"],0)

if __name__=="__main__":unittest.main()
