import tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar import intraday, paper

NOW=datetime(2026,9,29,10,0,tzinfo=TW)


class IntradayTests(unittest.TestCase):
    """It watches the rules' own orders and stops, and an unchanged situation stays silent."""

    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.s=Store(Path(self.dir.name)/'t.db')
        self.s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{}})
        self.s.set_meta('paper_runner',{'last_day':'2026-09-28','orders':[
            {'symbol':'3706','name':'神達','limit':79.0,'target':88.0,'stop':71.1,'placed':'2026-09-28','left':5}]})
        book=paper.Book(100000,started_day='2026-09-01')
        book.buy('2330','台積電','stock',10,1000,'2026-09-20','2026-09-20T13:30:00+08:00',plan={'stop':900.0})
        book.save(self.s)

    def tearDown(self):self.s.close();self.dir.cleanup()

    def quote(self,**prices):return lambda:{k:{'symbol':k,'price':v} for k,v in prices.items()}

    def test_a_reached_limit_is_reported_once_a_day(self):
        out=intraday.run(self.s,NOW,fetch=self.quote(**{'3706':78.5,'2330':1010}))
        self.assertEqual(out['status'],'built')
        self.assertIn('神達 3706｜掛單價到了（現價 78.5，掛單 79）',out['text'])
        self.assertEqual(intraday.run(self.s,NOW+timedelta(hours=1),fetch=self.quote(**{'3706':78}))['status'],'quiet')

    def test_a_stop_breaking_is_reported(self):
        out=intraday.run(self.s,NOW,fetch=self.quote(**{'3706':85,'2330':890}))
        self.assertIn('台積電 2330｜跌破停損（現價 890，停損 900）',out['text'])

    def test_prices_above_every_level_say_nothing(self):
        self.assertEqual(intraday.run(self.s,NOW,fetch=self.quote(**{'3706':85,'2330':1010}))['status'],'quiet')

    def test_a_non_trading_day_never_runs(self):
        self.s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{'2026-09-29':'假日'}})
        self.assertEqual(intraday.run(self.s,NOW,fetch=self.quote(**{'3706':78}))['status'],'closed')

    def test_nothing_held_and_nothing_ordered_is_not_an_error(self):
        self.s.set_meta('paper_runner',{'orders':[]});paper.Book(100000,started_day='2026-09-01').save(self.s)
        self.assertEqual(intraday.run(self.s,NOW,fetch=self.quote())['status'],'no_candidates')


if __name__=='__main__':
    unittest.main()
