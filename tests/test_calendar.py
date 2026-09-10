"""Unit tests for stock_radar.calendar — pure parsing/logic against a
recorded real TWSE holidaySchedule fixture (captured 2026-09-10). No
network."""
import sys
import json
from pathlib import Path
from datetime import date
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from stock_radar.calendar import fetch_holiday_schedule, is_trading_day, _roc_to_iso

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

    def get(self, url, headers=None, timeout=None):
        return FakeResponse(self.payload)


def load_fixture():
    return json.loads((FIXTURES / 'twse_holiday_schedule_sample.json').read_text())


class TestRocToIso(unittest.TestCase):
    def test_known_conversion(self):
        self.assertEqual(_roc_to_iso('1150101'), '2026-01-01')
        self.assertEqual(_roc_to_iso('1150227'), '2026-02-27')


class TestFetchHolidaySchedule(unittest.TestCase):
    def setUp(self):
        self.schedule = fetch_holiday_schedule(session=FakeSession(load_fixture()))

    def test_roc_years_captured(self):
        self.assertEqual(self.schedule['roc_years'], [115])

    def test_actual_holiday_included(self):
        self.assertIn('2026-01-01', self.schedule['closed_dates'])
        self.assertIn('2026-02-12', self.schedule['closed_dates'])  # 市場無交易，僅辦理結算交割作業

    def test_adjacent_trading_day_rows_excluded_from_closed_dates(self):
        """Regression: rows describing 'last trading day before holiday'
        or 'first trading day after holiday' name/date an actual TRADING
        day, not a closure -- they must not end up in closed_dates."""
        self.assertNotIn('2026-01-02', self.schedule['closed_dates'])   # 國曆新年開始交易日
        self.assertNotIn('2026-02-11', self.schedule['closed_dates'])   # 農曆春節前最後交易日
        self.assertNotIn('2026-02-23', self.schedule['closed_dates'])   # 農曆春節後開始交易日

    def test_makeup_holiday_with_explicit_description_included(self):
        self.assertIn('2026-02-27', self.schedule['closed_dates'])  # 和平紀念日補假


class TestIsTradingDay(unittest.TestCase):
    def setUp(self):
        self.schedule = fetch_holiday_schedule(session=FakeSession(load_fixture()))

    def test_known_holiday_is_closed(self):
        self.assertFalse(is_trading_day(date(2026, 1, 1), self.schedule))

    def test_ordinary_weekday_not_in_feed_is_open(self):
        # 2026-01-05 is a plain Monday, not in the holiday feed at all
        self.assertTrue(is_trading_day(date(2026, 1, 5), self.schedule))

    def test_weekend_closed_even_though_not_in_feed(self):
        """The feed only lists holiday/make-up days, not routine weekends
        -- is_trading_day must independently close Sat/Sun."""
        # 2026-01-03 is a Saturday, 01-04 is Sunday; neither is in the fixture
        self.assertFalse(is_trading_day(date(2026, 1, 3), self.schedule))
        self.assertFalse(is_trading_day(date(2026, 1, 4), self.schedule))

    def test_makeup_holiday_closed(self):
        self.assertFalse(is_trading_day(date(2026, 2, 27), self.schedule))

    def test_adjacent_trading_day_row_is_open(self):
        self.assertTrue(is_trading_day(date(2026, 2, 23), self.schedule))  # 農曆春節後開始交易日

    def test_uncovered_year_raises_instead_of_guessing(self):
        """Fail closed on missing data: a date whose ROC year isn't in the
        cached schedule must raise, not silently be treated as open."""
        with self.assertRaises(ValueError):
            is_trading_day(date(2027, 1, 1), self.schedule)

    def test_empty_schedule_raises(self):
        with self.assertRaises(ValueError):
            is_trading_day(date(2026, 1, 5), {'closed_dates': {}, 'roc_years': []})


if __name__ == '__main__':
    unittest.main()
