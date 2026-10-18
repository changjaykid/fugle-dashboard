import json,tempfile,unittest
from datetime import datetime
from pathlib import Path
from stock_radar.domain import TW
from stock_radar.store import Store
from stock_radar import close_review as cr

DAY='2026-09-24';NOW=datetime(2026,9,24,18,5,tzinfo=TW)


class CloseReviewTests(unittest.TestCase):
    """The 18:00 message is the account's day under swing-v1, never the retired valuation ledger."""

    def setUp(self):
        self.dir=tempfile.TemporaryDirectory();self.folder=Path(self.dir.name)
        self.s=Store(self.folder/'t.db')
        self.s.set_meta('trading_calendar',{'roc_years':[115],'closed_dates':{}})

    def tearDown(self):self.s.close();self.dir.cleanup()

    def write(self,day,text='模擬帳戶 2026-09-24（起始 100,000 元）\n淨值 100,052 元'):
        (self.folder/'paper-latest.json').write_text(json.dumps({'day':day,'status':'ok','text':text}))

    def test_the_account_is_the_message(self):
        self.write(DAY)
        out=cr.run(self.s,NOW,folder=self.folder)
        self.assertIn('台股收盤 2026-09-24',out['text'])
        self.assertIn('淨值 100,052 元',out['text'])

    def test_the_retired_recommendation_wording_is_gone(self):
        self.write(DAY)
        body=cr.run(self.s,NOW,folder=self.folder)['text']
        self.assertNotIn('可掛價建議',body)
        self.assertNotIn('未列入建議',body)

    def test_an_account_not_written_for_the_day_is_said_not_invented(self):
        self.write('2026-09-23')
        out=cr.run(self.s,NOW,folder=self.folder)
        self.assertIn('結算尚未完成',out['text'])
        self.assertFalse(self.s.meta('close_review')['account'])
