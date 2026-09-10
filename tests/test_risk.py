"""Unit tests for stock_radar.risk — offline via mocked requests.get using
real fixture payloads captured from live TWSE endpoints (2026-09-10)."""
import sys
import json
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from datetime import date

from stock_radar.risk import (
    _roc_date, fetch_halted_symbols, fetch_disposition_symbols, build_risk_facts,
)

FIXTURES = Path(__file__).parent / 'fixtures'


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """Routes GET by URL substring to the matching fixture file."""
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kwargs):
        for needle, fixture in self.routes.items():
            if needle in url:
                return FakeResponse(json.loads((FIXTURES / fixture).read_text()))
        raise AssertionError(f'unexpected URL in test: {url}')


class TestRocDate(unittest.TestCase):
    def test_seven_digit_form(self):
        self.assertEqual(_roc_date('1150910'), date(2026, 9, 10))

    def test_slash_form(self):
        self.assertEqual(_roc_date('115/09/07'), date(2026, 9, 7))

    def test_empty_returns_none(self):
        self.assertIsNone(_roc_date(''))
        self.assertIsNone(_roc_date(None))

    def test_garbage_returns_none(self):
        self.assertIsNone(_roc_date('not-a-date'))


class TestFetchHalted(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession({'TWTAWU': 'twse_twtawu_sample.json'})

    def test_symbol_within_halt_window_included(self):
        # Fixture: 1218 halted 1150813 -> resume 1150814 (ROC). Use a `today`
        # inside that real captured window.
        out = fetch_halted_symbols(session=self.session, today=date(2026, 8, 13))
        self.assertIn('1218', out)

    def test_symbol_after_resume_date_excluded(self):
        out = fetch_halted_symbols(session=self.session, today=date(2026, 9, 10))
        self.assertNotIn('1218', out)


class TestFetchDisposition(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession({'punish': 'twse_punish_sample.json'})

    def test_symbol_within_disposition_period_included(self):
        # Fixture: 2455 period 115/09/07~115/09/15
        out = fetch_disposition_symbols(session=self.session, today=date(2026, 9, 10))
        self.assertIn('2455', out)
        self.assertIn('reason', out['2455'])

    def test_symbol_before_period_excluded(self):
        out = fetch_disposition_symbols(session=self.session, today=date(2026, 8, 1))
        self.assertNotIn('2455', out)

    def test_symbol_after_period_excluded(self):
        out = fetch_disposition_symbols(session=self.session, today=date(2026, 10, 1))
        self.assertNotIn('2455', out)


class TestBuildRiskFacts(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession({
            'TWTAWU': 'twse_twtawu_sample.json',
            'punish': 'twse_punish_sample.json',
        })

    def test_clean_tse_symbol_cleared_no_events(self):
        instruments = [{'symbol': '2330', 'market': 'TSE'}]
        out = build_risk_facts(instruments, session=self.session,
                               now=__import__('datetime').datetime(2026, 9, 10, tzinfo=__import__('stock_radar.risk', fromlist=['TW']).TW))
        self.assertTrue(out['2330']['cleared'])
        self.assertEqual(out['2330']['events'], [])

    def test_disposed_symbol_cleared_true_but_has_event(self):
        """cleared=True means 'the check ran', not 'no problems' -- the
        event itself is what blocks decide() downstream."""
        instruments = [{'symbol': '2455', 'market': 'TSE'}]
        out = build_risk_facts(instruments, session=self.session,
                               now=__import__('datetime').datetime(2026, 9, 10, tzinfo=__import__('stock_radar.risk', fromlist=['TW']).TW))
        self.assertTrue(out['2455']['cleared'])
        self.assertTrue(len(out['2455']['events']) > 0)
        self.assertIn('處置', out['2455']['events'][0])

    def test_otc_symbol_not_cleared(self):
        instruments = [{'symbol': '6488', 'market': 'OTC'}]
        out = build_risk_facts(instruments, session=self.session,
                               now=__import__('datetime').datetime(2026, 9, 10, tzinfo=__import__('stock_radar.risk', fromlist=['TW']).TW))
        self.assertFalse(out['6488']['cleared'])
        self.assertTrue(len(out['6488']['events']) > 0)

    def test_decide_actually_unblocks_for_a_cleared_clean_symbol(self):
        """End-to-end: feed a risk fact built by this module into decide()
        and confirm a clean TSE symbol with a valid quote+valuation is no
        longer stuck at 'blocked' for lack of a risk check."""
        from datetime import datetime, timedelta
        from stock_radar.domain import TW, decide
        now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)
        instruments = [{'symbol': '2330', 'market': 'TSE', 'kind': 'stock'}]
        risk = build_risk_facts(instruments, session=self.session, now=now)['2330']
        valuation = {
            'sweet': 100.0, 'add': 110.0, 'buy': 120.0, 'method': 'forward_pe',
            'reason': 'x', 'thesis': 'y', 'as_of': (now-timedelta(hours=1)).isoformat(),
            'valid_until': (now+timedelta(days=1)).isoformat(),
            'sources': [{'url': 'https://x', 'as_of': now.isoformat(), 'title': 't'}],
            'evidence_reviewed': True,
        }
        quote = {
            'as_of': now.isoformat(), 'trade_date': now.date().isoformat(), 'is_trial': False,
            'price': 105.0, 'previous_close': 100.0, 'reference_price': 100.0,
            'limit_up': 130.0, 'limit_down': 70.0, 'book_as_of': now.isoformat(),
            'bids': [{'price': 104.5, 'size': 10}], 'asks': [{'price': 105.5, 'size': 10}],
            'halted': False, 'disposition': False,
        }
        result = decide(instruments[0], quote, valuation, now=now, market_open=True, risk=risk)
        self.assertNotEqual(result['status'], 'blocked')

    def test_decide_still_blocks_for_disposed_symbol(self):
        from datetime import datetime, timedelta
        from stock_radar.domain import TW, decide
        now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)
        instruments = [{'symbol': '2455', 'market': 'TSE', 'kind': 'stock'}]
        risk = build_risk_facts(instruments, session=self.session, now=now)['2455']
        valuation = {
            'sweet': 100.0, 'add': 110.0, 'buy': 120.0, 'method': 'forward_pe',
            'reason': 'x', 'thesis': 'y', 'as_of': (now-timedelta(hours=1)).isoformat(),
            'valid_until': (now+timedelta(days=1)).isoformat(),
            'sources': [{'url': 'https://x', 'as_of': now.isoformat(), 'title': 't'}],
            'evidence_reviewed': True,
        }
        quote = {
            'as_of': now.isoformat(), 'trade_date': now.date().isoformat(), 'is_trial': False,
            'price': 105.0, 'previous_close': 100.0, 'reference_price': 100.0,
            'limit_up': 130.0, 'limit_down': 70.0, 'book_as_of': now.isoformat(),
            'bids': [{'price': 104.5, 'size': 10}], 'asks': [{'price': 105.5, 'size': 10}],
        }
        result = decide(instruments[0], quote, valuation, now=now, market_open=True, risk=risk)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('處置', result['reason'])


if __name__ == '__main__':
    unittest.main()
