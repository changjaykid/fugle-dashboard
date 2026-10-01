import tempfile,unittest
from datetime import date,datetime,timedelta
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar import valuation_engine as ve

DAY=date(2026,9,18)
def roc(d):return f"{d.year-1911}年{d.month:02d}月{d.day:02d}日"
def history(pe_today='25',pb_today='2.5',n=200,pe='20',pb='2'):
    rows=[[roc(DAY-timedelta(days=k)),'1',114,pe,pb,'115/2'] for k in range(1,n+1)]+[[roc(DAY),'1',114,pe_today,pb_today,'115/2']]
    return {'months':{'all':rows}}
def rev(ytd=1.10,month=40.0):return {'revenue':{'period_roc_ym':'11508','cumulative_revenue':ytd*100,'cumulative_revenue_last_year':100,'yoy_pct':month}}
CLOSE={'close':100,'trade_date':str(DAY)};INST={'symbol':'T','kind':'stock'}

class ComputeTests(unittest.TestCase):
 def test_pe_band_is_trailing_eps_at_median_pe(self):
  v,why=ve.compute(INST,CLOSE,rev(),history());self.assertIsNone(why)
  self.assertEqual(v['method'],'pe_band');self.assertAlmostEqual(v['inputs']['ttm_eps'],4)
  self.assertEqual(v['fair'],80.0);self.assertEqual((v['sweet'],v['add'],v['buy']),(52.0,60.0,68.0))
 def test_revenue_growth_is_context_not_price(self):
  a,_=ve.compute(INST,CLOSE,rev(ytd=2.0,month=170.0),history());b,_=ve.compute(INST,CLOSE,{},history())
  self.assertEqual(a['fair'],b['fair']);self.assertIn('營收年增 今年累計 +100.0%',a['reason']);self.assertIn('成長不另外加價',a['reason'])
 def test_stock_at_its_normal_multiple_is_not_a_buy(self):
  v,_=ve.compute(INST,CLOSE,rev(),history(pe_today='20'));self.assertLess(v['buy'],CLOSE['close'])
 def test_very_high_normal_pe_gets_no_valuation_rather_than_a_book_one(self):
  # Book is not the escape hatch for a stock the market prices above MAX_FAIR_PE: its P/B band is the
  # same price history at a different multiple. 8033 was recommended at 11.35x book on a median P/E of 207.
  v,why=ve.compute(INST,CLOSE,rev(),history(pe='80',pe_today='70'))
  self.assertIsNone(v);self.assertIn('本益比中位數 80 倍',why);self.assertIn('兩種方法都不適用',why)
 def test_an_inapplicable_method_retires_the_price_it_already_published(self):
  # Refusing to recompute is not enough: the old buy price stays live for its four-day validity, so
  # 8033 and 3715 were still being recommended two days after the engine decided it could not value
  # them. A refusal about the company retires the price; a refusal about today's data does not.
  from stock_radar.valuation_engine import Inapplicable,publish_all
  now=datetime(2026,9,21,10,tzinfo=TW)
  with tempfile.TemporaryDirectory() as td:
   s=Store(Path(td)/'db');inst={'symbol':'T','kind':'stock','name':'測試'};s.upsert_instruments([inst])
   s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
   v={'sweet':70,'add':80,'buy':90,'method':'pb_band','thesis':'t','reason':'r','review_mode':'rule-engine-v1',
      'as_of':now.isoformat(),'valid_until':(now+timedelta(days=4)).isoformat(),'evidence_reviewed':True,
      'sources':[{'url':'https://example.com','as_of':now.isoformat()}]}
   s.publish_rule_valuation(s.propose('T',v,now=now),now=now)
   self.assertIsNotNone(s.active('T'))
   # A late fetch leaves the published price alone.
   self.assertEqual(publish_all(s,now,[(inst,None,'缺少本益比資料')])[0][1],'skip')
   self.assertIsNotNone(s.active('T'))
   # A method that does not fit the company takes it out of service.
   row=publish_all(s,now,[(inst,None,Inapplicable('本益比中位數 207 倍，兩種方法都不適用'))])[0]
   self.assertEqual(row[1],'retired')
   self.assertIsNone(s.active('T'))
   self.assertIn('retire_valuation',[r[0] for r in s.db.execute('SELECT action FROM audit')])
   s.close()
 def test_thin_earnings_still_uses_book_for_financials_and_cyclicals(self):
  # The policy route is unaffected: these are valued on book by design, whatever their P/E does.
  v,_=ve.compute({**INST,'industry_code':'金融保險業'},CLOSE,rev(),history(pe='80',pe_today='70'))
  self.assertEqual(v['method'],'pb_band')
 def test_book_band_above_its_ceiling_gets_no_valuation(self):
  # A median of 6x book (above MAX_FAIR_PB=5) no longer explains the price, so there is no buy price.
  v,why=ve.compute(INST,CLOSE,rev(),history(pe_today='-',pb='6',pb_today='5'))
  self.assertIsNone(v);self.assertIn('股價淨值比中位數 6.0 倍',why);self.assertIn('淨值法不適用',why)
 def test_book_band_at_its_ceiling_is_still_published(self):
  v,_=ve.compute(INST,CLOSE,rev(),history(pe_today='-',pb='5',pb_today='4'))
  self.assertEqual(v['method'],'pb_band');self.assertIn('此為淨值基準，不是獲利基準',v['reason'])
 def test_financials_and_cyclicals_use_book_value(self):
  for ind,word in (('金融保險業','P/B'),('航運業','景氣循環股')):
   v,_=ve.compute({**INST,'industry_code':ind},CLOSE,rev(),history());self.assertEqual(v['method'],'pb_band');self.assertIn(word,v['reason'])
 def test_loss_maker_falls_back_to_book_value(self):
  v,_=ve.compute(INST,CLOSE,rev(),history(pe_today='-'));self.assertEqual(v['method'],'pb_band');self.assertEqual(v['fair'],80.0)
 def test_insufficient_history_or_missing_day_refuses(self):
  self.assertIsNone(ve.compute(INST,CLOSE,rev(),history(n=50))[0])
  self.assertIsNone(ve.compute(INST,{'close':100,'trade_date':'2026-09-19'},rev(),history())[0])
  self.assertIsNone(ve.compute(INST,None,rev(),history())[0])

class Fake:
 def __init__(self,body):self.body=body
 def get(self,*a,**k):return self
 def json(self):return self.body
class FetchTests(unittest.TestCase):
 def ok(self):return {'stat':'OK','date':'20260901','title':'115年09月 台積電 個股日本益比','data':[['115年09月01日','1',114,'20','2','115/2']]}
 def test_accepts_matching_response(self):self.assertEqual(len(ve.fetch_month('2330','台積電','202609',Fake(self.ok()))),1)
 def test_rejects_other_stock_month_or_rows(self):
  for change in (lambda b:b.update(title='115年09月 鴻海'),lambda b:b.update(date='20260801'),lambda b:b.update(data=[['115年08月29日','1',114,'20','2','115/2']]),lambda b:b.update(stat='很抱歉')):
   b=self.ok();change(b)
   with self.assertRaises(ValueError):ve.fetch_month('2330','台積電','202609',Fake(b))

class PublishTests(unittest.TestCase):
 def setUp(self):
  self.dir=tempfile.TemporaryDirectory();self.s=Store(Path(self.dir.name)/'t.db');self.now=datetime(2026,9,18,17,tzinfo=TW)
  self.s.upsert_instruments([{'symbol':'T','name':'測試','kind':'stock','watched':True},{'symbol':'U','name':'未關注','kind':'stock'}])
  self.s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
  self.s.set_fact('T','public_close',CLOSE);self.s.set_fact('T','financials',{**rev(),'fetched_at':(self.now-timedelta(hours=1)).isoformat()});self.s.set_fact('T','ratio_history',history())
 def tearDown(self):self.s.close();self.dir.cleanup()
 def test_publishes_once_per_day_and_skips_incomplete(self):
  self.assertEqual(ve.publish(self.s,self.now),[('T','published',68.0)])
  a=self.s.active('T');self.assertEqual((a['method'],a['buy'],a['review_mode']),('pe_band',68.0,'rule-engine-v1'))
  self.assertNotIn('analysis_review',a)
  with self.assertRaises(ValueError):
   bad=dict(a);bad.pop('id');bad['analysis_review']={'financials':True};self.s.publish_rule_valuation(self.s.propose('T',bad,now=self.now),now=self.now)
  self.assertEqual(ve.publish(self.s,self.now+timedelta(hours=1))[0][1],'same')
  self.s.set_fact('T','ratio_history',{'months':{}});self.assertEqual(ve.publish(self.s,self.now+timedelta(days=1))[0][1],'skip')
  self.assertEqual(self.s.active('T')['buy'],68.0)

class SyncTests(unittest.TestCase):
 def test_month_is_refetched_until_fetched_after_it_ended(self):
  d=tempfile.TemporaryDirectory();s=Store(Path(d.name)/'t.db');calls=[]
  s.upsert_instruments([{'symbol':'T','name':'測試','kind':'stock','watched':True}])
  class Http:
   def get(self,url,params,**k):
    calls.append(params['date'][:6]);ym=params['date'][:6]
    return Fake({'stat':'OK','date':params['date'],'title':'測試','data':[[f"{int(ym[:4])-1911}年{ym[4:]}月01日",'1',114,'20','2','115/2']]})
  ve.sync_history(s,datetime(2026,9,18,tzinfo=TW),Http(),pause=0);self.assertEqual(len(calls),13)
  calls.clear();ve.sync_history(s,datetime(2026,9,19,tzinfo=TW),Http(),pause=0);self.assertEqual(calls,['202609'])
  calls.clear();ve.sync_history(s,datetime(2026,10,1,tzinfo=TW),Http(),pause=0);self.assertEqual(calls,['202609','202610'])
  s.close();d.cleanup()

if __name__=='__main__':unittest.main()
