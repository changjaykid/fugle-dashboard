"""Unit tests for stock_radar.fugle — built directly from the official
documented example payloads at developer.fugle.tw (fetched 2026-09-10),
not synthetic data."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from unittest import mock

from stock_radar.fugle import (
    build_quote, fetch_quotes, _epoch_micros_to_iso,
)

# Verbatim from https://developer.fugle.tw/docs/data/http-api/intraday/ticker/
TICKER_EXAMPLE = {
    "date": "2023-05-29", "type": "EQUITY", "exchange": "TWSE", "market": "TSE",
    "symbol": "2330", "name": "台積電", "industry": "24", "securityType": "01",
    "previousClose": 566, "referencePrice": 566, "limitUpPrice": 622,
    "limitDownPrice": 510, "canDayTrade": True, "canBuyDayTrade": True,
    "canBelowFlatMarginShortSell": True, "canBelowFlatSBLShortSell": True,
    "isAttention": False, "isDisposition": False, "isUnusuallyRecommended": False,
    "isSpecificAbnormally": False, "matchingInterval": 0, "securityStatus": "NORMAL",
    "boardLot": 1000, "tradingCurrency": "TWD",
}

# Verbatim from https://developer.fugle.tw/docs/data/http-api/intraday/quote/
QUOTE_EXAMPLE = {
    "date": "2023-05-29", "type": "EQUITY", "exchange": "TWSE", "market": "TSE",
    "symbol": "2330", "name": "台積電", "referencePrice": 566, "previousClose": 566,
    "openPrice": 574, "openTime": 1685322000049353, "highPrice": 574,
    "highTime": 1685322000049353, "lowPrice": 564, "lowTime": 1685327142152580,
    "closePrice": 568, "closeTime": 1685338200000000, "avgPrice": 568.77,
    "change": 2, "changePercent": 0.35, "amplitude": 1.77, "lastPrice": 568,
    "lastSize": 4778,
    "bids": [{"price": 567, "size": 87}, {"price": 566, "size": 2454},
             {"price": 565, "size": 611}, {"price": 564, "size": 609},
             {"price": 563, "size": 636}],
    "asks": [{"price": 568, "size": 800}, {"price": 569, "size": 806},
             {"price": 570, "size": 3643}, {"price": 571, "size": 1041},
             {"price": 572, "size": 2052}],
    "total": {"tradeValue": 31019803000, "tradeVolume": 54538,
              "tradeVolumeAtBid": 19853, "tradeVolumeAtAsk": 27900,
              "transaction": 9530, "time": 1685338200000000},
    "lastTrade": {"bid": 567, "ask": 568, "price": 568, "size": 4778,
                  "time": 1685338200000000, "serial": 6652422},
    "lastTrial": {"bid": 567, "ask": 568, "price": 568, "size": 4772,
                  "time": 1685338196400347, "serial": 6651941},
    "isClose": True, "serial": 6652422, "lastUpdated": 1685338200000000,
}


class TestEpochConversion(unittest.TestCase):
    def test_documented_epoch_converts_to_expected_wall_clock(self):
        # 1685338200000000 us -> 2023-05-29 05:30:00 UTC -> 13:30:00+08:00
        # (matches TWSE's real 13:30 market close time -- this is the
        # `closeTime`/`lastUpdated` value from the official example.)
        iso = _epoch_micros_to_iso(1685338200000000)
        self.assertTrue(iso.startswith('2023-05-29T13:30:00'))
        self.assertTrue(iso.endswith('+08:00'))

    def test_none_and_garbage_rejected(self):
        self.assertIsNone(_epoch_micros_to_iso(None))
        self.assertIsNone(_epoch_micros_to_iso('not-a-number'))
        self.assertIsNone(_epoch_micros_to_iso(0))
        self.assertIsNone(_epoch_micros_to_iso(-5))


class TestBuildQuote(unittest.TestCase):
    def test_official_example_produces_regular_trade_not_trial(self):
        """In the documented example, lastTrade.time (...200000000) is
        AFTER lastTrial.time (...196400347), i.e. the market has closed
        past the trial-match session -- so is_trial must be False, `price`
        holds the real trade, and `trial_price` is still independently
        populated from lastTrial (both keys are always populated when
        available -- decide() reads whichever one it needs by wall-clock
        window, not by is_trial alone)."""
        q = build_quote('2330', TICKER_EXAMPLE, QUOTE_EXAMPLE)
        self.assertFalse(q['is_trial'])
        self.assertEqual(q['price'], 568.0)
        self.assertEqual(q['trial_price'], 568.0)  # lastTrial.price in the example
        self.assertTrue(q['as_of'].startswith('2023-05-29T13:30:00'))

    def test_trial_only_payload_detected_as_trial(self):
        """Construct a quote payload with only lastTrial present (no
        lastTrade yet today) -- this is the real shape during the 08:30-
        09:00 pre-market session per Fugle's docs. `price` must stay None
        (no real trade happened yet) while `trial_price` is populated --
        this is the exact case that a collapsed single-field design would
        get wrong."""
        payload = dict(QUOTE_EXAMPLE)
        payload['lastTrade'] = {}
        q = build_quote('2330', TICKER_EXAMPLE, payload)
        self.assertTrue(q['is_trial'])
        self.assertIsNone(q['price'])
        self.assertEqual(q['trial_price'], 568.0)

    def test_both_ticks_present_populates_both_fields_independently(self):
        """Regression: even when both lastTrade and lastTrial exist (the
        normal post-open state, trial data just lingering from the
        morning), both price and trial_price must be independently
        available on the quote dict -- collapsing to one field based on
        is_trial was the bug this fixes."""
        q = build_quote('2330', TICKER_EXAMPLE, QUOTE_EXAMPLE)
        self.assertIsNotNone(q['price'])
        self.assertIsNotNone(q['trial_price'])

    def test_previous_close_and_reference_price_both_sourced_from_quote(self):
        q = build_quote('2330', TICKER_EXAMPLE, QUOTE_EXAMPLE)
        self.assertEqual(q['previous_close'], 566.0)
        self.assertEqual(q['reference_price'], 566.0)

    def test_limits_sourced_from_ticker(self):
        q = build_quote('2330', TICKER_EXAMPLE, QUOTE_EXAMPLE)
        self.assertEqual(q['limit_up'], 622.0)
        self.assertEqual(q['limit_down'], 510.0)

    def test_disposition_flag_passed_through(self):
        q = build_quote('2330', TICKER_EXAMPLE, QUOTE_EXAMPLE)
        self.assertFalse(q['disposition'])
        disposed_ticker = dict(TICKER_EXAMPLE, isDisposition=True)
        q2 = build_quote('2330', disposed_ticker, QUOTE_EXAMPLE)
        self.assertTrue(q2['disposition'])

    def test_bids_asks_parsed_as_numeric_dicts(self):
        q = build_quote('2330', TICKER_EXAMPLE, QUOTE_EXAMPLE)
        self.assertEqual(q['bids'][0], {'price': 567.0, 'size': 87.0})
        self.assertEqual(len(q['bids']), 5)
        self.assertEqual(len(q['asks']), 5)

    def test_neither_trial_nor_trade_present_yields_no_price(self):
        payload = dict(QUOTE_EXAMPLE, lastTrade={}, lastTrial={})
        q = build_quote('2330', TICKER_EXAMPLE, payload)
        self.assertIsNone(q['as_of'])
        self.assertIsNone(q['price'])
        self.assertIsNone(q['trial_price'])
        self.assertIsNone(q['is_trial'])


class TestFetchQuotes(unittest.TestCase):
    def test_fetch_quotes_calls_ticker_then_quote_per_symbol(self):
        class FakeResp:
            def __init__(self, payload):
                self.payload = payload
            def raise_for_status(self):
                pass
            def json(self):
                return self.payload

        def fake_get(url, **kwargs):
            if 'ticker' in url:
                return FakeResp(TICKER_EXAMPLE)
            return FakeResp(QUOTE_EXAMPLE)

        session = mock.Mock()
        session.get.side_effect = fake_get
        out = fetch_quotes([{'symbol': '2330'}], 'fake-key', session=session)
        self.assertIn('2330', out)
        self.assertEqual(out['2330']['previous_close'], 566.0)
        self.assertEqual(session.get.call_count, 2)
        # confirm the API key header was actually sent
        for call in session.get.call_args_list:
            self.assertEqual(call.kwargs['headers']['X-API-KEY'], 'fake-key')


if __name__ == '__main__':
    unittest.main()
