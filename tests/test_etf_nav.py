from datetime import datetime
import unittest
from stock_radar.etf_nav import parse_0050
from stock_radar.domain import TW

NOW=datetime(2026,9,10,22,tzinfo=TW)
# Synthetic, no price assumption. Literal SSR format is independently live checked.
PAGE='fileLinkData:{FUND_SH_NM:"測試基金(0050)",NAV_DATE:"2026\\u002F09\\u002F09",NAV:"100.25",FUND_CURRENCY:"NTD"},tagList:[]'
class Nav(unittest.TestCase):
 def test_literal_source_date_not_fetch_date(self):
  q=parse_0050(PAGE,now=NOW);self.assertEqual(q['nav_date'],'2026-09-09');self.assertEqual(q['nav'],100.25);self.assertEqual(q['age_calendar_days'],1)
 def test_unknown_structure_is_error(self):
  with self.assertRaises(ValueError):parse_0050('captcha',now=NOW)
 def test_mismatch_or_future_or_nonfinite_rejected(self):
  for a,b in [('0050','0056'),('NTD','USD'),('09",NAV','11",NAV'),('100.25','NaN')]:
   with self.subTest(b=b),self.assertRaises(ValueError):parse_0050(PAGE.replace(a,b),now=NOW)
 def test_no_execution_of_script(self):
  with self.assertRaises(ValueError):parse_0050(PAGE.replace('NAV:"100.25"','NAV:alert(1)'),now=NOW)
 def test_ambiguous_block_rejected(self):
  with self.assertRaises(ValueError):parse_0050(PAGE+PAGE,now=NOW)
