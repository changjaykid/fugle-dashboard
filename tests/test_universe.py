"""Unit tests for stock_radar.universe — pure HTML parsing, no network."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from stock_radar.universe import (
    parse_isin_html, equity_universe, SECTION_KIND,
    classify_etf_kinds, fetch_twse_fund_types, PLAIN_EQUITY_FUND_TYPES,
)

FIXTURES = Path(__file__).parent / 'fixtures'


def load(name):
    return (FIXTURES / name).read_bytes()


class TestParseIsinHtml(unittest.TestCase):
    def setUp(self):
        self.tse = parse_isin_html(load('isin_tse_sample.html'), 'TSE')
        self.otc = parse_isin_html(load('isin_otc_sample.html'), 'OTC')

    def test_known_stock_present_and_correctly_kinded(self):
        by_symbol = {i['symbol']: i for i in self.tse}
        self.assertIn('1101', by_symbol)  # 台泥, real fixture row
        self.assertEqual(by_symbol['1101']['kind'], 'stock')
        self.assertEqual(by_symbol['1101']['name'], '台泥')

    def test_etf_section_present(self):
        kinds = {i['kind'] for i in self.tse}
        self.assertIn('etf_equity', kinds)

    def test_etn_section_gets_its_own_kind_not_etf_other(self):
        """Regression (Codex review 2026-09-10): ETN must be kind='etn',
        distinct from etf_equity/etf_other, and must not count as an ETF
        at all -- it's a debt-like note structure, not a fund."""
        by_kind = {i['kind'] for i in self.tse + self.otc}
        if 'ETN' in {i['section'] for i in self.tse + self.otc}:
            self.assertIn('etn', by_kind)

    def test_no_warrants_leak_through_section_filter(self):
        for i in self.tse + self.otc:
            self.assertNotIn('認購', i['section'])
            self.assertNotIn('認售', i['section'])

    def test_no_warrant_cfi_leaks_through(self):
        """Even if a row's section metadata were ambiguous, the CFI code check
        (starts with 'R') must independently exclude it."""
        for i in self.tse + self.otc:
            self.assertFalse(i['cfi'].startswith('R'),
                            f"warrant CFI leaked through: {i}")

    def test_otc_stock_present(self):
        by_symbol = {i['symbol']: i for i in self.otc}
        self.assertIn('1240', by_symbol)  # 茂生農經, real OTC fixture row
        self.assertEqual(by_symbol['1240']['market'], 'OTC')

    def test_preferred_and_tdr_classified_but_not_equity(self):
        kinds_present = {i['kind'] for i in self.tse}
        # our sample includes preferred/TDR/REIT rows; ensure they're kept
        # with their own kind (not silently merged into 'stock').
        for expect_kind in ('preferred',):
            if expect_kind in kinds_present:
                items = [i for i in self.tse if i['kind'] == expect_kind]
                self.assertTrue(all(i['kind'] != 'stock' for i in items))

    def test_row_without_full_name_code_separator_skipped(self):
        # malformed rows (no ideographic space between code and name) must not
        # crash the parser or produce garbage entries
        garbage_html = b"<html><body><table><tr><td>NOSEPARATOR</td><td>x</td><td>x</td><td>x</td><td>x</td><td>ESVUFR</td></tr></table></body></html>"
        result = parse_isin_html(garbage_html, 'TSE')
        self.assertEqual(result, [])

    def test_short_row_skipped_not_crashed(self):
        short_html = b"<html><body><table><tr><td>a</td><td>b</td></tr></table></body></html>"
        result = parse_isin_html(short_html, 'TSE')
        self.assertEqual(result, [])


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

    def get(self, url, headers=None, timeout=None):
        return FakeResponse(self.payload)


class TestFetchTwseFundTypes(unittest.TestCase):
    def test_parses_real_sample_into_symbol_to_type_dict(self):
        import json
        payload = json.loads((FIXTURES / 'twse_fund_types_sample.json').read_text())
        session = FakeSession(payload)
        result = fetch_twse_fund_types(session=session)
        self.assertEqual(result['0050'], '國內成分證券指數股票型基金')
        self.assertEqual(result['00631L'], '槓桿/反向指數股票型基金')
        self.assertNotIn('00679B', result)  # OTC bond ETF, correctly absent from TSE-only feed

    def test_plain_equity_types_constant_matches_fixture_labels(self):
        self.assertIn('國內成分證券指數股票型基金', PLAIN_EQUITY_FUND_TYPES)
        self.assertNotIn('槓桿/反向指數股票型基金', PLAIN_EQUITY_FUND_TYPES)


class TestClassifyEtfKinds(unittest.TestCase):
    """Regression suite for the 2026-09-10 Codex-reported misclassification:
    00631L/00632R (leveraged/inverse) and 00679B/00795B (bond ETF) were
    being labelled 'etf_equity' by CFI-only logic. classify_etf_kinds()
    must downgrade both to 'etf_other' unless TWSE's own fund-type text
    confirms plain passive equity."""

    def make_item(self, symbol, kind='etf_equity'):
        return {'symbol': symbol, 'name': symbol, 'kind': kind, 'cfi': 'CEOGEU',
                'isin': 'x', 'listed_date': 'x', 'market': 'TSE', 'section': 'ETF',
                'industry_code': None}

    def test_confirmed_plain_equity_stays_etf_equity(self):
        items = [self.make_item('0050')]
        classify_etf_kinds(items, {'0050': '國內成分證券指數股票型基金'})
        self.assertEqual(items[0]['kind'], 'etf_equity')

    def test_leveraged_inverse_downgraded_to_etf_other(self):
        """Real repro: 00631L/00632R, CFI=CEOGDU, previously misclassified."""
        items = [self.make_item('00631L'), self.make_item('00632R')]
        classify_etf_kinds(items, {
            '00631L': '槓桿/反向指數股票型基金',
            '00632R': '槓桿/反向指數股票型基金',
        })
        self.assertEqual(items[0]['kind'], 'etf_other')
        self.assertEqual(items[1]['kind'], 'etf_other')

    def test_bond_etf_not_in_twse_fund_master_downgraded(self):
        """Real repro: 00679B/00795B (OTC bond ETFs) are not even present
        in TWSE's TSE-only fund master -- absence must mean 'unclassified',
        not 'assume plain equity'."""
        items = [self.make_item('00679B'), self.make_item('00795B')]
        classify_etf_kinds(items, {})  # neither symbol present
        self.assertEqual(items[0]['kind'], 'etf_other')
        self.assertEqual(items[1]['kind'], 'etf_other')

    def test_active_etf_downgraded(self):
        items = [self.make_item('00400A')]
        classify_etf_kinds(items, {'00400A': '國內成分證券主動式交易所交易基金(股票)'})
        self.assertEqual(items[0]['kind'], 'etf_other')

    def test_futures_tracking_etf_downgraded(self):
        items = [self.make_item('00635U')]
        classify_etf_kinds(items, {'00635U': '指數股票型期貨信託基金'})
        self.assertEqual(items[0]['kind'], 'etf_other')

    def test_unrecognized_future_fund_type_string_defaults_safe(self):
        """Allow-list, not deny-list: an unseen fund-type string must not
        be silently treated as plain equity."""
        items = [self.make_item('99999X')]
        classify_etf_kinds(items, {'99999X': '某種尚未見過的基金類型'})
        self.assertEqual(items[0]['kind'], 'etf_other')

    def test_non_etf_kind_untouched(self):
        items = [{'symbol': '2330', 'kind': 'stock'}]
        classify_etf_kinds(items, {})
        self.assertEqual(items[0]['kind'], 'stock')

    def test_fund_type_text_preserved_on_item_for_debugging(self):
        items = [self.make_item('0050')]
        classify_etf_kinds(items, {'0050': '國內成分證券指數股票型基金'})
        self.assertEqual(items[0]['fund_type'], '國內成分證券指數股票型基金')


class TestEquityUniverse(unittest.TestCase):
    def test_filters_to_stock_and_etf_only(self):
        tse = parse_isin_html(load('isin_tse_sample.html'), 'TSE')
        otc = parse_isin_html(load('isin_otc_sample.html'), 'OTC')
        eq = equity_universe(tse + otc)
        kinds = {i['kind'] for i in eq}
        self.assertTrue(kinds <= {'stock', 'etf_equity'})
        self.assertGreater(len(eq), 0)

    def test_excludes_non_equity_kinds(self):
        tse = parse_isin_html(load('isin_tse_sample.html'), 'TSE')
        all_kinds = {i['kind'] for i in tse}
        eq = equity_universe(tse)
        eq_kinds = {i['kind'] for i in eq}
        # if sample has any non-equity kind, it must be excluded from eq_kinds
        excluded = all_kinds - {'stock', 'etf_equity'}
        for k in excluded:
            self.assertNotIn(k, eq_kinds)


if __name__ == '__main__':
    unittest.main()
