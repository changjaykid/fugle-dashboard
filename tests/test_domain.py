"""Unit tests for stock_radar.domain — boundary conditions per spec.

Run: python3 -m pytest tests/ -q  (or python3 -m unittest tests.test_domain -v)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from datetime import datetime, timedelta, timezone

from stock_radar.domain import (
    TW, number, positive, stamp, fresh, floor_tick,
    validate_valuation, model_prices, review_reasons, decide, STATUSES,
    session_phase,
)


def iso(dt):
    return dt.isoformat()


def make_valuation(now, **overrides):
    v = {
        'sweet': 100.0, 'add': 110.0, 'buy': 120.0,
        'method': 'forward_pe', 'reason': '測試估值', 'thesis': '測試論述',
        'as_of': iso(now - timedelta(hours=1)),
        'valid_until': iso(now + timedelta(days=30)),
        'sources': [{'url': 'https://example.com/report', 'as_of': iso(now - timedelta(hours=1)), 'title': '測試來源'}],
        'evidence_reviewed': True,
    }
    v.update(overrides)
    return v


class TestNumberHelpers(unittest.TestCase):
    def test_number_parses_and_rejects(self):
        self.assertEqual(number('1,234.5'), 1234.5)
        self.assertIsNone(number('abc'))
        self.assertIsNone(number(True))  # bool must not silently become 1/0
        self.assertIsNone(number(float('nan')))
        self.assertIsNone(number(float('inf')))

    def test_positive(self):
        self.assertEqual(positive('5'), 5.0)
        self.assertIsNone(positive('0'))
        self.assertIsNone(positive('-5'))
        self.assertIsNone(positive(None))


class TestStampFresh(unittest.TestCase):
    def test_stamp_requires_timezone(self):
        self.assertIsNone(stamp('2026-09-10T10:00:00'))  # naive -> None
        self.assertIsNotNone(stamp('2026-09-10T10:00:00+08:00'))
        self.assertIsNone(stamp(None))
        self.assertIsNone(stamp('not-a-date'))

    def test_fresh_window(self):
        now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)
        self.assertTrue(fresh(iso(now), now, 120))
        self.assertTrue(fresh(iso(now - timedelta(seconds=119)), now, 120))
        self.assertFalse(fresh(iso(now - timedelta(seconds=121)), now, 120))
        self.assertFalse(fresh(iso(now + timedelta(seconds=1)), now, 120))  # future ts not "fresh"
        self.assertFalse(fresh(iso(now - timedelta(days=1)), now, 120))  # different date


class TestFloorTick(unittest.TestCase):
    def test_stock_tick_bands(self):
        self.assertEqual(floor_tick(9.99, 'stock'), 9.99)
        self.assertEqual(floor_tick(23.47, 'stock'), 23.45)   # band <50 -> .01, floor
        self.assertEqual(floor_tick(67.23, 'stock'), 67.2)     # band <100 -> .05
        self.assertEqual(floor_tick(234.7, 'stock'), 234.5)    # band <500 -> .1... actually check
        self.assertEqual(floor_tick(1234, 'stock'), 1230.0)    # band >=1000 -> step 5 (bands list has no 1000+ entry, falls to default step 5)

    def test_etf_tick_is_flat(self):
        self.assertEqual(floor_tick(23.456, 'etf_equity'), 23.45)  # <50 -> .01
        self.assertEqual(floor_tick(67.456, 'etf_equity'), 67.45)  # >=50 -> .05... floor to .05 band

    def test_rejects_non_positive_or_bad_kind(self):
        with self.assertRaises(ValueError):
            floor_tick(0, 'stock')
        with self.assertRaises(ValueError):
            floor_tick(100, 'bond')  # unsupported kind must raise, not silently tick


class TestValidateValuation(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)

    def test_valid_passes(self):
        v = make_valuation(self.now)
        validate_valuation(v, self.now)  # should not raise

    def test_string_prices_are_coerced_to_float_in_place(self):
        """Regression: a valuation with numeric-looking strings must pass validation
        AND come out with real floats, so a later `price > v['buy']` comparison in
        decide() cannot crash on str/float comparison."""
        v = make_valuation(self.now, sweet='100.0', add='110.0', buy='120.0')
        validate_valuation(v, self.now)
        self.assertIsInstance(v['sweet'], float)
        self.assertIsInstance(v['add'], float)
        self.assertIsInstance(v['buy'], float)
        self.assertEqual(v['buy'], 120.0)

    def test_optional_extra_price_fields_coerced_or_rejected(self):
        v = make_valuation(self.now, fair='125.5', avoid='garbage')
        with self.assertRaises(ValueError):
            validate_valuation(v, self.now)

    def test_method_kind_mismatch_rejected_when_kind_given(self):
        """A stock-only method (forward_pe) must be rejected for an ETF instrument,
        and vice versa, when the caller supplies the instrument kind."""
        v = make_valuation(self.now, method='forward_pe')
        with self.assertRaises(ValueError):
            validate_valuation(v, self.now, kind='etf_equity')
        v2 = make_valuation(self.now, method='etf_nav')
        with self.assertRaises(ValueError):
            validate_valuation(v2, self.now, kind='stock')

    def test_method_kind_match_passes(self):
        v = make_valuation(self.now, method='forward_pe')
        validate_valuation(v, self.now, kind='stock')  # should not raise

    def test_no_kind_given_skips_method_check(self):
        """Backward compatible: omitting kind= keeps old behavior (no method/kind check)."""
        v = make_valuation(self.now, method='forward_pe')
        validate_valuation(v, self.now)  # no kind passed, should not raise

    def test_order_must_hold(self):
        v = make_valuation(self.now, sweet=130.0)  # sweet > add > buy violates order
        with self.assertRaises(ValueError):
            validate_valuation(v, self.now)

    def test_missing_reason_thesis_method(self):
        v = make_valuation(self.now, reason='')
        with self.assertRaises(ValueError):
            validate_valuation(v, self.now)

    def test_expired_valid_until_rejected(self):
        v = make_valuation(self.now, valid_until=iso(self.now - timedelta(minutes=1)))
        with self.assertRaises(ValueError):
            validate_valuation(v, self.now)

    def test_source_must_be_https_with_as_of(self):
        v = make_valuation(self.now, sources=[{'url': 'http://example.com', 'as_of': iso(self.now)}])
        with self.assertRaises(ValueError):
            validate_valuation(v, self.now)

    def test_source_as_of_cannot_be_future(self):
        v = make_valuation(self.now, sources=[{'url': 'https://example.com', 'as_of': iso(self.now + timedelta(days=1))}])
        with self.assertRaises(ValueError):
            validate_valuation(v, self.now)

    def test_evidence_not_reviewed_rejected(self):
        v = make_valuation(self.now, evidence_reviewed=False)
        with self.assertRaises(ValueError):
            validate_valuation(v, self.now)


class TestModelPrices(unittest.TestCase):
    def test_forward_pe_stock(self):
        out = model_prices('forward_pe', {
            'forward_eps': 10, 'fair_pe': 20,
            'sweet_factor': 0.7, 'add_factor': 0.85, 'buy_factor': 1.0,
        }, 'stock')
        self.assertEqual(out['fair'], 200.0)
        self.assertEqual(out['buy'], 200.0)
        self.assertLess(out['sweet'], out['add'])
        self.assertLessEqual(out['add'], out['buy'])

    def test_kind_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            model_prices('forward_pe', {
                'forward_eps': 10, 'fair_pe': 20,
                'sweet_factor': 0.7, 'add_factor': 0.85, 'buy_factor': 1.0,
            }, 'etf_equity')

    def test_unsupported_method_rejected(self):
        with self.assertRaises(ValueError):
            model_prices('bond_ytm', {}, 'stock')

    def test_factor_order_enforced(self):
        with self.assertRaises(ValueError):
            model_prices('forward_pe', {
                'forward_eps': 10, 'fair_pe': 20,
                'sweet_factor': 0.9, 'add_factor': 0.5, 'buy_factor': 1.0,
            }, 'stock')

    def test_etf_nav_model(self):
        out = model_prices('etf_nav', {
            'nav': 140, 'fair_nav_ratio': 1.0,
            'sweet_factor': 0.95, 'add_factor': 0.98, 'buy_factor': 1.0,
        }, 'etf_equity')
        self.assertEqual(out['fair'], 140.0)


class TestReviewReasons(unittest.TestCase):
    def test_threshold_triggers(self):
        reasons = review_reasons({'forward_eps': 10}, {'forward_eps': 11.5})
        self.assertTrue(any('forward_eps' in r for r in reasons))

    def test_below_threshold_no_trigger(self):
        reasons = review_reasons({'forward_eps': 10}, {'forward_eps': 10.5})
        self.assertEqual(reasons, [])

    def test_events_passed_through(self):
        reasons = review_reasons({}, {}, events=['財報公布'])
        self.assertIn('財報公布', reasons)


class TestSessionPhase(unittest.TestCase):
    """Regression suite for the tri-state market_open contract (Codex
    review 2026-09-10): market_open=None must be its OWN branch, never
    collapsed into 'closed' via bool(None)."""

    def test_market_open_none_returns_none_phase(self):
        now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)
        self.assertIsNone(session_phase(now, None))

    def test_market_open_false_returns_closed(self):
        now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)
        self.assertEqual(session_phase(now, False), 'closed')

    def test_market_open_true_pre_market_window(self):
        now = datetime(2026, 9, 10, 8, 45, 0, tzinfo=TW)
        self.assertEqual(session_phase(now, True), 'pre_market')

    def test_market_open_true_regular_window(self):
        now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)
        self.assertEqual(session_phase(now, True), 'regular')

    def test_market_open_true_but_outside_hours_is_closed(self):
        now = datetime(2026, 9, 10, 20, 0, 0, tzinfo=TW)
        self.assertEqual(session_phase(now, True), 'closed')

    def test_none_and_closed_are_distinct_values_not_just_falsy(self):
        """The regression this guards against: some earlier code wrote
        `if not phase:` which is True for BOTH None and '' (never actually
        produced, but demonstrates the bug class) -- and worse, a caller
        that wrote `if not market_open:` could not tell None from False.
        This test asserts identity-based distinguishability end to end."""
        now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)
        none_phase = session_phase(now, None)
        closed_phase = session_phase(now, False)
        self.assertIsNone(none_phase)
        self.assertEqual(closed_phase, 'closed')
        self.assertNotEqual(none_phase, closed_phase)


class TestDecide(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 10, 9, 30, 0, tzinfo=TW)  # market hours, post-trial
        self.valuation = make_valuation(self.now)
        self.risk_ok = {'cleared': True, 'checked_at': iso(self.now)}
        self.instrument = {'symbol': '2330', 'kind': 'stock'}

    def _quote(self, **overrides):
        q = {
            'as_of': iso(self.now), 'trade_date': self.now.date().isoformat(),
            'is_trial': False, 'price': 105.0, 'previous_close': 100.0,
            'reference_price': 100.0, 'limit_down': 90.0, 'limit_up': 110.0,
            'book_as_of': iso(self.now),
            'bids': [{'price': 104.5, 'size': 10}, {'price': 104, 'size': 50}],
            'asks': [{'price': 105.5, 'size': 10}],
        }
        q.update(overrides)
        return q

    def _quote_low(self, price, **overrides):
        """Quote fixture with bids sitting just below the given price, for sweet-zone tests."""
        q = self._quote(price=price, bids=[
            {'price': round(price - 0.5, 2), 'size': 20},
            {'price': round(price - 1.0, 2), 'size': 50},
        ], asks=[{'price': round(price + 0.5, 2), 'size': 10}])
        q.update(overrides)
        return q

    def test_no_valuation_is_pending(self):
        r = decide(self.instrument, self._quote(), None, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'pending')

    def test_decide_never_crashes_on_string_valuation_prices(self):
        """Regression: valuation dict arriving with string prices (e.g. round-tripped
        through JSON storage inconsistently) must not raise inside decide()'s later
        `p > valuation['buy']` float comparisons -- validate_valuation coerces in place."""
        v = make_valuation(self.now, sweet='100.0', add='110.0', buy='120.0')
        q = self._quote_low(price=125.0, reference_price=120.0, previous_close=120.0,
                            limit_down=108.0, limit_up=132.0)
        r = decide(self.instrument, q, v, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'avoid')  # no TypeError, correct comparison result

    def test_decide_rejects_etf_only_method_for_stock_instrument(self):
        v = make_valuation(self.now, method='etf_nav')
        r = decide(self.instrument, self._quote(), v, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'blocked')

    def test_unmodeled_kind_is_pending_not_blocked(self):
        """etf_other (e.g. ETN) has no valuation model yet per spec -- this
        is a normal '待研究' state, not an operational block."""
        etn = dict(self.instrument, kind='etf_other')
        r = decide(etn, self._quote(), None, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'pending')

    def test_missing_risk_check_blocked(self):
        r = decide(self.instrument, self._quote(), self.valuation, now=self.now, market_open=True, risk=None)
        self.assertEqual(r['status'], 'blocked')

    def test_market_closed_blocked(self):
        r = decide(self.instrument, self._quote(), self.valuation, now=self.now, market_open=False, risk=self.risk_ok)
        self.assertEqual(r['status'], 'blocked')

    def test_market_open_none_is_blocked_with_distinct_reason_from_false(self):
        """Regression: market_open=None (calendar unverified) must not be
        silently coerced to the same 'closed' path as market_open=False
        (calendar positively says today is a holiday) -- the reasons shown
        to a human must differ so an unverified-calendar bug is
        distinguishable from a correctly-detected holiday."""
        r_unknown = decide(self.instrument, self._quote(), self.valuation, now=self.now,
                           market_open=None, risk=self.risk_ok)
        r_closed = decide(self.instrument, self._quote(), self.valuation, now=self.now,
                          market_open=False, risk=self.risk_ok)
        self.assertEqual(r_unknown['status'], 'blocked')
        self.assertEqual(r_closed['status'], 'blocked')
        self.assertNotEqual(r_unknown['reason'], r_closed['reason'])

    def test_market_open_defaults_to_none_not_false(self):
        """decide()'s market_open kwarg default changed from False to None
        (2026-09-10): a caller that forgets to pass market_open at all must
        land in the 'unverified' branch, not silently look like a
        confirmed non-trading day."""
        r = decide(self.instrument, self._quote(), self.valuation, now=self.now, risk=self.risk_ok)
        self.assertEqual(r['status'], 'blocked')
        self.assertIn('未確認', r['reason'])

    def test_stale_quote_rejected(self):
        old_quote = self._quote(as_of=iso(self.now - timedelta(seconds=999)))
        r = decide(self.instrument, old_quote, self.valuation, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'stale')

    def test_price_above_buy_is_avoid(self):
        # Price must exceed valuation['buy']=120 but stay within the 7% gap-vs-reference
        # guard (which fires earlier in decide()), so ref/prev also sit near 120.
        q = self._quote_low(price=121.0, reference_price=120.0, previous_close=120.0,
                            limit_down=108.0, limit_up=132.0)
        r = decide(self.instrument, q, self.valuation, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'avoid')

    def test_price_in_sweet_zone(self):
        q = self._quote_low(price=99.0, reference_price=100.0)
        r = decide(self.instrument, q, self.valuation, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'sweet')
        self.assertIsNotNone(r['suggested'])
        self.assertLessEqual(r['suggested'], 99.0)  # never suggest above cap

    def test_gap_beyond_7pct_blocked(self):
        q = self._quote(price=109.0, reference_price=100.0)  # >7% gap from ref triggers block before limit check
        # 109/100-1 = 9% > 7%
        r = decide(self.instrument, q, self.valuation, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'blocked')

    def test_halted_blocked(self):
        q = self._quote(halted=True)
        r = decide(self.instrument, q, self.valuation, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'blocked')

    def test_trial_before_9am_requires_trial_flag(self):
        early = datetime(2026, 9, 10, 8, 45, 0, tzinfo=TW)
        risk_early = {'cleared': True, 'checked_at': iso(early)}
        v_early = make_valuation(early)
        q = self._quote(as_of=iso(early), book_as_of=iso(early), is_trial=False)
        r = decide(self.instrument, q, v_early, now=early, market_open=True, risk=risk_early)
        # Missing explicit trial flag before 9am: domain treats as blocked (fundamentals/date
        # check happens first) OR stale depending on which guard fires; either is an acceptable
        # "do not show actionable price" outcome, but must never be a live status.
        self.assertIn(r['status'], ('stale', 'blocked'))
        self.assertIsNone(r['suggested'])

    def test_corporate_action_needs_review(self):
        q = self._quote(reference_price=95.0)  # ref moved vs prev close without review flag
        risk = dict(self.risk_ok)
        r = decide(self.instrument, q, self.valuation, now=self.now, market_open=True, risk=risk)
        self.assertEqual(r['status'], 'blocked')

    def test_incomplete_book_is_stale(self):
        q = self._quote(bids=[], asks=[])
        r = decide(self.instrument, q, self.valuation, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'stale')

    def test_decide_never_crashes_on_string_bid_ask_prices(self):
        """Regression (flagged in review): bids/asks price/size arriving as
        strings from some feed must not raise inside the later `lo <= b['price']
        <= cap` float comparisons; non-numeric levels are dropped instead."""
        q = self._quote_low(price=104.0, bids=[
            {'price': '103.5', 'size': '20'},
            {'price': 'not-a-number', 'size': 10},
            {'price': 103.0, 'size': 'also-not-a-number'},
        ], asks=[{'price': '104.5', 'size': '10'}])
        r = decide(self.instrument, q, self.valuation, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertIn(r['status'], ('sweet', 'add', 'buy'))
        self.assertEqual(r['suggested'], 103.5)

    def test_decide_all_garbage_bids_falls_back_to_stale(self):
        q = self._quote_low(price=104.0, bids=[{'price': 'x', 'size': 'y'}],
                            asks=[{'price': '104.5', 'size': '10'}])
        r = decide(self.instrument, q, self.valuation, now=self.now, market_open=True, risk=self.risk_ok)
        self.assertEqual(r['status'], 'stale')

    def test_never_suggests_above_limit_up(self):
        v = make_valuation(self.now, sweet=200, add=210, buy=220)
        q = self._quote(price=105.0)
        r = decide(self.instrument, q, v, now=self.now, market_open=True, risk=self.risk_ok)
        if r['suggested'] is not None:
            self.assertLessEqual(r['suggested'], 110.0)  # limit_up


if __name__ == '__main__':
    unittest.main()
