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
   text=build(s,now);self.assertLessEqual(len(text),1900);self.assertIn('不是今日掛單',text)
   text=morning(s,{'mode':'live','items':[]},now);self.assertLessEqual(len(text),1900);self.assertIn('沒有通過全部檢查',text)
   self.assertNotIn('提案買進上限',build(s,now+timedelta(days=31)))
   s.close()
