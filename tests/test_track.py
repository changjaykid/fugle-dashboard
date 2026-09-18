import json,tempfile,unittest
from datetime import date,timedelta
from pathlib import Path
from stock_radar.store import Store
from stock_radar import track

def bar(d,low,close):return [f"{d.year-1911}/{d.month:02d}/{d.day:02d}",'0','0',str(close),str(close),str(low),str(close)]
def radar(*items):return {'items':[{'watched':True,'symbol':s,'name':s,'signal':sig} for s,sig in items]+[{'watched':False,'symbol':'X','signal':{'status':'buy','suggested':1}}]}

class TrackTests(unittest.TestCase):
 def setUp(self):
  self.dir=tempfile.TemporaryDirectory();self.s=Store(Path(self.dir.name)/'t.db');self.d0=date(2026,9,14)
  days=[self.d0+timedelta(days=k) for k in range(8)]
  self.s.set_fact('A','price_bars',{'rows':[bar(d,95 if d==self.d0 else 100,100+k) for k,d in enumerate(days)]})
  self.s.set_fact('B','price_bars',{'rows':[bar(d,100,100-k) for k,d in enumerate(days)]})
 def tearDown(self):self.s.close();self.dir.cleanup()
 def rec(self,sym):return next(r for r in track.rows(self.s) if r['symbol']==sym)

 def test_records_only_watched_and_later_snapshot_wins(self):
  track.record(self.s,radar(('A',{'status':'buy','suggested':97})),'2026-09-14','2026-09-14T08:50:00+08:00')
  track.record(self.s,radar(('A',{'status':'buy','suggested':96})),'2026-09-14','2026-09-14T08:55:00+08:00')
  track.record(self.s,radar(('A',{'status':'buy','suggested':90})),'2026-09-14','2026-09-14T08:40:00+08:00')
  self.assertEqual([r['symbol'] for r in track.rows(self.s)],['A']);self.assertEqual(self.rec('A')['suggested'],96)

 def test_fill_uses_day_low_and_returns_start_from_limit_price(self):
  track.record(self.s,radar(('A',{'status':'buy','suggested':96}),('B',{'status':'avoid','suggested':50})),'2026-09-14','2026-09-14T08:50:00+08:00')
  track.evaluate(self.s);a,b=self.rec('A'),self.rec('B')
  self.assertTrue(a['filled']);self.assertEqual(a['returns']['5'],round((105/96-1)*100,2));self.assertNotIn('20',a['returns'])
  self.assertIsNone(b['suggested']);self.assertNotIn('filled',b);self.assertEqual(b['returns']['5'],-5.0)

 def test_unfilled_limit_is_tracked_from_close(self):
  track.record(self.s,radar(('A',{'status':'sweet','suggested':90})),'2026-09-14','2026-09-14T08:50:00+08:00')
  track.evaluate(self.s);a=self.rec('A')
  self.assertFalse(a['filled']);self.assertEqual(a['returns']['5'],5.0)
  st=track.stats(self.s);self.assertEqual((st['actionable'],st['filled'],st['unfilled']),(1,0,1))

 def test_outcomes_survive_rolling_bar_cache(self):
  track.record(self.s,radar(('A',{'status':'buy','suggested':96})),'2026-09-14','2026-09-14T08:50:00+08:00')
  track.evaluate(self.s);self.s.set_fact('A','price_bars',{'rows':[]});track.evaluate(self.s)
  self.assertTrue(self.rec('A')['filled']);self.assertIn('5',self.rec('A')['returns'])

 def test_summary_is_honest_before_horizon(self):
  self.assertIn('尚無紀錄',track.summary(self.s))
  self.s.set_fact('A','price_bars',{'rows':[bar(self.d0,95,100)]})
  track.record(self.s,radar(('A',{'status':'buy','suggested':96})),'2026-09-14','2026-09-14T08:50:00+08:00');track.evaluate(self.s)
  self.assertIn('尚未滿 5 個交易日',track.summary(self.s))

 def test_backfill_ignores_after_open_snapshots(self):
  snap=Path(self.dir.name)/'snaps';snap.mkdir()
  (snap/'a.json').write_text(json.dumps({**radar(('A',{'status':'buy','suggested':96})),'generated_at':'2026-09-14T08:50:00+08:00'}))
  (snap/'b.json').write_text(json.dumps({**radar(('A',{'status':'buy','suggested':50})),'generated_at':'2026-09-14T10:00:00+08:00'}))
  track.backfill(self.s,snap);self.assertEqual(self.rec('A')['suggested'],96)

if __name__=='__main__':unittest.main()
