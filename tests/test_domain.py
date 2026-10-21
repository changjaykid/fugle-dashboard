"""Unit tests for stock_radar.domain: the parsing every fetcher relies on."""
import unittest

from stock_radar.domain import number, positive, stamp


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


class TestStamp(unittest.TestCase):
    def test_stamp_requires_timezone(self):
        self.assertIsNone(stamp('2026-09-10T10:00:00'))  # naive -> None
        self.assertIsNotNone(stamp('2026-09-10T10:00:00+08:00'))
        self.assertIsNone(stamp(None))
        self.assertIsNone(stamp('not-a-date'))
