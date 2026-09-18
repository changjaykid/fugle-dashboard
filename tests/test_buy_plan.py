import unittest,copy
from datetime import datetime,timedelta
from stock_radar.domain import TW
from stock_radar.buy_plan import make_plan,gate_signal,last_completed_day
class PlanTests(unittest.TestCase):
 def setUp(self):
  self.now=datetime(2026,9,15,8,50,tzinfo=TW);self.i={'kind':'stock'}
  self.v={'sweet':70,'add':80,'buy':90,'method':'research','thesis':'fixture','reason':'fixture','as_of':(self.now-timedelta(days=1)).isoformat(),'valid_until':(self.now+timedelta(days=21)).isoformat(),'evidence_reviewed':True,'sources':[{'url':'https://example.com/report','as_of':(self.now-timedelta(days=1)).isoformat()}]}
  self.c={'close':85,'trade_date':'2026-09-14'};self.t={'as_of':'2026-09-14','flow_as_of':'2026-09-14','support':79,'supports':[79,83],'requires_stabilization':False};self.r={'cleared':True,'events':[],'checked_at':self.now.isoformat()};self.cal={'roc_years':[115],'closed_dates':{}}
 def plan(self):return make_plan(self.i,self.v,self.c,self.t,self.r,self.cal,self.now)
 def test_plan_is_conditional_and_never_live_order(self):
  p=self.plan();self.assertEqual(p['entry'],83);self.assertEqual(p['band'],'buy');self.assertEqual(p['status'],'conditional');self.assertFalse(p['is_live_order'])
 def test_stabilization_blocks_live_signal_but_keeps_plan(self):
  self.t['requires_stabilization']=True;p=self.plan();self.assertEqual(p['entry'],83);self.assertEqual(p['status'],'wait_stabilize');self.assertIsNone(gate_signal({'status':'buy','suggested':84},p)['suggested'])
 def test_stale_structure_risk_close_and_expired_valuation_hide_entry(self):
  for field,bad in [('c',{'close':85,'trade_date':'2026-09-11'}),('t',{**self.t,'flow_as_of':'2026-09-11'}),('r',{**self.r,'cleared':False}),('v',{**self.v,'valid_until':self.now.isoformat()})]:
   with self.subTest(field=field):
    old=getattr(self,field);setattr(self,field,bad);self.assertIsNone(self.plan()['entry']);setattr(self,field,old)
 def test_above_cap_and_broken_support(self):
  self.c['close']=95;self.assertEqual(self.plan()['status'],'no_chase');self.assertIsNone(self.plan()['entry'])
  self.c['close']=78;self.assertEqual(self.plan()['status'],'broken');self.assertIsNone(self.plan()['entry'])
 def test_weekend_and_missing_calendar(self):
  self.assertEqual(str(last_completed_day(datetime(2026,9,14,8,tzinfo=TW),self.cal)),'2026-09-11')
  self.cal={};self.assertIsNone(self.plan()['entry'])

 def test_buy_cap_far_below_traded_range_is_overvalued(self):
  self.t.update(low_60d=120,high_60d=180);self.c['close']=150;p=self.plan()
  self.assertEqual(p['status'],'overvalued');self.assertEqual(p['band'],'avoid');self.assertIsNone(p['entry'])
  self.assertIsNone(gate_signal({'status':'buy','suggested':89},p)['suggested'])
 def test_fair_far_above_traded_range_blocks_sweet_signal(self):
  self.v['fair']=150;self.t.update(low_60d=75,high_60d=100);p=self.plan()
  self.assertEqual(p['status'],'valuation_gap');self.assertIsNone(p['entry'])
  self.assertEqual(gate_signal({'status':'sweet','suggested':84},p)['status'],'blocked')
 def test_valuation_inside_traded_range_keeps_plan(self):
  self.v['fair']=105;self.t.update(low_60d=80,high_60d=100);p=self.plan()
  self.assertEqual(p['status'],'conditional');self.assertEqual(p['entry'],83)

 def test_current_trial_breakdown_revokes_otherwise_valid_plan(self):
  p=self.plan();self.assertEqual(p['status'],'conditional')
  sig=gate_signal({'status':'buy','suggested':78},p,{'is_trial':True,'trial_price':78})
  self.assertEqual(sig['status'],'blocked');self.assertIsNone(sig['suggested'])

class OutlookTests(unittest.TestCase):
 def test_outlook_ranks_the_whole_market_and_reports_the_watchlist(self):
  import tempfile
  from pathlib import Path
  from stock_radar.store import Store
  from stock_radar.buy_plan import format_plans
  now=datetime(2026,9,20,20,tzinfo=TW);d=tempfile.TemporaryDirectory();s=Store(Path(d.name)/'t.db')
  s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
  s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{}})
  syms=[(f'S{k}','stock',False) for k in range(7)]+[('W','stock',True)]
  s.upsert_instruments([{'symbol':a,'name':'名'+a,'kind':k,'watched':w} for a,k,w in syms])
  for k,(a,_,w) in enumerate(syms):
   v={'sweet':70 if not w else 30,'add':80 if not w else 40,'buy':90 if not w else 50,'fair':100,'method':'pe_band','inputs':{'pe_today':20-k if not w else 30,'fair_pe':20},'thesis':'t','reason':'r','as_of':(now-timedelta(hours=1)).isoformat(),'valid_until':(now+timedelta(days=3)).isoformat(),'evidence_reviewed':True,'analysis_review':{x:True for x in ('financials','outlook','valuation_basis','price_structure','risks')},'sources':[{'url':'https://example.com','as_of':(now-timedelta(hours=1)).isoformat()}]}
   s.publish_reviewed_analysis(s.propose(a,v,now=now-timedelta(minutes=30)),now=now-timedelta(minutes=30))
   s.set_fact(a,'public_close',{'close':85,'trade_date':'2026-09-18'})
   s.set_fact(a,'technical',{'as_of':'2026-09-18','flow_as_of':'2026-09-18','support':79,'supports':[79,83],'requires_stabilization':False})
   s.set_fact(a,'risk',{'cleared':True,'events':[],'checked_at':(now-timedelta(hours=2)).isoformat()})
   if a!='S6':s.set_fact(a,'research_detail',{'thesis':'看好理由','risks':['主要風險'],'as_of':(now-timedelta(days=1)).isoformat(),'valid_until':(now+timedelta(days=10)).isoformat()})
  text=format_plans(s,now);s.close();d.cleanup()
  self.assertEqual(text.count('承接 83 元'),5);self.assertNotIn('① 名S6',text);self.assertIn('待研究池',text);self.assertIn('名S6',text[text.index('待研究池'):]);self.assertNotIn('名S0 ',text)
  self.assertIn('你的關注：名W 不追',text)
