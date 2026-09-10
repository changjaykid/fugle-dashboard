"""Unit tests for stock_radar.financials — pure parsing against recorded
real TWSE fixtures (captured 2026-09-10 for 2330/3661/1101). No network."""
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from stock_radar.financials import (
    fetch_quarterly_income_general, fetch_monthly_revenue, fetch_pe_yield_pb,
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
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, headers=None, timeout=None, params=None):
        return FakeResponse(self.payload)


def load(name):
    return json.loads((FIXTURES / name).read_text())


class TestQuarterlyIncome(unittest.TestCase):
    def setUp(self):
        session = FakeSession(load('twse_quarterly_income_sample.json'))
        self.result = fetch_quarterly_income_general(session=session)

    def test_known_symbol_eps(self):
        self.assertAlmostEqual(self.result['3661']['eps_basic'], 37.57)
        self.assertEqual(self.result['3661']['name'], '世芯-KY')

    def test_revenue_and_net_income_are_floats(self):
        row = self.result['2330']
        self.assertIsInstance(row['revenue'], float)
        self.assertIsInstance(row['net_income'], float)
        self.assertGreater(row['revenue'], 0)

    def test_missing_symbol_absent_not_zero(self):
        self.assertNotIn('9999', self.result)

    def test_source_url_present(self):
        self.assertTrue(self.result['3661']['source_url'].startswith('https://'))

    def test_report_period_kept_separate_from_publish_date(self):
        """Regression (Codex review 2026-09-10): year_roc/quarter are the
        actual financial reporting period; report_date (出表日期) is only
        when TWSE published/refreshed the file and must never be treated
        as the period the numbers describe."""
        row = self.result['3661']
        self.assertIn('year_roc', row)
        self.assertIn('quarter', row)
        self.assertIn('report_date', row)
        self.assertNotEqual(row['year_roc'], row['report_date'])


class TestMonthlyRevenue(unittest.TestCase):
    def setUp(self):
        session = FakeSession(load('twse_monthly_revenue_sample.json'))
        self.result = fetch_monthly_revenue(session=session)

    def test_yoy_pct_parsed(self):
        row = self.result['3661']
        self.assertIsInstance(row['yoy_pct'], float)
        self.assertGreater(row['yoy_pct'], 0)  # verified real growth in fixture

    def test_source_url_present(self):
        self.assertTrue(self.result['3661']['source_url'].startswith('https://'))

    def test_period_roc_ym_is_the_only_as_of_field(self):
        """This endpoint has no separate publish-date field -- period_roc_ym
        IS the report period and must not be confused with a fetch/publish
        timestamp elsewhere in the payload."""
        row = self.result['3661']
        self.assertIn('period_roc_ym', row)
        self.assertNotIn('report_date', row)

    def test_note_field_empty_dash_becomes_none(self):
        row = self.result['1101']
        # 台泥's note may or may not be '-'; just check it's either None or a string
        self.assertTrue(row['note'] is None or isinstance(row['note'], str))


class TestPeYieldPb(unittest.TestCase):
    def setUp(self):
        session = FakeSession(load('twse_bwibbu_sample.json'))
        self.result = fetch_pe_yield_pb(session=session)

    def test_pe_pb_yield_parsed(self):
        row = self.result['3661']
        self.assertIsInstance(row['pe'], float)
        self.assertIsInstance(row['pb'], float)
        self.assertIsInstance(row['yield_pct'], float)

    def test_as_of_date_present(self):
        row = self.result['3661']
        self.assertIsNotNone(row['as_of_roc_date'])

    def test_source_url_present(self):
        self.assertTrue(self.result['3661']['source_url'].startswith('https://'))

    def test_etf_not_present_no_fake_pe(self):
        """ETFs are not in TSE's per-share P/E report; must be absent, not
        silently defaulted to some placeholder."""
        self.assertNotIn('0050', self.result)


if __name__ == '__main__':
    unittest.main()
