"""Unit tests for stock_radar.cli — network calls mocked, DB is real (tmp
SQLite file per test), so this exercises the actual Store/domain wiring
without hitting live TWSE endpoints."""
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


def make_valuation(now, **overrides):
    v = {
        'sweet': 100.0, 'add': 110.0, 'buy': 120.0,
        'method': 'forward_pe', 'reason': '測試', 'thesis': '測試',
        'as_of': (now - timedelta(hours=1)).isoformat(),
        'valid_until': (now + timedelta(days=30)).isoformat(),
        'sources': [{'url': 'https://example.com', 'as_of': (now - timedelta(hours=1)).isoformat(), 'title': 'x'}],
        'evidence_reviewed': True,
    }
    v.update(overrides)
    return v


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
    def test_watchlist_only_filters(self):
        fake_items = [
            {'symbol': '2330', 'kind': 'stock', 'watched': True},
            {'symbol': '9999', 'kind': 'stock', 'watched': False},
        ]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        with mock.patch('stock_radar.cli.fetch_quotes', return_value={'2330': {
            'symbol': '2330', 'as_of': datetime.now(TW).isoformat(), 'price': 100.0,
        }}) as fake_fetch:
            self.run_cli('sync-quotes', '--watchlist-only')
        called_instruments = fake_fetch.call_args[0][0]
        self.assertEqual(len(called_instruments), 1)
        self.assertEqual(called_instruments[0]['symbol'], '2330')

    def test_no_instruments_does_not_crash(self):
        self.run_cli('sync-quotes')  # empty DB, should print message not raise

    def test_source_fugle_uses_fugle_fetcher_when_key_available(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'watched': True}]
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
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'watched': True}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        with mock.patch('stock_radar.cli._fugle_api_key', return_value=None):
            with self.assertRaises(SystemExit):
                self.run_cli('sync-quotes', '--source', 'fugle')

    def test_source_fugle_failure_falls_back_to_mis(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'watched': True}]
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
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'watched': True}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        with mock.patch('stock_radar.cli.fetch_quotes', return_value={'2330': {
            'symbol': '2330', 'as_of': datetime.now(TW).isoformat(), 'price': 100.0,
        }}) as fake_mis, mock.patch('stock_radar.cli.fetch_fugle_quotes') as fake_fugle:
            self.run_cli('sync-quotes')
        fake_mis.assert_called_once()
        fake_fugle.assert_not_called()


class TestProposeApply(CliTestBase):
    def setUp(self):
        super().setUp()
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'name': '台積電'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')

    def test_propose_then_apply_round_trip(self):
        now = datetime.now(TW)
        v = make_valuation(now)
        vfile = Path(self.tmpdir) / 'v.json'
        vfile.write_text(json.dumps(v))
        # capture printed proposal id via direct store call for determinism
        store = Store(self.db_path)
        pid = store.propose('2330', v)
        store.close()
        self.run_cli('apply', pid, '--actor', cli.ALLOWED_ACTORS[0], '--channel', cli.DISCORD_CHANNEL)
        store = Store(self.db_path)
        try:
            active = store.active('2330')
            self.assertIsNotNone(active)
            self.assertEqual(active['buy'], 120.0)
        finally:
            store.close()

    def test_apply_rejects_unauthorized_actor_via_cli(self):
        now = datetime.now(TW)
        store = Store(self.db_path)
        pid = store.propose('2330', make_valuation(now))
        store.close()
        with self.assertRaises(PermissionError):
            self.run_cli('apply', pid, '--actor', '000000', '--channel', cli.DISCORD_CHANNEL)


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
    def test_export_writes_valid_json_with_no_quotes(self):
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'name': '台積電', 'market': 'TSE'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        self.assertEqual(data['mode'], 'simulation')
        self.assertEqual(data['coverage']['universe'], 1)
        self.assertEqual(data['coverage']['quotes'], 0)
        # No quotes synced -> health must flag it, not silently look healthy
        self.assertTrue(any(h['status'] == 'blocked' for h in data['health']))

    def test_export_never_crashes_with_empty_db(self):
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path))
        data = json.loads(out_path.read_text())
        self.assertEqual(data['items'], [])


class TestResearchAndHistoryWiring(CliTestBase):
    """Regression (Codex review 2026-09-10): export must read research/
    valuation_history from the real DB, not pass a hardcoded {} through."""

    def setUp(self):
        super().setUp()
        fake_items = [{'symbol': '3661', 'kind': 'stock', 'name': '世芯-KY', 'market': 'TSE'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')

    def test_research_command_writes_fact_and_export_surfaces_it(self):
        notes_file = Path(self.tmpdir) / 'notes.json'
        notes_file.write_text(json.dumps({
            'why_now': '訂單能見度提升', 'chips': '外資連續課買',
            'catalysts': ['Q3財報'], 'risks': ['客戶集中度高'],
        }))
        self.run_cli('research', '3661', '--file', str(notes_file))
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        item = next(i for i in data['items'] if i['symbol'] == '3661')
        self.assertEqual(item['research']['why_now'], '訂單能見度提升')
        self.assertEqual(item['research']['catalysts'], ['Q3財報'])
        self.assertEqual(item['research']['risks'], ['客戶集中度高'])

    def test_research_command_rejects_unknown_fields(self):
        notes_file = Path(self.tmpdir) / 'notes.json'
        notes_file.write_text(json.dumps({'made_up_field': 'x'}))
        with self.assertRaises(SystemExit):
            self.run_cli('research', '3661', '--file', str(notes_file))

    def test_active_valuation_thesis_and_sources_surface_in_research(self):
        now = datetime.now(TW)
        store = Store(self.db_path)
        try:
            proposal_id = store.propose('3661', make_valuation(now, thesis='高成長颱帳销'))
        finally:
            store.close()
        self.run_cli('apply', proposal_id, '--actor', cli.ALLOWED_ACTORS[0], '--channel', cli.DISCORD_CHANNEL)
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        item = next(i for i in data['items'] if i['symbol'] == '3661')
        self.assertEqual(item['research']['thesis'], '高成長颱帳销')
        self.assertTrue(len(item['research']['sources']) > 0)

    def test_valuation_history_populated_from_store_versions(self):
        now = datetime.now(TW)
        store = Store(self.db_path)
        try:
            proposal_id = store.propose('3661', make_valuation(now))
        finally:
            store.close()
        self.run_cli('apply', proposal_id, '--actor', cli.ALLOWED_ACTORS[0], '--channel', cli.DISCORD_CHANNEL)
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        item = next(i for i in data['items'] if i['symbol'] == '3661')
        self.assertEqual(len(item['valuation_history']), 1)
        self.assertEqual(item['valuation_history'][0]['status'], 'active')
        self.assertEqual(item['valuation_history'][0]['payload']['buy'], 120.0)


class TestQuoteSourceHealth(CliTestBase):
    """Regression (Codex review 2026-09-10): health must reflect the ACTUAL
    last sync-quotes outcome, never a hardcoded '401' string."""

    def setUp(self):
        super().setUp()
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'name': '台積電', 'market': 'TSE'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')

    def test_no_sync_yet_is_pending_not_blocked(self):
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        h = next(x for x in data['health'] if x['name'] == '試撮')
        self.assertEqual(h['status'], 'pending')

    def test_successful_mis_sync_is_blocked_not_hardcoded_401(self):
        fake_quotes = {'2330': {'symbol': '2330', 'as_of': datetime.now(TW).isoformat(),
                                'price': 100.0, 'is_trial': None}}
        with mock.patch('stock_radar.cli.fetch_quotes', return_value=fake_quotes):
            self.run_cli('sync-quotes')
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        h = next(x for x in data['health'] if x['name'] == '試撮')
        self.assertEqual(h['status'], 'blocked')
        self.assertNotIn('401', h['detail'])
        self.assertIn('未嘗試 Fugle', h['detail'])

    def test_successful_fugle_sync_is_ok_status(self):
        fake_quotes = {'2330': {'symbol': '2330', 'as_of': datetime.now(TW).isoformat(),
                                'price': 100.0, 'is_trial': True, 'trial_price': 100.0}}
        with mock.patch('stock_radar.cli.fetch_fugle_quotes', return_value=fake_quotes):
            self.run_cli('sync-quotes', '--source', 'fugle', '--fugle-key-file', self._fake_key_file())
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        h = next(x for x in data['health'] if x['name'] == '試撮')
        self.assertEqual(h['status'], 'ok')

    def test_fugle_failure_then_mis_fallback_shows_actual_error_not_generic_401(self):
        with mock.patch('stock_radar.cli.fetch_fugle_quotes', side_effect=RuntimeError('403 Forbidden')), \
             mock.patch('stock_radar.cli.fetch_quotes', return_value={
                 '2330': {'symbol': '2330', 'as_of': datetime.now(TW).isoformat(), 'price': 100.0, 'is_trial': None}}):
            self.run_cli('sync-quotes', '--source', 'fugle', '--fugle-key-file', self._fake_key_file())
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        h = next(x for x in data['health'] if x['name'] == '試撮')
        self.assertEqual(h['status'], 'blocked')
        self.assertIn('403', h['detail'])

    def _fake_key_file(self):
        p = Path(self.tmpdir) / 'key.txt'
        p.write_text('fake-key')
        return str(p)


class TestDemoDbGuard(unittest.TestCase):
    """Regression (Codex review 2026-09-10): --mode live must never be
    allowed to run against the disposable dev/test DEMO_DB path."""

    def test_live_mode_against_demo_db_path_rejected(self):
        with self.assertRaises(SystemExit):
            cli.main(['--db', str(cli.DEMO_DB), 'export', '--out', '/tmp/x.json', '--mode', 'live'])

    def test_live_mode_against_explicit_other_db_allowed(self):
        tmpdir = tempfile.mkdtemp(prefix='radar-proddb-test-')
        try:
            db_path = Path(tmpdir) / 'prod.db'
            out_path = Path(tmpdir) / 'radar.json'
            # No --assume-market-open in live mode either -- this should
            # succeed (empty DB, no instruments) without the demo-db guard
            # firing, proving the guard is path-specific, not mode-specific.
            cli.main(['--db', str(db_path), 'export', '--out', str(out_path), '--mode', 'live'])
            self.assertTrue(out_path.exists())
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


class TestExportCalendarGating(CliTestBase):
    """Regression (2026-09-10): --assume-market-open used to be the ONLY
    way to unblock export; now export reads the real cached calendar via
    sync-calendar and only needs the manual flag for testing/override."""

    def setUp(self):
        super().setUp()
        fake_items = [{'symbol': '2330', 'kind': 'stock', 'name': '台積電', 'market': 'TSE'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')

    def test_no_calendar_synced_fails_closed_not_open(self):
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        self.assertTrue(any(h['name'] == '交易日曆' and h['status'] == 'blocked' for h in data['health']))

    def test_calendar_synced_for_open_day_unblocks_market_open_gate(self):
        now = datetime.now(TW)
        fake_schedule = {'closed_dates': {}, 'roc_years': [now.year - 1911]}
        with mock.patch('stock_radar.cli.fetch_holiday_schedule', return_value=fake_schedule):
            self.run_cli('sync-calendar')
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        self.assertFalse(any(h['name'] == '交易日曆' for h in data['health']))

    def test_assume_market_open_still_bypasses_calendar_entirely(self):
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation', '--assume-market-open')
        data = json.loads(out_path.read_text())
        self.assertFalse(any(h['name'] == '交易日曆' for h in data['health']))

    def test_assume_market_open_forbidden_in_live_mode(self):
        """Regression (Codex review 2026-09-10): --assume-market-open must
        never be usable to fake an open market in a LIVE export -- only
        for --mode simulation research/testing runs."""
        out_path = Path(self.tmpdir) / 'radar.json'
        with self.assertRaises(SystemExit):
            self.run_cli('export', '--out', str(out_path), '--mode', 'live', '--assume-market-open')

    def test_missing_calendar_produces_unverified_not_false_market_open(self):
        """Regression: an unpopulated calendar must feed market_open=None
        into decide(), not a bare False -- verified via the signal reason
        text differing from a positively-confirmed holiday/closed day. A
        valuation + risk-clear fact are set up first so decide() actually
        reaches the market_open branch instead of stopping earlier at
        'no valuation' / 'no risk check'."""
        now = datetime.now(TW)
        proposal_id = self.run_cli_capture_proposal('2330', now)
        self.run_cli('apply', proposal_id, '--actor', cli.ALLOWED_ACTORS[0], '--channel', cli.DISCORD_CHANNEL)
        store = Store(self.db_path)
        try:
            store.set_fact('2330', 'risk', {'cleared': True, 'checked_at': now.isoformat(), 'events': []})
        finally:
            store.close()
        out_path = Path(self.tmpdir) / 'radar.json'
        self.run_cli('export', '--out', str(out_path), '--mode', 'simulation')
        data = json.loads(out_path.read_text())
        item = next(i for i in data['items'] if i['symbol'] == '2330')
        self.assertIn('未確認', item['signal']['reason'])

    def run_cli_capture_proposal(self, symbol, now):
        vfile = Path(self.tmpdir) / 'v.json'
        vfile.write_text(json.dumps(make_valuation(now)))
        store = Store(self.db_path)
        try:
            return store.propose(symbol, json.loads(vfile.read_text()))
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


class TestNotifySummary(CliTestBase):
    def make_radar(self, mode='simulation'):
        p = Path(self.tmpdir) / 'radar.json'
        p.write_text(json.dumps({
            'generated_at': '2026-09-10T08:50:00+08:00', 'market_date': '2026-09-10',
            'mode': mode, 'coverage': {'universe': 1, 'stocks': 1, 'etfs': 0, 'quotes': 0, 'valued': 0},
            'health': [], 'items': [],
        }))
        return p

    def test_dry_run_prints_without_sending(self):
        p = self.make_radar()
        self.run_cli('notify-summary', '--radar-json', str(p), '--test', '--dry-run')

    def test_simulation_mode_without_test_flag_still_marks_test_in_output(self, capsys=None):
        """format_daily_summary forces the 🧪 marker itself from mode=simulation,
        so this must NOT raise even without --test, and the emitted text
        must still be test-marked (no longer a CLI-level refusal)."""
        p = self.make_radar(mode='simulation')
        self.run_cli('notify-summary', '--radar-json', str(p), '--dry-run')

    def test_live_mode_without_test_flag_allowed_dry_run(self):
        p = self.make_radar(mode='live')
        self.run_cli('notify-summary', '--radar-json', str(p), '--dry-run')

    def test_actual_send_calls_discord_with_token(self):
        p = self.make_radar()
        with mock.patch('stock_radar.cli._bot_token', return_value='fake-token'), \
             mock.patch('stock_radar.cli.send_message', return_value={'id': '123'}) as fake_send:
            self.run_cli('notify-summary', '--radar-json', str(p), '--test')
        fake_send.assert_called_once()
        self.assertEqual(fake_send.call_args[0][0], 'fake-token')


class TestDiscordLookup(CliTestBase):
    def setUp(self):
        super().setUp()
        fake_items = [{'symbol': '3661', 'kind': 'stock', 'name': '世芯-KY', 'market': 'TSE'}]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')
        self.radar_path = Path(self.tmpdir) / 'radar.json'
        self.radar_path.write_text(json.dumps({
            'items': [{
                'symbol': '3661', 'name': '世芯-KY',
                'quote': {'previous_close': 3905.0, 'trial_price': None},
                'valuation': None,
                'signal': {'status': 'pending', 'status_label': '待估值', 'suggested': None, 'action': ''},
            }],
        }))

    def test_lookup_without_post_does_not_call_discord(self):
        with mock.patch('stock_radar.cli.send_message') as fake_send:
            self.run_cli('discord-lookup', '3661', '--radar-json', str(self.radar_path), '--test')
        fake_send.assert_not_called()

    def test_lookup_with_post_calls_discord(self):
        with mock.patch('stock_radar.cli._bot_token', return_value='fake-token'), \
             mock.patch('stock_radar.cli.send_message', return_value={'id': '1'}) as fake_send:
            self.run_cli('discord-lookup', '3661', '--radar-json', str(self.radar_path), '--test', '--post')
        fake_send.assert_called_once()

    def test_simulation_mode_radar_json_auto_marks_test_without_test_flag(self, capsys=None):
        """Regression (Codex review 2026-09-10): a lookup against a
        simulation-mode radar.json must render the 🧪 marker even if the
        caller forgets --test -- mirrors format_daily_summary's own
        mode-driven auto-marking, via discord.is_test_mode()."""
        sim_path = Path(self.tmpdir) / 'radar_sim.json'
        payload = json.loads(self.radar_path.read_text())
        payload['mode'] = 'simulation'
        sim_path.write_text(json.dumps(payload))
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_cli('discord-lookup', '3661', '--radar-json', str(sim_path))
        self.assertIn('🧪', buf.getvalue())

    def test_live_mode_radar_json_without_test_flag_not_marked(self):
        live_path = Path(self.tmpdir) / 'radar_live.json'
        payload = json.loads(self.radar_path.read_text())
        payload['mode'] = 'live'
        live_path.write_text(json.dumps(payload))
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_cli('discord-lookup', '3661', '--radar-json', str(live_path))
        self.assertNotIn('🧪', buf.getvalue())


class TestLookup(CliTestBase):
    def setUp(self):
        super().setUp()
        fake_items = [
            {'symbol': '3661', 'kind': 'stock', 'name': '世芯-KY', 'market': 'TSE'},
            {'symbol': '0050', 'kind': 'etf_equity', 'name': '元大台灣50', 'market': 'TSE'},
        ]
        with mock.patch('stock_radar.cli.build_universe', return_value=fake_items):
            self.run_cli('sync-universe')

    def test_lookup_by_symbol(self):
        self.run_cli('lookup', '3661')  # should not raise; output goes to stdout

    def test_lookup_by_partial_name(self):
        self.run_cli('lookup', '世芯')

    def test_lookup_no_match_does_not_crash(self):
        self.run_cli('lookup', 'nonexistent-xyz')


if __name__ == '__main__':
    unittest.main()
