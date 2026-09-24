import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timedelta
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar.research_brief import parse_closes,install,build,morning

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
   text=morning(s,{'mode':'live','items':[]},now);self.assertIn('⚠️ 無法確認',text);self.assertNotIn('今天不進場',text)
   s.set_meta('morning_candidates',{'as_of':(now-timedelta(hours=2)).isoformat(),'symbols':[]});s.set_meta('last_quote_sync',{'at':now.isoformat(),'source':'fugle','requested':10,'received':10})
   text=morning(s,{'mode':'live','items':[]},now);self.assertLessEqual(len(text),1900);self.assertIn('⛔ 今天不進場',text)
   self.assertNotIn('提案買進上限',build(s,now+timedelta(days=31)))
   s.close()

class MorningTests(unittest.TestCase):
 def test_go_message_puts_answer_first_with_price_cap_reason_and_clock_time(self):
  now=datetime(2026,9,21,8,50,tzinfo=TW)
  item={'symbol':'3706','name':'神達','kind':'stock','watched':True,'valuation':{'buy':82.8,'inputs':{'pe_today':14.38,'fair_pe':17.42},'valid_until':(now+timedelta(days=2)).isoformat(),'evidence_reviewed':True},
        'signal':{'status':'buy','suggested':77.6,'valid_until':(now+timedelta(minutes=2)).isoformat(),'calculated_at':now.isoformat()},'quote':{'as_of':now.isoformat(),'trade_date':'2026-09-21','is_trial':True,'trial_price':78}}
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'t.db');s.set_fact('3706','research_detail',{'thesis':'看好理由','risks':['主要風險'],'as_of':(now-timedelta(days=1)).isoformat(),'valid_until':(now+timedelta(days=10)).isoformat()});text=morning(s,{'mode':'live','items':[item]},now);s.close()
  lines=text.split('\n')
  self.assertEqual(lines[0],'台股盤前 9/21（一）08:50');self.assertTrue(lines[2].startswith('✅ 今天可以買 1 檔'))
  self.assertIn('神達 3706 ── 掛 77.6，最高 82.8',text);self.assertIn('本益比 14.38，比一年常態 17.4 便宜 17%',text)
  self.assertIn('09:00 開盤前參考',text);self.assertNotIn('T08:5',text);self.assertIn('風險：主要風險',text)

class MarketMorningTests(unittest.TestCase):
 def test_cheapest_five_stocks_then_etfs_in_their_own_section(self):
  now=datetime(2026,9,21,8,50,tzinfo=TW)
  def item(sym,kind,pe_today,fair=20.0):
   inputs={'pe_today':pe_today,'fair_pe':fair} if kind=='stock' else {'median':100,'p25':95,'p10':90,'price_today':94}
   return {'symbol':sym,'name':'名'+sym,'kind':kind,'valuation':{'buy':120,'method':'pe_band' if kind=='stock' else 'etf_band','inputs':inputs,'valid_until':(now+timedelta(days=2)).isoformat(),'evidence_reviewed':True},
           'signal':{'status':'buy','suggested':94.0,'valid_until':(now+timedelta(minutes=2)).isoformat(),'calculated_at':now.isoformat()},'quote':{'as_of':now.isoformat(),'trade_date':'2026-09-21','is_trial':True,'trial_price':95}}
  items=[item(f'S{k}','stock',20-k) for k in range(7)]+[item('0050','etf_equity',None),item('U','stock',1)]
  with tempfile.TemporaryDirectory() as d:
   s=Store(Path(d)/'t.db')
   for k in range(7):s.set_fact(f'S{k}','research_detail',{'thesis':'看好理由','risks':['主要風險'],'as_of':(now-timedelta(days=1)).isoformat(),'valid_until':(now+timedelta(days=10)).isoformat()})
   s.set_meta('morning_candidates',{'as_of':now.isoformat(),'symbols':['S0']});s.set_meta('last_quote_sync',{'at':now.isoformat(),'source':'fugle','requested':9,'received':9})
   text=morning(s,{'mode':'live','items':items},now);s.close()
  self.assertIn('✅ 今天可以買 6 檔',text);self.assertIn('6. 名0050 0050（ETF）',text);self.assertEqual(text.count('── 掛 94，最高 120'),6)
  self.assertIn('另有 1 檔通過規則篩選但尚未研究，不是推薦',text);self.assertNotIn('名U U ──',text)
  self.assertLess(text.index('名S6'),text.index('名S5'));self.assertNotIn('名S0 ',text);self.assertNotIn('名S1 ',text)
  self.assertIn('最便宜 25%',text);self.assertLessEqual(len(text),1900)

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
