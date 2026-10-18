import tempfile,unittest
from datetime import datetime
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar import paper, weekend

NOW=datetime(2026,10,17,10,0,tzinfo=TW)   # a Saturday


class WeekendTests(unittest.TestCase):
    """Both weekend messages describe swing-v1's account and orders; the valuation words are gone."""

    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.s=Store(Path(self.dir.name)/'t.db')
        book=paper.Book(100000,started_day='2026-10-02')
        book.buy('3231','緯創','stock',27,179.5,'2026-10-15','2026-10-15T13:30:00+08:00',
                 plan={'stop':161.5,'target':191.5})
        book.positions['3231']['mark']=176.0
        book.save(self.s)
        self.s.set_meta('paper_runner',{'orders':[{'symbol':'2617','name':'台航','limit':31.05,'stop':27.9,
                        'target':33.8,'placed':'2026-10-13','left':3,'why':'回檔'}]})
        self.s.set_meta('daily_scan',{'market_open':True})

    def tearDown(self):self.s.close();self.dir.cleanup()

    def test_the_weekly_report_is_the_account_and_the_rule_record(self):
        text=weekend.build('weekly',NOW,self.s)
        self.assertIn('模擬帳戶淨值',text)
        self.assertIn('買 緯創 3231 27 股 @ 179.5',text)
        self.assertIn('緯創 3231｜27 股 成本 179.5 → 176',text)
        self.assertIn('選定時的回測',text)
        self.assertIn('0050 在 120 日均線之上',text)

    def test_the_outlook_lists_every_order_with_its_stop_and_target(self):
        text=weekend.build('outlook',NOW,self.s)
        self.assertIn('台航 2617｜掛 31.05｜停損 27.9｜目標先看 33.8｜剩 3 個交易日',text)
        self.assertIn('停損 161.5',text)

    def test_neither_uses_the_retired_valuation_words(self):
        for kind in ('weekly','outlook'):
            text=weekend.build(kind,NOW,self.s)
            for word in ('承接','甜甜價','便宜','本益比比'):
                self.assertNotIn(word,text)
