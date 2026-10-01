import unittest
from stock_radar import costs, paper, strategy

DAY, AT = '2026-04-01', '2026-04-01T13:30:00+08:00'


class CostTests(unittest.TestCase):
    def test_fee_floor_decides_small_orders(self):
        # The floor, not the rate, is what a small order pays, and it is why MIN_ORDER_VALUE exists.
        self.assertEqual(costs.fee(5000), 20.0)
        self.assertEqual(costs.fee(100000), float(int(100000 * 0.001425)))
        self.assertAlmostEqual(costs.rate_at(5000), (20 * 2 + 15) / 5000 * 100, places=3)
        self.assertGreater(costs.rate_at(5000), costs.rate_at(50000))
        self.assertAlmostEqual(costs.MIN_ORDER_VALUE, 20 / 0.001425, places=1)

    def test_tax_is_sell_side_only_and_lower_for_etfs(self):
        self.assertEqual(costs.buy_cost(1000, 50)['tax'], 0.0)
        self.assertEqual(costs.sell_cost(1000, 50, 'stock')['tax'], float(int(50000 * 0.003)))
        self.assertEqual(costs.sell_cost(1000, 50, 'etf_equity')['tax'], float(int(50000 * 0.001)))
        with self.assertRaises(ValueError):
            costs.sell_cost(1000, 50, 'futures')

    def test_round_trip_return_is_on_cash_not_on_price(self):
        trip = costs.round_trip(1000, 50, 50, 'stock')
        self.assertEqual(trip['gross_pct'], 0.0)
        self.assertLess(trip['return_pct'], 0)        # flat price still loses the costs
        self.assertLess(trip['profit'], 0)
        up = costs.round_trip(1000, 50, 55, 'stock')
        self.assertLess(up['return_pct'], up['gross_pct'])


class StrategyTests(unittest.TestCase):
    def test_stop_is_widened_to_the_floor_and_refused_past_the_ceiling(self):
        # Measured 2026-10-01: 14 of 106 real entries sat under 1% above the 20-day low, so the raw
        # level is sometimes indistinguishable from costs.
        tight = strategy.stop_for(100, 99.5)
        self.assertTrue(tight['widened'])
        self.assertAlmostEqual(tight['fraction'], strategy.SETTINGS['min_stop_fraction'])
        self.assertIsNone(strategy.stop_for(100, 80))   # 20% away, past max_stop_fraction

    def test_target_never_exceeds_fair_value(self):
        plain = strategy.target_for(100, 92, None)
        self.assertAlmostEqual(plain['price'], 100 + 8 * strategy.SETTINGS['target_r_multiple'])
        capped = strategy.target_for(100, 92, 105)
        self.assertEqual(capped['price'], 105)
        self.assertTrue(capped['capped_at_fair'])

    def test_size_is_equal_weight_capped_by_risk_and_refused_under_the_fee_floor(self):
        shares, why = strategy.size_for(100000, 100000, 100, 92)
        self.assertIsNone(why)
        self.assertLessEqual(shares * 100, 100000 * strategy.SETTINGS['max_position_fraction'])
        self.assertLessEqual((shares * 100) * 0.08, 100000 * strategy.SETTINGS['max_risk_fraction'] + 1)
        # Odd lots are the point: a 20,000 budget cannot buy a lot of a 600 dollar stock.
        odd, why = strategy.size_for(100000, 100000, 600, 552)
        self.assertIsNone(why)
        self.assertNotEqual(odd % 1000, 0)
        self.assertGreater(odd, 0)
        none, why = strategy.size_for(100000, 3000, 100, 92)
        self.assertEqual(none, 0)
        self.assertIn('低於', why)

    def test_a_bar_spanning_both_levels_is_resolved_as_a_stop(self):
        position = {'entry': 100, 'stop': 92, 'stop_now': 92, 'target': 112, 'entry_index': 0,
                    'shares': 100, 'plan': {'exit_mode': 'target_stop'}}
        both = (100, 115, 90, 110)
        self.assertEqual(strategy.exit_check(position, both, 1)['reason'], 'stop')

    def test_trail_mode_only_ratchets_upwards(self):
        s = {'exit_mode': 'trail', 'trail_fraction': 0.10}
        position = {'entry': 100, 'stop': None, 'stop_now': None, 'target': 150, 'entry_index': 0,
                    'shares': 100, 'plan': {'exit_mode': 'trail'}}
        position['stop_now'] = strategy.ratchet(position, (100, 120, 99, 118), s)
        self.assertAlmostEqual(position['stop_now'], 108.0)
        position['stop_now'] = strategy.ratchet(position, (118, 119, 110, 112), s)
        self.assertAlmostEqual(position['stop_now'], 108.0)   # peak did not move, stop did not fall

    def test_hold_mode_never_exits(self):
        position = {'entry': 100, 'stop': None, 'stop_now': None, 'target': None, 'entry_index': 0,
                    'shares': 100, 'plan': {'exit_mode': 'hold'}}
        self.assertIsNone(strategy.exit_check(position, (100, 10, 1, 2), 500,
                                              {'exit_mode': 'hold', 'max_holding_days': 0}))


class BookTests(unittest.TestCase):
    def book(self):
        return paper.Book(100000, started_day=DAY)

    def test_a_limit_order_fills_only_at_a_price_the_day_reached(self):
        b = self.book()
        self.assertIsNone(b.fill_price((105, 108, 101, 107), 100, 'buy'))
        self.assertEqual(b.fill_price((105, 108, 99, 107), 100, 'buy'), 100)
        # Gapped below the limit: the fill is the open, which is better than the order.
        self.assertEqual(b.fill_price((95, 99, 94, 98), 100, 'buy'), 95)

    def test_cash_fees_and_the_odd_lot_label(self):
        b = self.book()
        b.buy('2330', '台積電', 'stock', 30, 600, DAY, AT)
        charge = costs.buy_cost(30, 600)
        self.assertAlmostEqual(b.available(), 100000 - charge['cash'])
        self.assertEqual(b.positions['2330']['order_type'], 'odd_lot')
        b.buy('1234', '整股', 'stock', 1000, 20, DAY, AT)
        self.assertEqual(b.positions['1234']['order_type'], 'lot')
        with self.assertRaises(ValueError):
            b.buy('2330', '台積電', 'stock', 10, 600, DAY, AT)

    def test_sale_proceeds_are_not_spendable_until_settlement(self):
        b = self.book()
        b.buy('2330', '台積電', 'stock', 30, 600, DAY, AT)
        before = b.available()
        b.sell('2330', 650, DAY, AT, 'target', release_day='2026-04-07')
        self.assertEqual(b.available(), before)          # nothing landed yet
        self.assertGreater(b.unsettled(), 0)
        self.assertEqual(b.equity(), round(before + b.unsettled(), 2))
        b.settle('2026-04-06')
        self.assertEqual(b.unsettled(), b.unsettled())   # not due yet
        self.assertEqual(b.available(), before)
        b.settle('2026-04-07')
        self.assertGreater(b.available(), before)
        self.assertEqual(b.unsettled(), 0)

    def test_net_equity_subtracts_the_cost_of_getting_out(self):
        b = self.book()
        b.buy('2330', '台積電', 'stock', 30, 600, DAY, AT)
        b.mark({'2330': (600, 610, 595, 605)}, DAY)
        self.assertLess(b.net_equity(), b.equity())
        self.assertAlmostEqual(b.net_equity(),
                               round(b.available() + costs.sell_cost(30, 605, 'stock')['cash'], 2))

    def test_a_symbol_without_a_bar_keeps_its_mark_and_is_reported(self):
        b = self.book()
        b.buy('2330', '台積電', 'stock', 30, 600, DAY, AT)
        stale = b.mark({}, '2026-04-02')
        self.assertEqual(stale, ['2330'])
        self.assertEqual(b.positions['2330']['mark'], 600)
        self.assertEqual(b.positions['2330']['mark_day'], DAY)

    def test_a_saved_book_reloads_as_itself_and_does_not_duplicate(self):
        import shutil
        import tempfile
        from pathlib import Path
        from stock_radar.store import Store
        folder = Path(tempfile.mkdtemp())
        try:
            store = Store(folder / 'p.db')
            b = self.book()
            b.buy('2330', '台積電', 'stock', 30, 600, DAY, AT, plan={'stop': 552, 'target': 648})
            b.buy('1234', '整股', 'stock', 1000, 20, DAY, AT)
            b.sell('1234', 22, '2026-04-03', AT, 'target', release_day='2026-04-07')
            b.mark({'2330': (600, 610, 595, 605)}, '2026-04-03')
            b.save(store)
            again = paper.Book.load(store)
            for field in ('available', 'unsettled', 'net_equity', 'equity', 'realised'):
                self.assertEqual(getattr(again, field)(), getattr(b, field)(), field)
            self.assertEqual(list(again.positions), list(b.positions))
            self.assertEqual(len(again.trades), len(b.trades))
            self.assertEqual(len(again.closed), len(b.closed))
            self.assertEqual(again.started_day, b.started_day)
            again.save(store)
            rows = store.db.execute('SELECT COUNT(*) FROM paper_trades').fetchone()[0]
            self.assertEqual(rows, 4)   # two buys, one sell, one closed round trip
            store.close()
        finally:
            shutil.rmtree(folder)

    def test_performance_counts_a_flat_round_trip_as_a_loss(self):
        b = self.book()
        b.buy('2330', '台積電', 'stock', 30, 600, DAY, AT)
        b.sell('2330', 600, DAY, AT, 'time')
        p = paper.performance(b)
        self.assertEqual(p['closed_trades'], 1)
        self.assertEqual(p['wins'], 0)
        self.assertLess(p['realised_profit'], 0)
        self.assertGreater(p['fees_and_tax'], 0)
        self.assertEqual(p['exit_reasons'], {'time': 1})
