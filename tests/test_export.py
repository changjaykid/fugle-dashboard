"""Unit tests for stock_radar.export — contract shape compliance."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from datetime import datetime, timedelta
from stock_radar.domain import TW
from stock_radar.export import build_radar_json


class TestBuildRadarJson(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)
        self.instruments = [
            {'symbol': '3661', 'name': '世芯-KY', 'kind': 'stock', 'market': 'TSE', 'industry_code': '半導體業'},
            {'symbol': '0050', 'name': '元大台灣50', 'kind': 'etf_equity', 'market': 'TSE', 'industry_code': None},
        ]

    def test_empty_state_matches_contract_pending_shape(self):
        out = build_radar_json(instruments=self.instruments, quotes={}, decisions={},
                               valuations={}, research={}, health=[], now=self.now)
        self.assertEqual(out['schema_version'], 1)
        self.assertEqual(out['coverage']['universe'], 2)
        self.assertEqual(out['coverage']['stocks'], 1)
        self.assertEqual(out['coverage']['etfs'], 1)
        self.assertEqual(out['coverage']['quotes'], 0)
        self.assertEqual(out['coverage']['valued'], 0)
        item = out['items'][0]
        self.assertIsNone(item['quote']['price'])
        self.assertEqual(item['signal']['status'], 'pending')
        self.assertIsNone(item['valuation'])

    def test_missing_data_uses_null_not_zero(self):
        """Per contract: 缺資料 JSON null，不使用 0."""
        out = build_radar_json(instruments=self.instruments, quotes={}, decisions={},
                               valuations={}, research={}, health=[], now=self.now)
        item = out['items'][0]
        for field in ('previous_close', 'reference_price', 'price', 'trial_price', 'as_of', 'trade_date'):
            self.assertIsNone(item['quote'][field], f'{field} should be null, not 0')

    def test_quote_populates_coverage_count(self):
        quotes = {'3661': {'price': 4055.0, 'as_of': self.now.isoformat(), 'is_trial': False,
                           'previous_close': 3905.0, 'source': 'mis.twse', 'source_url': 'https://mis.twse.com.tw'}}
        out = build_radar_json(instruments=self.instruments, quotes=quotes, decisions={},
                               valuations={}, research={}, health=[], now=self.now)
        self.assertEqual(out['coverage']['quotes'], 1)
        item = next(i for i in out['items'] if i['symbol'] == '3661')
        self.assertEqual(item['quote']['price'], 4055.0)

    def test_trial_price_goes_to_trial_field_not_price(self):
        quotes = {'3661': {'price': 4000.0, 'as_of': self.now.isoformat(), 'is_trial': True,
                           'previous_close': 3905.0, 'source': 'x', 'source_url': 'https://x'}}
        out = build_radar_json(instruments=self.instruments, quotes=quotes, decisions={},
                               valuations={}, research={}, health=[], now=self.now)
        item = next(i for i in out['items'] if i['symbol'] == '3661')
        self.assertIsNone(item['quote']['price'])
        self.assertEqual(item['quote']['trial_price'], 4000.0)

    def test_valuation_present_populates_valued_count(self):
        valuations = {'3661': {'id': 'abc', 'sweet': 100, 'add': 110, 'buy': 120,
                               'method': 'forward_pe', 'reason': 'x', 'thesis': 'y',
                               'as_of': self.now.isoformat(), 'valid_until': (self.now+timedelta(days=1)).isoformat(),
                               'sources': [], 'evidence_reviewed': True}}
        out = build_radar_json(instruments=self.instruments, quotes={}, decisions={},
                               valuations=valuations, research={}, health=[], now=self.now)
        self.assertEqual(out['coverage']['valued'], 1)
        item = next(i for i in out['items'] if i['symbol'] == '3661')
        self.assertEqual(item['valuation']['buy'], 120)

    def test_health_passed_through(self):
        health = [{'name': '試撮', 'status': 'blocked', 'detail': '尚未取得', 'as_of': None}]
        out = build_radar_json(instruments=[], quotes={}, decisions={}, valuations={},
                               research={}, health=health, now=self.now)
        self.assertEqual(out['health'], health)

    def test_mode_passthrough_and_simulation_marked(self):
        out = build_radar_json(instruments=[], quotes={}, decisions={}, valuations={},
                               research={}, health=[], mode='simulation', now=self.now)
        self.assertEqual(out['mode'], 'simulation')

    def test_generated_at_and_market_date_iso(self):
        out = build_radar_json(instruments=[], quotes={}, decisions={}, valuations={},
                               research={}, health=[], now=self.now)
        self.assertEqual(out['generated_at'], self.now.isoformat())
        self.assertEqual(out['market_date'], '2026-09-10')

    def test_etf_other_included_and_counted_toward_etfs(self):
        """Per RADAR_DATA_CONTRACT.md, kind includes etf_other (e.g. ETN);
        it must still appear in items and count toward coverage.etfs, not
        be silently dropped from export."""
        instruments = self.instruments + [
            {'symbol': '020000', 'name': '富邦VIX', 'kind': 'etf_other', 'market': 'TSE', 'industry_code': None},
        ]
        out = build_radar_json(instruments=instruments, quotes={}, decisions={},
                               valuations={}, research={}, health=[], now=self.now)
        self.assertEqual(out['coverage']['etfs'], 2)
        item = next(i for i in out['items'] if i['symbol'] == '020000')
        self.assertEqual(item['kind'], 'etf_other')

    def test_research_fields_default_to_none_or_empty_list(self):
        out = build_radar_json(instruments=self.instruments, quotes={}, decisions={},
                               valuations={}, research={}, health=[], now=self.now)
        item = out['items'][0]
        self.assertIsNone(item['research']['thesis'])
        self.assertEqual(item['research']['catalysts'], [])
        self.assertEqual(item['research']['risks'], [])


if __name__ == '__main__':
    unittest.main()
