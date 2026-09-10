"""Unit tests for stock_radar.tpex — subprocess curl wrapper, mocked (no
real network / real subprocess needed to run the suite)."""
import sys
import json
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from stock_radar import tpex


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestFetchOtcDailyClose(unittest.TestCase):
    def test_success_parses_rows(self):
        payload = [
            {'SecuritiesCompanyCode': '1240', 'CompanyName': '茂生農經', 'Close': '55.30'},
            {'SecuritiesCompanyCode': '00679B', 'CompanyName': '元大美債20年', 'Close': '30.5'},
        ]
        fake = FakeCompletedProcess(returncode=0, stdout=json.dumps(payload))
        with mock.patch('subprocess.run', return_value=fake) as m:
            result = tpex.fetch_otc_daily_close()
        self.assertEqual(set(result.keys()), {'1240', '00679B'})
        self.assertEqual(result['1240']['Close'], '55.30')
        # verify no -k / --insecure flag ever passed (no SSL bypass)
        called_args = m.call_args[0][0]
        self.assertNotIn('-k', called_args)
        self.assertNotIn('--insecure', called_args)

    def test_nonzero_exit_raises(self):
        fake = FakeCompletedProcess(returncode=22, stdout='', stderr='HTTP 404')
        with mock.patch('subprocess.run', return_value=fake):
            with self.assertRaises(RuntimeError):
                tpex.fetch_otc_daily_close()

    def test_non_json_output_raises(self):
        fake = FakeCompletedProcess(returncode=0, stdout='not json at all')
        with mock.patch('subprocess.run', return_value=fake):
            with self.assertRaises(RuntimeError):
                tpex.fetch_otc_daily_close()

    def test_timeout_raises(self):
        import subprocess as sp
        with mock.patch('subprocess.run', side_effect=sp.TimeoutExpired(cmd='curl', timeout=30)):
            with self.assertRaises(RuntimeError):
                tpex.fetch_otc_daily_close()

    def test_row_missing_code_skipped_not_crashed(self):
        payload = [{'CompanyName': 'no code here'}]
        fake = FakeCompletedProcess(returncode=0, stdout=json.dumps(payload))
        with mock.patch('subprocess.run', return_value=fake):
            result = tpex.fetch_otc_daily_close()
        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
