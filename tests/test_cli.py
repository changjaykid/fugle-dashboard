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

    def test_simulation_mode_without_test_flag_refused(self):
        p = self.make_radar(mode='simulation')
        with self.assertRaises(SystemExit):
            self.run_cli('notify-summary', '--radar-json', str(p))

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
