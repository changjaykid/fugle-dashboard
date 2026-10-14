"""The one set of entry and exit rules: what it buys, where, and how it leaves."""
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from stock_radar import journal, swing
from stock_radar.store import Store


def sessions(n, end=date(2026, 4, 3)):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(str(d))
        d -= timedelta(days=1)
    return out[::-1]


def trend(n=130, start=100.0, step=0.4, pullback=0, lots=1000):
    """A steady uptrend with a shallow pullback over the last `pullback` sessions, on light volume."""
    bars, price = [], start
    for i in range(n):
        if pullback and i >= n - pullback:
            price -= step * 3
            bars.append([price + 0.3, price + 0.5, price - 0.5, price, lots * 0.6])
        else:
            price += step
            bars.append([price - 0.3, price + 0.5, price - 0.5, price, lots])
    return bars


def market(days, bars_by_symbol, index_up=True):
    out = []
    for k, d in enumerate(days):
        table = {s: b[k] for s, b in bars_by_symbol.items()}
        level = 100 + (k if index_up else -k) * 0.2
        table['0050'] = [level, level, level, level, 1000]
        out.append((d, table))
    return out


class RuleTests(unittest.TestCase):
    def test_bands_are_twenty_closes_two_deviations(self):
        closes = [100.0] * 19 + [120.0]
        lower, middle, upper = swing.bands(closes)
        self.assertAlmostEqual(middle, 101.0)
        self.assertAlmostEqual(upper - middle, middle - lower)
        self.assertIsNone(swing.bands([100.0] * 19))

    def test_a_pullback_in_a_trend_with_buying_is_a_buy_at_the_lower_band(self):
        bars = trend(pullback=4)
        flows = [[50, 10]] * len(bars)
        f = swing.features(bars, flows)
        ok, why, _ = swing.judge(f, market_return_60=0.0)
        self.assertTrue(ok, why)
        p = swing.plan(f)
        self.assertLessEqual(p['limit'], f['close'])
        self.assertEqual(p['stop'], swing.tick(p['limit'] * 0.9))
        self.assertAlmostEqual(p['stop_pct'], round((1 - p['stop'] / p['limit']) * 100, 2))
        self.assertAlmostEqual(p['reward_risk'], round((p['target'] - p['limit']) / (p['limit'] - p['stop']), 2))
        self.assertGreater(p['target'], f['close'])

    def test_selling_by_the_institutions_rules_it_out(self):
        bars = trend(pullback=4)
        ok, why, _ = swing.judge(swing.features(bars, [[-50, -10]] * len(bars)), 0.0)
        self.assertFalse(ok)
        self.assertIn('法人賣超', why)

    def test_a_stock_at_its_high_is_not_a_pullback(self):
        bars = trend()
        ok, why, _ = swing.judge(swing.features(bars, [[50, 10]] * len(bars)), 0.0)
        self.assertFalse(ok)
        self.assertIn('回檔區', why)

    def test_a_disposition_notice_rules_it_out(self):
        bars = trend(pullback=4)
        ok, why, _ = swing.judge(swing.features(bars, [[50, 10]] * len(bars)), 0.0,
                              risk={'events': ['處置'], 'cleared': False})
        self.assertFalse(ok)
        self.assertIn('處置', why)

    def test_a_falling_market_places_no_orders(self):
        days = sessions(130)
        flat = [[100, 100.5, 99.5, 100, 1000]] * 130
        names = {'2330': {'name': '台積電'}, '1101': {'name': '台泥'}}
        weak = swing.scan(market(days, {'2330': trend(pullback=4), '1101': flat}, index_up=False),
                          [(d, {'2330': [50, 10]}) for d in days], names)
        self.assertFalse(weak['market_open'])
        self.assertEqual(weak['candidates'], [])
        self.assertEqual([c['symbol'] for c in weak['held_back']], ['2330'])
        strong = swing.scan(market(days, {'2330': trend(pullback=4), '1101': flat}),
                            [(d, {'2330': [50, 10]}) for d in days], names)
        self.assertTrue(strong['market_open'])
        self.assertEqual([c['symbol'] for c in strong['candidates']], ['2330'])
        row = strong['candidates'][0]
        self.assertEqual((row['foreign_lots'], row['trust_lots']), (50 * 5, 10 * 5))
        self.assertGreater(row['vs_market_60_pct'], 0)


class ExitTests(unittest.TestCase):
    def test_nothing_leaves_on_the_day_it_fills(self):
        self.assertIsNone(swing.exit_today({'entry': 100}, [100, 100, 50, 60], 1, 101))

    def test_the_stop_comes_first_when_a_bar_spans_both(self):
        out = swing.exit_today({'entry': 100}, [100, 120, 85, 110], 3, 105)
        self.assertEqual(out[:2], ('stop', 90.0))

    def test_reaching_the_band_does_not_sell_it_arms_the_trail(self):
        position = {'entry': 100}
        self.assertIsNone(swing.exit_today(position, [104, 108, 103, 107], 3, 106))
        self.assertTrue(position['armed'])
        self.assertEqual(position['peak'], 108)
        # 3% under the best price closes it, at the close.
        self.assertIsNone(swing.exit_today(position, [107, 110, 106, 107.5], 4, 106))
        out = swing.exit_today(position, [107, 107, 105, 106.6], 5, 106)
        self.assertEqual(out[:2], ('target', 106.6))

    def test_a_dip_before_the_band_is_reached_is_held(self):
        position = {'entry': 100}
        self.assertIsNone(swing.exit_today(position, [103, 108, 96, 97], 3, 120))
        self.assertFalse(position.get('armed'))

    def test_time_runs_out_at_forty_sessions(self):
        out = swing.exit_today({'entry': 100}, [100, 101, 99, 100.5], 40, 130)
        self.assertEqual(out[:2], ('time', 100.5))

    def test_a_band_too_close_to_the_price_paid_does_not_arm(self):
        position = {'entry': 100}
        swing.exit_today(position, [100, 101, 99, 100.4], 3, 100.5)
        self.assertFalse(position.get('armed'))

    def test_prices_sit_on_the_exchange_tick_grid(self):
        self.assertEqual(swing.tick(702.18), 702.0)
        self.assertEqual(swing.tick(113.97), 113.5)
        self.assertEqual(swing.tick(36.81), 36.8)
        self.assertEqual(swing.tick(1003.72, down=False), 1005.0)
        self.assertEqual(swing.tick(9.999), 9.99)

    def test_a_limit_fills_only_where_the_day_traded(self):
        self.assertIsNone(swing.fill([100, 101, 99, 100], 98))
        self.assertEqual(swing.fill([100, 101, 97, 100], 98), 98)
        self.assertEqual(swing.fill([96, 99, 95, 98], 98), 96)


class FollowAndJournalTests(unittest.TestCase):
    def prices(self, bars):
        days = sessions(len(bars))
        return [(d, {'2330': b}) for d, b in zip(days, bars)]

    def test_follow_reports_unfilled_open_and_closed(self):
        flat = [[100, 101, 99, 100, 1000]] * 30
        p = self.prices(flat + [[100, 101, 99.5, 100, 1000]] * 6)
        self.assertEqual(swing.follow(p, 29, '2330', 95)['status'], 'unfilled')
        p = self.prices(flat + [[100, 101, 99.5, 100, 1000]] * 2)
        self.assertEqual(swing.follow(p, 29, '2330', 95)['status'], 'pending')
        p = self.prices(flat + [[99, 100, 94, 96, 1000], [96, 97, 80, 82, 1000]])
        out = swing.follow(p, 29, '2330', 95)
        self.assertEqual((out['status'], out['how'], out['fill']), ('closed', 'stop', 95))
        self.assertAlmostEqual(out['net_pct'], round((85.5 / 95 - 1 - 0.00585) * 100, 2))

    def test_the_journal_records_once_and_settles_what_can_be_settled(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        store = Store(folder / 'j.db')
        self.addCleanup(store.close)
        bars = [[100, 101, 99, 100, 1000]] * 30 + [[99, 100, 94, 96, 1000], [96, 97, 80, 82, 1000]]
        days = sessions(len(bars))
        with store.transaction():
            for d, b in zip(days, bars):
                store.db.execute('INSERT INTO market_days VALUES(?,?,?)', (d, 'prices', json.dumps({'2330': b})))
        c = {'symbol': '2330', 'name': '台積電', 'close': 100, 'limit': 95, 'target': 102, 'stop': 85.5,
             'flow_share': 0.02, 'why': '測試'}
        store.set_meta('daily_scan', {'version': swing.VERSION, 'day': days[29], 'candidates': [c], 'held_back': []})
        self.assertEqual(journal.record(store)['added'], 1)
        self.assertEqual(journal.record(store)['added'], 0)
        self.assertEqual(journal.evaluate(store)['settled'], 1)
        st = journal.stats(store)
        self.assertEqual((st['proposed'], st['closed'], st['exits']['stop']), (1, 1, 1))


if __name__ == '__main__':
    unittest.main()
