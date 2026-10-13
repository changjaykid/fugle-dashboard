import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timedelta
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar.research_brief import parse_closes,install,build,scan_morning

class ResearchTests(unittest.TestCase):
 def test_close_dates_and_not_intraday(self):
  now=datetime(2026,9,14,8,50,tzinfo=TW)
  text='日期,證券代號,收盤價\n1150911,2330,2410\n1150914,2317,248\n1150915,2303,140\n1150911,0000,NaN\n'
  rows=parse_closes(text,now)
  self.assertEqual(list(rows),['2330']);self.assertEqual(rows['2330']['trade_date'],'2026-09-11')
 def test_import_never_activates_and_retries_deduplicate(self):
  now=datetime(2026,9,14,10,tzinfo=TW)
  proposal={'id':'fixture-v1','sweet':65,'add':75,'buy':85,'method':'research','thesis':'fixture','reason':'fixture','as_of':now.isoformat(),'valid_until':(now+timedelta(days=30)).isoformat(),'evidence_reviewed':True,'sources':[{'url':'https://example.com/filing','as_of':now.isoformat()}]}
  r={'symbol':'TEST','name':'測試','as_of':now.isoformat(),'valid_until':proposal['valid_until'],'proposal':proposal,'thesis':'fixture','why_now':'fixture','chips':'missing','catalysts':[],'risks':['fixture'],'sources':proposal['sources']}
  doc={'items':[r]}
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'test.db');s.upsert_instruments([{'symbol':r['symbol'],'name':r['name'],'kind':'stock','watched':True} for r in doc['items']]);install(s,doc,now);install(s,doc,now)
   self.assertEqual(sum(len(s.versions(r['symbol'])) for r in doc['items']),1)
   self.assertTrue(all(s.active(r['symbol']) is None for r in doc['items']))
   text=build(s,now);self.assertLessEqual(len(text),1900);self.assertIn('目前沒有資料完整的承接計畫',text)
   self.assertNotIn('提案買進上限',build(s,now+timedelta(days=31)))
   s.close()

class ScanMorningTests(unittest.TestCase):
 NOW=datetime(2026,10,14,8,55,tzinfo=TW)
 def scan(self,**over):
  c={'symbol':'2330','name':'台積電','close':1000.0,'limit':960.0,'target':1060.0,'stop':864.0,'middle':1010.0,
     'target_pct':10.42,'below_close_pct':4.0,'why':'上升趨勢中回檔 5.0%、貼近月線','flow_share':0.03,'flow_lots':900,'relative_volume':0.8}
  base={'version':'swing-v1','day':'2026-10-13','market_open':True,'tally':{'universe':1000,'judged':900,'buy':1},
        'candidates':[c],'held_back':[],'near':[]}
  base.update(over);return base
 def run_with(self,payload,quotes=None):
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'t.db')
   if payload is not None:s.set_meta('daily_scan',payload)
   text=scan_morning(s,self.NOW,quotes);s.close()
  return text
 def test_the_order_leads_with_the_limit_the_band_and_the_stop(self):
  text=self.run_with(self.scan())
  self.assertEqual(text.split('\n')[0],'台股盤前 10/14（三）08:55')
  self.assertIn('✅ 今天掛單 1 檔',text)
  self.assertIn('台積電 2330 ── 掛 960（收盤 1000，低 4.0%）',text)
  self.assertIn('先看布林上緣 1060（+10.4%），碰到後續抱、自高點回落 3% 賣；停損 864',text)
  self.assertLessEqual(len(text),1900)
 def test_a_weak_market_places_nothing_and_says_why(self):
  text=self.run_with(self.scan(market_open=False,candidates=[]))
  self.assertIn('0050 在 120 日線之下',text);self.assertNotIn('✅',text)
 def test_no_candidate_names_the_nearest_miss(self):
  text=self.run_with(self.scan(candidates=[],near=[{'symbol':'2317','name':'鴻海','why':'回檔量能 1.4 倍，賣壓未縮'}]))
  self.assertIn('今天沒有符合條件的標的',text);self.assertIn('鴻海 2317－回檔量能 1.4 倍',text)
 def test_a_missing_scan_is_not_read_as_no_trade(self):
  self.assertIn('這不是「不進場」的判斷',self.run_with(None))
 def test_a_trial_price_under_the_stop_cancels_the_order(self):
  text=self.run_with(self.scan(),{'2330':{'is_trial':True,'trial_price':850}})
  self.assertIn('試撮 850 已在停損之下，今天不買',text)

class HonestyTests(unittest.TestCase):
 def check(self,**q):
  from stock_radar.research_brief import quotes_checked
  now=datetime(2026,9,21,8,51,tzinfo=TW)
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'t.db');s.set_meta('morning_candidates',{'as_of':(now-timedelta(hours=2)).isoformat(),'symbols':['A']})
   s.set_meta('last_quote_sync',{'at':now.isoformat(),'source':'fugle','requested':30,'received':30,**q});r=quotes_checked(s,now);s.close()
  return r
 def test_only_a_real_fugle_check_today_counts(self):
  self.assertIsNone(self.check())
  self.assertIn('無法分辨試撮價',self.check(source='mis'))
  self.assertIn('10/30',self.check(received=10))
  self.assertIn('沒有執行',self.check(at='2026-09-18T08:50:00+08:00'))
 def test_fit_keeps_the_answer_and_drops_detail_first(self):
  from stock_radar.research_brief import fit,SITE
  lines=['台股盤前','✅ 今天可以買 1 檔','1. 神達 3706 ── 掛 80，最高 90','   風險：'+'很長'*900,'另有 20 檔通過規則篩選但尚未研究，不是推薦 → 網站',SITE]
  text=fit(lines);self.assertLessEqual(len(text),1900);self.assertIn('✅ 今天可以買 1 檔',text);self.assertIn('1. 神達 3706',text);self.assertNotIn('風險：',text)
