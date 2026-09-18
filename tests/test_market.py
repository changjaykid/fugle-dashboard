import tempfile,unittest
from datetime import date,datetime
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar import market

def prices_body(d,rows):return {'stat':'OK','date':d.strftime('%Y%m%d'),'tables':[{'title':'x'},{'title':'每日收盤行情(全部)','data':rows}]}
def row(sym,close,shares):return [sym,'n',f"{shares:,}",'1','1','10','11','9',str(close),'+','0','0','0','0','0','0']
class Http:
 def __init__(self,days):self.days=days;self.calls=[]
 def get(self,url,params,**k):
  ds=params['date'];self.calls.append((url.rsplit('/',1)[1],ds));d=date(int(ds[:4]),int(ds[4:6]),int(ds[6:]))
  class R:
   def __init__(s,b):s.b=b
   def json(s):return s.b
  if ds not in self.days:return R({'stat':'很抱歉，沒有符合條件的資料!'})
  if 'MI_INDEX' in url:return R(prices_body(d,[row('2330',100,2_000_000),row('9999',5,1000)]))
  if 'BWIBBU' in url:return R({'stat':'OK','date':ds,'data':[['2330','台積電','100','1.0',114,'20.5','5.1','115/2'],['9999','x','5','0',114,'-','0.8','115/2']]})
  return R({'stat':'OK','date':ds,'data':[['2330','台積電','0','0','1,000,000','0','0','0','0','0','-500,000']]})

class MarketTests(unittest.TestCase):
 def setUp(self):
  self.dir=tempfile.TemporaryDirectory();self.s=Store(Path(self.dir.name)/'t.db')
  self.s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{'2026-09-16':'休市'}})
 def tearDown(self):self.s.close();self.dir.cleanup()
 def test_parse_units_and_blanks(self):
  p=market.parse('prices',prices_body(date(2026,9,17),[row('2330',100,2_000_000)]),date(2026,9,17));self.assertEqual(p['2330'],[10,11,9,100,2000])
  r=market.parse('ratios',{'stat':'OK','date':'20260917','data':[['9999','x','5','0',114,'-','0.8','']]},date(2026,9,17));self.assertEqual(r['9999'][:2],[None,0.8])
  self.assertIsNone(market.parse('prices',{'stat':'很抱歉'},date(2026,9,17)))
  with self.assertRaises(ValueError):market.parse('ratios',{'stat':'OK','date':'20260916','data':[['1']]},date(2026,9,17))
 def test_sync_skips_calendar_holidays_and_keeps_n(self):
  http=Http({'20260918','20260917','20260915'})
  market.sync(self.s,datetime(2026,9,18,17,tzinfo=TW),http,pause=0,keep={'prices':3,'ratios':2,'flows':1})
  self.assertEqual([d for d,_ in market.load(self.s,'prices')],['2026-09-15','2026-09-17','2026-09-18'])
  self.assertEqual(len(market.load(self.s,'ratios')),2);self.assertNotIn(('MI_INDEX','20260916'),http.calls)
  http.calls.clear();market.sync(self.s,datetime(2026,9,18,18,tzinfo=TW),http,pause=0,keep={'prices':3,'ratios':2,'flows':1});self.assertEqual(http.calls,[])
 def test_missing_report_for_a_trading_day_is_retried_not_marked_closed(self):
  http=Http({'20260917'});failed=market.sync(self.s,datetime(2026,9,18,17,tzinfo=TW),http,pause=0,keep={'prices':1,'ratios':0,'flows':0})
  self.assertIn('prices:2026-09-18',failed);self.assertNotIn('2026-09-18',market.stored(self.s,'closed'))
 def test_liquidity_counts_missing_sessions_as_zero(self):
  prices=[('d1',{'A':[1,1,1,1,3000]}),('d2',{'A':[1,1,1,1,1000],'B':[1,1,1,1,500]})]
  self.assertEqual(market.liquidity(prices,days=2),{'A':2000.0,'B':250.0})

if __name__=='__main__':unittest.main()
