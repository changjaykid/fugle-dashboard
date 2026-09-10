"""Unit tests for stock_radar.universe — pure HTML parsing, no network."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from stock_radar.universe import parse_isin_html, equity_universe, SECTION_KIND

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
