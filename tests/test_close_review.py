import tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store,dumps
from stock_radar import close_review as cr

DAY='2026-09-24';NOW=datetime(2026,9,24,18,5,tzinfo=TW)

class CloseReviewTests(unittest.TestCase):
    """Grading the day must count a missed run as clearly as a bad fill (STOCK_AGENT.md 9)."""

    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.s=Store(Path(self.dir.name)/'t.db')
        self.s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{}})
        prices={'FILL':[100,101,95,98,5000],   # touched a 96 limit, closed below it
                'MISS':[100,106,99,105,5000],  # never reached the 90 limit but ran
                'WAIT':[50,56,50,55,5000],     # no-chase name that ran 10%
                'QUIET':[40,40.5,39.5,40,5000]}
        with self.s.transaction():
            self.s.db.execute('INSERT INTO market_days VALUES(?,?,?)',(DAY,'prices',dumps(prices)))
            for sym,suggested in (('FILL',96.0),('MISS',90.0),('WAIT',None),('QUIET',None)):
                rec={'day':DAY,'symbol':sym,'name':sym,'status':'buy' if suggested else 'avoid',
                     'suggested':suggested,'reason':'r','recorded_at':NOW.isoformat()}
                self.s.db.execute('INSERT INTO recommendations VALUES(?,?,?,?)',(f'{DAY}:{sym}',DAY,sym,dumps(rec)))

    def tearDown(self):self.s.close();self.dir.cleanup()

    def test_counts_fills_misses_and_wait_quality(self):
        summary=cr.review(self.s,DAY)
        self.assertEqual((summary['picks'],summary['filled'],summary['unfilled']),(2,1,1))
        self.assertAlmostEqual(summary['filled_avg_pct'],round((98/96-1)*100,2))
        self.assertEqual(summary['waits'],2)
        self.assertEqual([x['symbol'] for x in summary['missed']],['WAIT'])
        self.assertEqual(summary['wait_ok'],1)

    def test_text_states_both_sides_and_the_caveat(self):
        body=cr.text(cr.review(self.s,DAY))
        self.assertIn('觸及掛價 1 檔',body)
        self.assertIn('未觸及 1 檔',body)
        self.assertIn('錯過',body)
        self.assertIn('當日漲跌不代表這些判斷長期正確',body)

    def test_a_day_without_bars_reports_no_data_and_sends_nothing(self):
        self.assertIsNone(cr.review(self.s,'2026-09-23'))
        out=cr.run(self.s,NOW+timedelta(days=1),send=False)
        self.assertEqual(out['status'],'no_data')

    def test_run_stores_a_summary_for_later_reading(self):
        cr.run(self.s,NOW,send=False)
        stored=self.s.meta('close_review')
        self.assertEqual(stored['day'],DAY);self.assertEqual(stored['missed'],['WAIT'])
