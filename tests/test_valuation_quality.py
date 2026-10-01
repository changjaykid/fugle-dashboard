import unittest,tempfile
from pathlib import Path
from datetime import datetime,timedelta
from stock_radar.domain import TW
from stock_radar.valuation_quality import ANCHOR_GRACE,anchor_state,assess
from stock_radar.store import Store
from stock_radar.valuation_engine import publish_all

NOW=datetime(2026,9,21,10,tzinfo=TW)
REVIEW={k:True for k in ('financials','outlook','valuation_basis','price_structure','risks')}

def anchored(now=NOW,days=1):
 return {'sweet':70,'add':80,'buy':90,'method':'pe_band','thesis':'t','reason':'r','as_of':now.isoformat(),
         'valid_until':(now+timedelta(days=days)).isoformat(),'evidence_reviewed':True,
         'sources':[{'url':'https://example.com','as_of':now.isoformat()}],'analysis_review':dict(REVIEW)}

def screen(at,base):
 out={**base,'sweet':60,'review_mode':'rule-engine-v1','as_of':at.isoformat(),
      'valid_until':(at+timedelta(days=2)).isoformat()}
 out.pop('analysis_review');return out

def researched_store(td,now=NOW):
 s=Store(Path(td)/'db');inst={'symbol':'TEST','kind':'stock','name':'測試'}
 s.upsert_instruments([inst])
 s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
 pid=s.propose('TEST',anchored(now),now=now);s.publish_reviewed_analysis(pid,now=now)
 return s,inst,pid

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
 def test_daily_and_direct_rule_publish_preserve_research_inside_the_grace_window(self):
  # Unchanged intent: a screen never outranks a researched price, and an expiry alone does not release it.
  with tempfile.TemporaryDirectory() as td:
   s,inst,pid=researched_store(td)
   later=NOW+timedelta(days=2);rule=screen(later,anchored())
   self.assertEqual(publish_all(s,later,[(inst,rule,None)])[0][1],'skip');self.assertEqual(s.active('TEST')['id'],pid)
   rid=s.propose('TEST',rule,now=later)
   with self.assertRaisesRegex(ValueError,'不得覆蓋'):s.publish_rule_valuation(rid,now=later)
   self.assertEqual(s.active('TEST')['id'],pid);s.close()
 def test_rule_engine_takes_over_once_the_grace_window_has_passed(self):
  # Holding a lapsed anchor for ever removed its symbol from the system permanently: research_llm only
  # wakes symbols whose plan is actionable, and an expired valuation makes the plan stale, so nothing
  # could ever renew it. 2454 was four days from this on 2026-10-01.
  with tempfile.TemporaryDirectory() as td:
   s,inst,pid=researched_store(td)
   after=NOW+timedelta(days=1)+ANCHOR_GRACE+timedelta(minutes=1);rule=screen(after,anchored())
   self.assertEqual(publish_all(s,after,[(inst,rule,None)])[0][1],'handover')
   self.assertNotEqual(s.active('TEST')['id'],pid)
   self.assertEqual(s.active('TEST')['review_mode'],'rule-engine-v1')
   self.assertEqual(s.meta('research_anchors')['states'],{'TEST':'lapsed'})
   # The handover is a recorded act, not a silent overwrite.
   actions=[r[0] for r in s.db.execute('SELECT action FROM audit')]
   self.assertIn('valuation_anchor_handover',actions)
   s.close()
 def test_anchor_state_names_the_three_phases(self):
  self.assertIsNone(anchor_state({'review_mode':'rule-engine-v1'},NOW))
  v=anchored()
  self.assertEqual(anchor_state(v,NOW),'live')
  self.assertEqual(anchor_state(v,NOW+timedelta(days=1,hours=1)),'grace')
  self.assertEqual(anchor_state(v,NOW+timedelta(days=1)+ANCHOR_GRACE+timedelta(minutes=1)),'lapsed')
  # An unreadable date is not evidence that the anchor lapsed, so it keeps its protection.
  self.assertEqual(anchor_state({**v,'valid_until':None},NOW+timedelta(days=400)),'live')
