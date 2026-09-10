"""Unit tests for stock_radar.quotes — pure parsing against a recorded real
fixture (tests/fixtures/mis_quote_sample.json, captured 2026-09-10 from the
live mis.twse.com.tw feed for 2330/0050/1240). No network in these tests;
a fake `session` object with a canned .get() is injected."""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from stock_radar.quotes import fetch_quotes, build_symbol_key

FIXTURES = Path(__file__).parent / 'fixtures'


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'params': params})
        return FakeResponse(self.payload)


def load_fixture():
    return json.loads((FIXTURES / 'mis_quote_sample.json').read_text())


class TestBuildSymbolKey(unittest.TestCase):
    def test_tse_prefix(self):
        self.assertEqual(build_symbol_key('2330', 'TSE'), 'tse_2330.tw')

    def test_otc_prefix(self):
        self.assertEqual(build_symbol_key('1240', 'OTC'), 'otc_1240.tw')

    def test_unknown_market_raises(self):
        with self.assertRaises(KeyError):
            build_symbol_key('2330', 'BOND')


class TestFetchQuotes(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession(load_fixture())
        self.instruments = [
            {'symbol': '2330', 'market': 'TSE'},
            {'symbol': '0050', 'market': 'TSE'},
            {'symbol': '1240', 'market': 'OTC'},
        ]

    def test_returns_all_symbols(self):
        quotes = fetch_quotes(self.instruments, session=self.session)
        self.assertEqual(set(quotes.keys()), {'2330', '0050', '1240'})

    def test_price_and_book_parsed(self):
        quotes = fetch_quotes(self.instruments, session=self.session)
        q = quotes['2330']
        self.assertIsInstance(q['price'], float)
        self.assertGreater(q['price'], 0)
        self.assertTrue(len(q['bids']) > 0)
        self.assertTrue(len(q['asks']) > 0)
        for level in q['bids'] + q['asks']:
            self.assertIn('price', level)
            self.assertIn('size', level)

    def test_is_trial_always_none_not_guessed(self):
        """Regression: this feed has no reliable trial-vs-regular flag, so
        we must never infer is_trial from wall-clock time here (that
        decision belongs in domain.decide(), and only if a real signal is
        found)."""
        quotes = fetch_quotes(self.instruments, session=self.session)
        for q in quotes.values():
            self.assertIsNone(q['is_trial'])

    def test_trade_date_formatted_iso(self):
        quotes = fetch_quotes(self.instruments, session=self.session)
        for q in quotes.values():
            if q['trade_date']:
                self.assertRegex(q['trade_date'], r'^\d{4}-\d{2}-\d{2}$')

    def test_batches_requests(self):
        many = [{'symbol': str(1000 + i), 'market': 'TSE'} for i in range(250)]
        session = FakeSession({'msgArray': []})
        fetch_quotes(many, session=session, batch_size=100)
        self.assertEqual(len(session.calls), 3)  # 250 / 100 -> 3 batches

    def test_source_fields_present(self):
        quotes = fetch_quotes(self.instruments, session=self.session)
        for q in quotes.values():
            self.assertEqual(q['source'], 'mis.twse.com.tw getStockInfo.jsp')
            self.assertTrue(q['source_url'].startswith('https://'))


if __name__ == '__main__':
    unittest.main()
