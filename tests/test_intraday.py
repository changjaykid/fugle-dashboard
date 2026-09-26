import tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar import intraday

NOW=datetime(2026,9,29,10,0,tzinfo=TW)

def valuation(now):
    return {'sweet':60.0,'add':70.0,'buy':82.8,'fair':97.5,'method':'pe_band','reason':'r','thesis':'t',
            'inputs':{'pe_today':14.38,'fair_pe':17.42},'as_of':(now-timedelta(hours=4)).isoformat(),
            'valid_until':(now+timedelta(days=3)).isoformat(),'evidence_reviewed':True,'review_mode':'rule-engine-v1',
            'sources':[{'url':'https://example.com','as_of':(now-timedelta(hours=4)).isoformat()}]}

class IntradayTests(unittest.TestCase):
    """The morning call is not the whole day, but an unchanged situation must stay silent."""

    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.s=Store(Path(self.dir.name)/'t.db')
        self.s.set_meta('analysis_authority',{'decision':'user-confirmed-2026-09-14','autonomous_research':True})
        self.s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{}})
        self.s.set_meta('morning_candidates',{'symbols':['3706']})
        self.s.upsert_instruments([{'symbol':'3706','name':'神達','kind':'stock','market':'TSE'}])
        self.s.publish_rule_valuation(self.s.propose('3706',valuation(NOW),now=NOW),now=NOW)
        self.s.set_fact('3706','public_close',{'close':80.5,'trade_date':'2026-09-28'})
        self.s.set_fact('3706','technical',{'as_of':'2026-09-28','flow_as_of':'2026-09-28','support':77.6,
                                            'supports':[77.6,79.0],'low_60d':77.6,'high_60d':95.8,'ma20':82.0,
                                            'resistance':95.8,'foreign_5d_lots':1.0,'trust_5d_lots':1.0,
                                            'requires_stabilization':False})
        self.s.set_fact('3706','risk',{'cleared':True,'events':[],'checked_at':(NOW-timedelta(hours=4)).isoformat()})

    def tearDown(self):self.s.close();self.dir.cleanup()

    def quote(self,price):return lambda:{'3706':{'symbol':'3706','price':price}}

    def test_reaching_the_entry_is_reported_once_a_day(self):
        out=intraday.run(self.s,NOW,fetch=self.quote(79.0))
        self.assertEqual(out['events'],1);self.assertIn('觸及承接參考價',out['text'])
        self.assertIn('不是新的掛價建議',out['text'])
        self.assertEqual(intraday.run(self.s,NOW+timedelta(hours=1),fetch=self.quote(79.0))['status'],'quiet')

    def test_losing_support_is_reported_and_not_repeated(self):
        out=intraday.run(self.s,NOW,fetch=self.quote(70.0))
        self.assertEqual(out['events'],1);self.assertIn('跌破支撐',out['text'])
        self.assertEqual(intraday.run(self.s,NOW+timedelta(minutes=5),fetch=self.quote(68.0))['status'],'quiet')

    def test_only_one_intraday_message_a_day(self):
        first=intraday.run(self.s,NOW,fetch=self.quote(79.0))
        self.assertEqual(first['events'],1)
        self.assertEqual(intraday.run(self.s,NOW+timedelta(hours=2),fetch=self.quote(60.0))['status'],'quiet')

    def test_falling_back_into_range_alone_is_not_worth_a_message(self):
        self.s.set_fact('3706','public_close',{'close':95.0,'trade_date':'2026-09-28'})  # closed above the cap
        out=intraday.run(self.s,NOW,fetch=self.quote(82.0))  # back under buy_max, nothing broken
        self.assertEqual(out['status'],'quiet')

    def test_a_price_above_the_plan_says_nothing(self):
        self.assertEqual(intraday.run(self.s,NOW,fetch=self.quote(120.0))['status'],'quiet')

    def test_a_non_trading_day_never_runs(self):
        self.s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{'2026-09-29':'測試休市'}})
        self.assertEqual(intraday.run(self.s,NOW,fetch=self.quote(79.0))['status'],'closed')
