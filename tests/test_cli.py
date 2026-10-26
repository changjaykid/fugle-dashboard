"""Unit tests for stock_radar.cli — network calls mocked, DB is real (tmp
SQLite file per test), so this exercises the actual Store/domain wiring
without hitting live TWSE or Discord."""
import sys
import json
import tempfile
import shutil
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from datetime import datetime, timedelta

from stock_radar import cli
from stock_radar.calendar import fetch_holiday_schedule
from stock_radar.domain import TW
from stock_radar.store import Store


class CliTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='radar-cli-test-')
        self.db_path = Path(self.tmpdir) / 'test.db'

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_cli(self, *args):
        return cli.main(['--db', str(self.db_path), *args])


class TestSyncUniverse(CliTestBase):
    def test_sync_universe_upserts_instruments(self):
        fake_items = [
            {'symbol': '2330', 'name': '台積電', 'isin': 'x', 'listed_date': 'x',
             'market': 'TSE', 'section': '股票', 'industry_code': '半導體業', 'cfi': 'ESVUFR', 'kind': 'stock'},
            {'symbol': '0050', 'name': '元大台灣50', 'isin': 'x', 'listed_date': 'x',
             'market': 'TSE', 'section': 'ETF', 'industry_code': None, 'cfi': 'CEOGEU', 'kind': 'etf_equity'},
        ]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        store = Store(self.db_path)
        try:
            symbols = {i['symbol'] for i in store.instruments()}
            self.assertEqual(symbols, {'2330', '0050'})
        finally:
            store.close()


class TestSyncQuotes(CliTestBase):

    def test_no_instruments_does_not_crash(self):
        self.run_cli('sync-quotes')  # empty DB, should print message not raise

    def test_source_fugle_uses_fugle_fetcher_when_key_available(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        keyfile = Path(self.tmpdir) / 'key.txt'
        keyfile.write_text('fake-fugle-key')
        with mock.patch('stock_radar.cli.fetch_fugle_quotes', return_value={'2330': {
            'symbol': '2330', 'as_of': datetime.now(TW).isoformat(), 'price': 100.0,
        }}) as fake_fugle, mock.patch('stock_radar.cli.fetch_quotes') as fake_mis:
            self.run_cli('sync-quotes', '--source', 'fugle', '--fugle-key-file', str(keyfile))
        fake_fugle.assert_called_once()
        fake_mis.assert_not_called()

    def test_source_fugle_without_key_refuses(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        with mock.patch('stock_radar.cli._fugle_api_key', return_value=None):
            with self.assertRaises(SystemExit):
                self.run_cli('sync-quotes', '--source', 'fugle')

    def test_source_fugle_failure_falls_back_to_mis(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        keyfile = Path(self.tmpdir) / 'key.txt'
        keyfile.write_text('fake-fugle-key')
        with mock.patch('stock_radar.cli.fetch_fugle_quotes', side_effect=RuntimeError('401')), \
             mock.patch('stock_radar.cli.fetch_quotes', return_value={'2330': {
                'symbol': '2330', 'as_of': datetime.now(TW).isoformat(), 'price': 100.0,
             }}) as fake_mis:
            self.run_cli('sync-quotes', '--source', 'fugle', '--fugle-key-file', str(keyfile))
        fake_mis.assert_called_once()

    def test_default_source_is_mis_untouched(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        with mock.patch('stock_radar.cli.fetch_quotes', return_value={'2330': {
            'symbol': '2330', 'as_of': datetime.now(TW).isoformat(), 'price': 100.0,
        }}) as fake_mis, mock.patch('stock_radar.cli.fetch_fugle_quotes') as fake_fugle:
            self.run_cli('sync-quotes')
        fake_mis.assert_called_once()
        fake_fugle.assert_not_called()

    def _fugle_lock_path(self):
        return Path(self.tmpdir) / 'fugle_sync.lock'

    def test_fugle_above_candidate_cap_refused_without_override(self):
        """Regression (Codex review 2026-09-10): --source fugle against more
        than FUGLE_CANDIDATE_CAP symbols must refuse loudly (undocumented
        free-tier quota), not silently fetch a partial/truncated result."""
        fake_items = [{'symbol': str(1000 + i), 'kind': 'stock'}
                     for i in range(cli.FUGLE_CANDIDATE_CAP + 1)]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        keyfile = Path(self.tmpdir) / 'key.txt'
        keyfile.write_text('fake-fugle-key')
        with mock.patch('stock_radar.cli.fetch_fugle_quotes') as fake_fugle, \
             mock.patch('stock_radar.cli.FUGLE_LOCK_PATH', self._fugle_lock_path()):
            with self.assertRaises(SystemExit):
                self.run_cli('sync-quotes', '--source', 'fugle', '--fugle-key-file', str(keyfile))
        fake_fugle.assert_not_called()

    def test_fugle_above_cap_allowed_with_explicit_override(self):
        fake_items = [{'symbol': str(1000 + i), 'kind': 'stock'}
                     for i in range(cli.FUGLE_CANDIDATE_CAP + 1)]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        keyfile = Path(self.tmpdir) / 'key.txt'
        keyfile.write_text('fake-fugle-key')
        with mock.patch('stock_radar.cli.fetch_fugle_quotes', return_value={}) as fake_fugle, \
             mock.patch('stock_radar.cli.FUGLE_LOCK_PATH', self._fugle_lock_path()):
            self.run_cli('sync-quotes', '--source', 'fugle', '--fugle-key-file', str(keyfile), '--allow-full-market-fugle')
        fake_fugle.assert_called_once()

    def test_fugle_within_cap_uses_single_flight_lock(self):
        """Verifies the lock is actually exercised (not silently skipped)
        by asserting a second concurrent call to the same lock path is
        rejected while the first is 'in progress' (simulated by holding
        the lock manually before calling sync-quotes)."""
        fake_items = [{'symbol': '2330', 'kind': 'stock'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        keyfile = Path(self.tmpdir) / 'key.txt'
        keyfile.write_text('fake-fugle-key')
        lock_path = self._fugle_lock_path()
        from stock_radar.runtime import single_flight_lock
        with single_flight_lock(lock_path):
            with mock.patch('stock_radar.cli.fetch_fugle_quotes') as fake_fugle, \
                 mock.patch('stock_radar.cli.fetch_quotes', return_value={'2330': {
                    'symbol': '2330', 'as_of': datetime.now(TW).isoformat(), 'price': 100.0,
                 }}) as fake_mis, \
                 mock.patch('stock_radar.cli.FUGLE_LOCK_PATH', lock_path):
                self.run_cli('sync-quotes', '--source', 'fugle', '--fugle-key-file', str(keyfile))
        fake_fugle.assert_not_called()  # lock was busy -> never even attempted the HTTP calls
        fake_mis.assert_called_once()   # fell back cleanly


class TestSyncRisk(CliTestBase):
    def test_sync_risk_populates_facts(self):
        fake_items = [
            {'symbol': '2330', 'kind': 'stock', 'market': 'TSE', 'name': '台積電'},
        ]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        with mock.patch('stock_radar.cli.build_risk_facts', return_value={
            '2330': {'cleared': True, 'checked_at': datetime.now(TW).isoformat(), 'events': []},
        }):
            self.run_cli('sync-risk')
        store = Store(self.db_path)
        try:
            fact = store.fact('2330', 'risk')
            self.assertTrue(fact['cleared'])
        finally:
            store.close()

    def test_sync_risk_no_instruments_does_not_crash(self):
        self.run_cli('sync-risk')


class TestExport(CliTestBase):
    """The page payload: the scan, the account and data health -- nothing else."""

    def export(self, mode='simulation'):
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', mode)
        return json.loads(out_path.read_text())

    def test_an_empty_database_exports_no_scan_and_says_the_data_is_missing(self):
        data = self.export()
        self.assertEqual(set(data), {'schema_version', 'mode', 'generated_at', 'market_date', 'health', 'paper', 'scan'})
        self.assertIsNone(data['scan']);self.assertIsNone(data['paper'])
        health = {h['name']: h['status'] for h in data['health']}
        self.assertEqual(health, {'交易日曆': 'blocked', '公開收盤': 'blocked'})

    def test_the_scan_and_the_public_close_reach_the_page(self):
        store = Store(self.db_path)
        try:
            store.set_meta('trading_calendar', {'roc_years': [datetime.now(TW).year - 1911], 'closed_dates': {}})
            store.set_meta('public_close_sync', {'at': '2026-10-20T14:30:00+08:00', 'count': 1})
            store.set_meta('daily_scan', {'version': 'swing-v1', 'day': '2026-10-20', 'market_open': True,
                                          'candidates': [], 'held_back': [], 'near': [], 'tally': {}})
        finally:
            store.close()
        data = self.export()
        self.assertEqual(data['scan']['version'], 'swing-v1')
        self.assertEqual([h['name'] for h in data['health']], ['公開收盤'])


class TestOutbox(CliTestBase):
    NOW = datetime(2026, 9, 29, 8, 55, tzinfo=TW)

    def send(self, store, **kw):
        return cli._send_with_outbox(store, 't', 'hello', channel='c', now=self.NOW, sleep=lambda s: None, **kw)

    def test_a_skipped_duplicate_still_reports_the_original_message_id(self):
        """The morning heartbeat asks "did today's message go out?". A duplicate means it did,
        so the id of the message already in the channel must come back with the skip."""
        store = Store(self.db_path)
        try:
            with mock.patch('stock_radar.cli.send_message', return_value={'id': '999'}):
                first = self.send(store)
            second = self.send(store)
        finally:
            store.close()
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(second['status'], 'skipped_duplicate')
        self.assertEqual(second['message_id'], '999')

    def test_a_transient_failure_is_retried(self):
        store = Store(self.db_path)
        try:
            with mock.patch('stock_radar.cli.send_message', side_effect=[RuntimeError('503'), {'id': '7'}]):
                r = self.send(store)
        finally:
            store.close()
        self.assertEqual((r['status'], r['attempts'], r['message_id']), ('sent', 2, '7'))

    def test_failure_on_every_attempt_raises(self):
        store = Store(self.db_path)
        try:
            with mock.patch('stock_radar.cli.send_message', side_effect=RuntimeError('503')) as fake:
                with self.assertRaises(RuntimeError):
                    self.send(store, max_attempts=3)
        finally:
            store.close()
        self.assertEqual(fake.call_count, 3)

class TestDemoDbGuard(unittest.TestCase):
    """Regression (Codex review 2026-09-10): --mode live must never be
    allowed to run against the disposable dev/test DEMO_DB path."""

    def test_live_mode_against_demo_db_path_rejected(self):
        with self.assertRaises(SystemExit):
            cli.main(['--db', str(cli.DEMO_DB), 'export', '--out', '/tmp/x.json', '--mode', 'live'])


    def test_live_mode_against_explicit_other_db_allowed(self):
        tmpdir = tempfile.mkdtemp(prefix='radar-demodb-live-test-')
        try:
            out_path = Path(tmpdir) / 'radar.json'
            cli.main(['--db', str(Path(tmpdir) / 'prod.db'), 'export', '--out', str(out_path), '--mode', 'live'])
            self.assertEqual(json.loads(out_path.read_text())['mode'], 'live')
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_simulation_mode_against_demo_db_path_allowed(self):
        tmpdir = tempfile.mkdtemp(prefix='radar-demodb-sim-test-')
        try:
            out_path = Path(tmpdir) / 'radar.json'
            db_path = Path(tmpdir) / 'not_actually_demo.db'
            # simulate calling with the demo db path itself in simulation mode
            with mock.patch('stock_radar.cli.DEMO_DB', db_path):
                cli.main(['--db', str(db_path), 'export', '--out', str(out_path), '--mode', 'simulation'])
            self.assertTrue(out_path.exists())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestSyncCalendar(CliTestBase):
    def test_sync_calendar_caches_schedule_in_store(self):
        fake_schedule = {'closed_dates': {'2026-01-01': '中華民國開國紀念日'}, 'roc_years': [115]}
        with mock.patch('stock_radar.cli.fetch_holiday_schedule', return_value=fake_schedule):
            self.run_cli('sync-calendar')
        store = Store(self.db_path)
        try:
            self.assertEqual(store.meta('trading_calendar'), fake_schedule)
        finally:
            store.close()


    def test_a_new_year_does_not_drop_the_one_still_being_traded(self):
        this_year = {'closed_dates': {'2026-10-26': '補假', '2026-12-25': '行憲紀念日'}, 'roc_years': [115]}
        next_year = {'closed_dates': {'2027-01-01': '中華民國開國紀念日'}, 'roc_years': [116]}
        with mock.patch('stock_radar.cli.fetch_holiday_schedule', return_value=this_year):
            self.run_cli('sync-calendar')
        with mock.patch('stock_radar.cli.fetch_holiday_schedule', return_value=next_year):
            self.run_cli('sync-calendar')
        store = Store(self.db_path)
        try:
            cached = store.meta('trading_calendar')
        finally:
            store.close()
        self.assertEqual(cached['roc_years'], [115, 116])
        self.assertEqual(set(cached['closed_dates']), {'2026-10-26', '2026-12-25', '2027-01-01'})

    def test_a_refetched_year_replaces_its_own_dates(self):
        first = {'closed_dates': {'2026-10-09': '補假', '2026-10-26': '補假'}, 'roc_years': [115]}
        revised = {'closed_dates': {'2026-10-26': '補假'}, 'roc_years': [115]}
        for schedule in (first, revised):
            with mock.patch('stock_radar.cli.fetch_holiday_schedule', return_value=schedule):
                self.run_cli('sync-calendar')
        store = Store(self.db_path)
        try:
            self.assertEqual(store.meta('trading_calendar'), revised)
        finally:
            store.close()


class TestBackup(CliTestBase):
    def test_backup_creates_file(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'name': '台積電'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        out_path = Path(self.tmpdir) / 'backup.db'
        self.run_cli('backup', '--out', str(out_path))
        self.assertTrue(out_path.exists())

    def test_restore_roundtrip_via_cli(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'name': '台積電'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        backup_path = Path(self.tmpdir) / 'backup.db'
        self.run_cli('backup', '--out', str(backup_path))
        target_path = Path(self.tmpdir) / 'restored.db'
        self.run_cli('restore', '--backup', str(backup_path), '--target', str(target_path))
        store = Store(target_path)
        try:
            self.assertEqual(len(store.instruments()), 1)
        finally:
            store.close()

    def test_restore_without_force_on_nonempty_target_raises(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'name': '台積電'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        backup_path = Path(self.tmpdir) / 'backup.db'
        self.run_cli('backup', '--out', str(backup_path))
        # self.db_path already exists and is non-empty from sync-universe above
        with self.assertRaises(FileExistsError):
            self.run_cli('restore', '--backup', str(backup_path), '--target', str(self.db_path))


if __name__ == '__main__':
    unittest.main()
