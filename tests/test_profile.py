"""What a company does, from its own MOPS filing, and the decision facts the page shows beside it."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from stock_radar import daily_scan, profile
from stock_radar.domain import TW
from stock_radar.store import Store

PAGE = ('<table><tr><th class="dColor">主要經營業務</th>\n<td class="lColor" colspan="3">'
        '研究開發生產、製造、銷售各種積體電路&nbsp;</td></tr><tr><th>公司成立日期</th><td>76/10/21</td></tr></table>')
NOW = datetime(2026, 10, 15, 18, 0, tzinfo=TW)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder)
        self.store = Store(folder / 'p.db'); self.addCleanup(self.store.close)
        self.store.set_meta('daily_scan', {'day': '2026-10-15', 'candidates': [
            {'symbol': '2379', 'name': '瑞昱', 'industry': '半導體業', 'limit': 702.0, 'close': 758.0}],
            'held_back': [], 'near': [{'symbol': '2317', 'name': '鴻海'}]})

    def test_the_business_line_is_read_from_the_filing(self):
        self.assertEqual(profile.parse(PAGE), '研究開發生產、製造、銷售各種積體電路')
        self.assertIsNone(profile.parse('<html>nothing here</html>'))

    def test_only_what_the_page_shows_is_fetched_and_it_is_kept_ninety_days(self):
        asked = []
        out = profile.refresh(self.store, NOW, fetcher=lambda s: asked.append(s) or '業務', pause=0)
        self.assertEqual(asked, ['2379', '2317'])
        self.assertEqual(out['fetched'], ['2379', '2317'])
        again = profile.refresh(self.store, NOW + timedelta(days=30), fetcher=lambda s: self.fail('refetched'), pause=0)
        self.assertEqual(again['kept'], 2)
        profile.refresh(self.store, NOW + timedelta(days=91), fetcher=lambda s: asked.append(s) or '新業務', pause=0)
        self.assertEqual(profile.business(self.store, '2379'), '新業務')

    def test_a_failed_fetch_keeps_what_was_there_and_does_not_raise(self):
        self.store.set_fact('2379', 'profile', {'business': '舊', 'fetched_at': (NOW - timedelta(days=200)).isoformat()})
        def broken(symbol):
            raise TimeoutError()
        out = profile.refresh(self.store, NOW, fetcher=broken, pause=0)
        self.assertIn('2379:TimeoutError', out['failed'])
        self.assertEqual(profile.business(self.store, '2379'), '舊')

    def test_the_page_gets_the_company_the_size_and_the_fundamentals(self):
        self.store.set_fact('2379', 'profile', {'business': '積體電路', 'fetched_at': NOW.isoformat()})
        self.store.set_fact('2379', 'financials', {'revenue': {'yoy_pct': 23.35, 'period_roc_ym': '11509'},
                                                   'income': {'eps_basic': 15.86}})
        with self.store.transaction():
            self.store.db.execute('INSERT INTO market_days VALUES(?,?,?)',
                                  ('2026-10-15', 'ratios', json.dumps({'2379': [28.21, 7.95, 3.21]})))
        self.store.set_meta('paper_performance', {'net_equity': 100000})
        row = daily_scan.annotate(self.store, self.store.meta('daily_scan'))['candidates'][0]
        self.assertEqual((row['business'], row['revenue_yoy_pct'], row['eps'], row['pe'], row['dividend_yield']),
                         ('積體電路', 23.35, 15.86, 28.21, 3.21))
        self.assertEqual((row['suggested_shares'], row['suggested_amount']), (7, 4914))


if __name__ == '__main__':
    unittest.main()
