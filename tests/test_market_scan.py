import tempfile,unittest
from datetime import date,datetime,timedelta
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store,dumps
from stock_radar import market_scan as ms,market

def days(n,end=date(2026,9,18)):
    out=[];d=end
    while len(out)<n:
        if d.weekday()<5:out.append(str(d))
        d-=timedelta(days=1)
    return sorted(out)

class TechnicalTests(unittest.TestCase):
 def test_structure_from_market_rows_and_stale_symbol_refused(self):
  ds=days(60);prices=[(d,{'A':[10,11+k%3,9-k%2,10+k*.01,2000]}) for k,d in enumerate(ds)];flows=[(d,{'A':[-100,-50]}) for d in ds[-5:]]
  t=ms.technical('A',prices,flows);self.assertEqual(t['as_of'],ds[-1]);self.assertEqual(t['low_60d'],8);self.assertEqual(t['foreign_5d_lots'],-500)
  prices[-1][1].pop('A');self.assertIsNone(ms.technical('A',prices,flows))

class EtfTests(unittest.TestCase):
 def test_price_position_bands_are_ordered_and_explained(self):
  ds=days(200);prices=[(d,{'0050':[1,1,1,100+k*.1,5000]}) for k,d in enumerate(ds)]
  v,why=ms.etf_valuation({'symbol':'0050','kind':'etf_equity'},{'close':110,'trade_date':ds[-1]},prices)
  self.assertIsNone(why);self.assertLess(v['sweet'],v['add']);self.assertLess(v['add'],v['buy']);self.assertEqual(v['method'],'etf_band')
  self.assertIn('不是估值',v['reason']);self.assertIn('淨值',v['reason'])
 def test_young_etf_is_not_valued(self):
  ds=days(50);prices=[(d,{'00999':[1,1,1,10,5000]}) for d in ds]
  self.assertIsNone(ms.etf_valuation({'symbol':'00999','kind':'etf_equity'},{'close':10,'trade_date':ds[-1]},prices)[0])

class ScanTests(unittest.TestCase):
 def test_liquidity_is_the_first_gate_and_etfs_are_not_scanned(self):
  d=tempfile.TemporaryDirectory();s=Store(Path(d.name)/'t.db');now=datetime(2026,9,18,17,tzinfo=TW)
  s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
  syms={'LIQ':'stock','THIN':'stock','0050':'etf_equity','00632R':'etf_other'}
  s.upsert_instruments([{'symbol':k,'name':k,'kind':v,'market':'TSE'} for k,v in syms.items()])
  ds=days(200)
  for k,day in enumerate(ds):
   rows={'LIQ':[100,101,99,100,3000],'THIN':[100,101,99,100,200],'0050':[1,1,1,100+k*.1,9000],'00632R':[1,1,1,20-k*.01,50000]}
   s.db.execute('INSERT INTO market_days VALUES(?,?,?)',(day,'prices',dumps(rows)))
   s.db.execute('INSERT INTO market_days VALUES(?,?,?)',(day,'ratios',dumps({'LIQ':[20,2,1],'THIN':[20,2,1]})))
  for day in ds[-5:]:s.db.execute('INSERT INTO market_days VALUES(?,?,?)',(day,'flows',dumps({'LIQ':[1,1]})))
  s.db.commit()
  for k in syms:s.set_fact(k,'public_close',{'close':100 if k!='0050' else 119.9,'trade_date':ds[-1]})
  r=ms.scan(s,now)
  # Ordinary shares only (2026-10-02). ETFs are a basket of the same stocks, so an ETF beside its
  # constituents is one bet counted twice; they get their own strategy or none. Here that leaves LIQ
  # and THIN as the listed universe, and only LIQ clears the liquidity gate.
  self.assertEqual((r['listed'],r['liquid'],r['published']),(2,1,1))
  self.assertEqual(s.active('LIQ')['method'],'pe_band')
  self.assertIsNone(s.active('0050'));self.assertIsNone(s.active('00632R'))
  self.assertIsNone(s.active('THIN'))
  self.assertEqual(s.meta('liquidity')['lots']['THIN'],200.0);self.assertIsNotNone(s.fact('LIQ','technical'))
  s.close();d.cleanup()

class DiscountTests(unittest.TestCase):
 def test_etf_discount_falls_back_to_recorded_price(self):
  self.assertAlmostEqual(ms.discount({'fair':100,'inputs':{'price_today':95}},None),.05)

class CandidateTests(unittest.TestCase):
 def test_etfs_keep_reserved_slots_even_when_stocks_look_cheaper(self):
  from unittest import mock
  insts=[{'symbol':f'S{k}','kind':'stock'} for k in range(40)]+[{'symbol':f'E{k}','kind':'etf_equity'} for k in range(8)]
  class S:
   def __init__(self):self.m={}
   def active(self,sym):return {'inputs':{'pe_today':10,'fair_pe':20}} if sym[0]=='S' else {'fair':100,'inputs':{}}
   def fact(self,sym,cat):return {'close':97}
   def instruments(self):return insts
   def set_meta(self,k,v):self.m[k]=v
  s=S()
  with mock.patch('stock_radar.buy_plan.store_plan',return_value={'status':'conditional'}):out=ms.candidates(s,insts,datetime(2026,9,18,17,tzinfo=TW))
  self.assertEqual(len(out),30);self.assertEqual(sum(x[0]=='E' for x in out),6);self.assertEqual(out[20],'E0')

if __name__=='__main__':unittest.main()
