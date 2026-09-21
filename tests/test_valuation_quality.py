import unittest,tempfile
from pathlib import Path
from datetime import datetime,timedelta
from stock_radar.domain import TW
from stock_radar.valuation_quality import assess
from stock_radar.store import Store
from stock_radar.valuation_engine import publish_all
class QualityTests(unittest.TestCase):
 def test_gap_is_model_uncertainty_not_overpricing(self):
  q=assess({'kind':'stock'},{'buy':1385,'fair':1633.58},{'low_60d':2990,'high_60d':4855})
  self.assertEqual(q['status'],'needs_research');self.assertIn('不能僅據此判定',q['reason'])
 def test_normal_stock_and_etf_not_repriced(self):
  self.assertIsNone(assess({'kind':'stock'},{'buy':90,'fair':100},{'low_60d':80,'high_60d':110}))
  self.assertIsNone(assess({'kind':'etf_equity'},{'buy':10,'fair':15},{'low_60d':100}))
 def test_research_hold_does_not_disappear_when_market_moves(self):
  hold={'status':'needs_research','reason':'需獲利指引'}
  self.assertEqual(assess({'kind':'stock'},{'buy':90},{},hold),hold)
 def test_daily_and_direct_rule_publish_preserve_expired_research(self):
  now=datetime(2026,9,21,10,tzinfo=TW)
  with tempfile.TemporaryDirectory() as td:
   s=Store(Path(td)/'db');inst={'symbol':'TEST','kind':'stock','name':'測試'};s.upsert_instruments([inst]);s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
   v={'sweet':70,'add':80,'buy':90,'method':'pe_band','thesis':'t','reason':'r','as_of':now.isoformat(),'valid_until':(now+timedelta(days=1)).isoformat(),'evidence_reviewed':True,'sources':[{'url':'https://example.com','as_of':now.isoformat()}],'analysis_review':{k:True for k in ('financials','outlook','valuation_basis','price_structure','risks')}}
   pid=s.propose('TEST',v,now=now);s.publish_reviewed_analysis(pid,now=now)
   later=now+timedelta(days=2);rule={**v,'sweet':60,'review_mode':'rule-engine-v1','as_of':later.isoformat(),'valid_until':(later+timedelta(days=2)).isoformat()};rule.pop('analysis_review')
   self.assertEqual(publish_all(s,later,[(inst,rule,None)])[0][1],'skip');self.assertEqual(s.active('TEST')['id'],pid)
   rid=s.propose('TEST',rule,now=later)
   with self.assertRaisesRegex(ValueError,'不得覆蓋'):s.publish_rule_valuation(rid,now=later)
   self.assertEqual(s.active('TEST')['id'],pid);s.close()
