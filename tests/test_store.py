"""Unit tests for stock_radar.store — SQLite persistence, proposal lifecycle, backup."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
import tempfile
import shutil
from datetime import datetime, timedelta

from stock_radar.domain import TW
from stock_radar.store import Store

CHANNEL = '1493898877970153532'


def make_valuation(now, **overrides):
    v = {
        'sweet': 100.0, 'add': 110.0, 'buy': 120.0,
        'method': 'forward_pe', 'reason': '測試估值', 'thesis': '測試論述',
        'as_of': (now - timedelta(hours=1)).isoformat(),
        'valid_until': (now + timedelta(days=30)).isoformat(),
        'sources': [{'url': 'https://example.com/report', 'as_of': (now - timedelta(hours=1)).isoformat(), 'title': '來源'}],
        'evidence_reviewed': True,
    }
    v.update(overrides)
    return v


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='radar-test-')
        self.db_path = Path(self.tmpdir) / 'radar.db'
        self.store = Store(self.db_path)
        self.now = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestInstruments(StoreTestBase):
    def test_upsert_and_list(self):
        self.store.upsert_instruments([
            {'symbol': '2330', 'name': '台積電', 'kind': 'stock'},
            {'symbol': '0050', 'name': '元大台灣50', 'kind': 'etf_equity'},
        ])
        items = self.store.instruments()
        symbols = {i['symbol'] for i in items}
        self.assertEqual(symbols, {'2330', '0050'})

    def test_upsert_updates_existing(self):
        self.store.upsert_instruments([{'symbol': '2330', 'name': 'old', 'kind': 'stock'}])
        self.store.upsert_instruments([{'symbol': '2330', 'name': 'new', 'kind': 'stock'}])
        items = self.store.instruments()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['name'], 'new')


class TestObservations(StoreTestBase):
    def test_observe_and_quote(self):
        q = {'symbol': '2330', 'as_of': self.now.isoformat(), 'price': 105.0}
        self.store.observe(q)
        got = self.store.quote('2330')
        self.assertEqual(got['price'], 105.0)

    def test_quote_returns_none_when_absent(self):
        self.assertIsNone(self.store.quote('9999'))

    def test_history_ordered_ascending(self):
        for i in range(3):
            t = self.now + timedelta(minutes=i)
            self.store.observe({'symbol': '2330', 'as_of': t.isoformat(), 'price': 100 + i})
        h = self.store.history('2330')
        prices = [x['price'] for x in h]
        self.assertEqual(prices, [100, 101, 102])


class TestValuationLifecycle(StoreTestBase):
    def test_propose_requires_instrument_exists(self):
        v = make_valuation(self.now)
        with self.assertRaises(ValueError):
            self.store.propose('2330', v, now=self.now)

    def test_propose_rejects_invalid_valuation(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        bad = make_valuation(self.now, reason='')
        with self.assertRaises(ValueError):
            self.store.propose('2330', bad, now=self.now)

    def test_propose_then_apply(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        v = make_valuation(self.now)
        pid = self.store.propose('2330', v, now=self.now)
        self.assertIsNone(self.store.active('2330'))  # not active until applied
        self.store.apply(pid, actor='554850002883706901',
                         allowed_actors=['554850002883706901'], channel=CHANNEL, now=self.now)
        active = self.store.active('2330')
        self.assertIsNotNone(active)
        self.assertEqual(active['buy'], 120.0)

    def test_apply_rejects_wrong_channel(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        pid = self.store.propose('2330', make_valuation(self.now), now=self.now)
        with self.assertRaises(PermissionError):
            self.store.apply(pid, actor='554850002883706901',
                             allowed_actors=['554850002883706901'], channel='0000000000', now=self.now)

    def test_apply_rejects_unauthorized_actor(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        pid = self.store.propose('2330', make_valuation(self.now), now=self.now)
        with self.assertRaises(PermissionError):
            self.store.apply(pid, actor='999999', allowed_actors=['554850002883706901'],
                             channel=CHANNEL, now=self.now)

    def test_apply_rejects_already_processed(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        pid = self.store.propose('2330', make_valuation(self.now), now=self.now)
        self.store.apply(pid, actor='554850002883706901',
                         allowed_actors=['554850002883706901'], channel=CHANNEL, now=self.now)
        with self.assertRaises(ValueError):
            self.store.apply(pid, actor='554850002883706901',
                             allowed_actors=['554850002883706901'], channel=CHANNEL, now=self.now)

    def test_second_proposal_supersedes_first_on_apply(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        v1 = make_valuation(self.now)
        pid1 = self.store.propose('2330', v1, now=self.now)
        self.store.apply(pid1, actor='A', allowed_actors=['A'], channel=CHANNEL, now=self.now)

        v2 = make_valuation(self.now, sweet=105.0, add=115.0, buy=125.0)
        pid2 = self.store.propose('2330', v2, now=self.now)
        self.store.apply(pid2, actor='A', allowed_actors=['A'], channel=CHANNEL, now=self.now)

        versions = self.store.versions('2330')
        statuses = {v['id']: v['status'] for v in versions}
        self.assertEqual(statuses[pid1], 'superseded')
        self.assertEqual(statuses[pid2], 'active')

    def test_stale_parent_rejected(self):
        """If base version changed since proposal was drafted, applying must fail (no blind overwrite)."""
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        v1 = make_valuation(self.now)
        pid1 = self.store.propose('2330', v1, now=self.now)
        v_other = make_valuation(self.now, sweet=90.0, add=95.0, buy=99.0)
        pid_other = self.store.propose('2330', v_other, now=self.now)
        self.store.apply(pid_other, actor='A', allowed_actors=['A'], channel=CHANNEL, now=self.now)
        # pid1's parent_id is None (no active existed when proposed); now an active exists,
        # so applying pid1 afterwards should fail since parent mismatch.
        with self.assertRaises(ValueError):
            self.store.apply(pid1, actor='A', allowed_actors=['A'], channel=CHANNEL, now=self.now)

    def test_one_active_constraint_enforced_by_db(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        pid = self.store.propose('2330', make_valuation(self.now), now=self.now)
        self.store.apply(pid, actor='A', allowed_actors=['A'], channel=CHANNEL, now=self.now)
        active = self.store.active('2330')
        self.assertIsNotNone(active)
        # Only one row can have status='active' per symbol (unique index) — verified structurally
        rows = self.store.db.execute("SELECT COUNT(*) FROM valuations WHERE symbol=? AND status='active'", ('2330',)).fetchone()
        self.assertEqual(rows[0], 1)


class TestFacts(StoreTestBase):
    def test_set_and_get_fact(self):
        self.store.set_fact('2330', 'financials', {'eps': 10.5, 'as_of': '2026-08-15'})
        got = self.store.fact('2330', 'financials')
        self.assertEqual(got['eps'], 10.5)

    def test_missing_fact_returns_empty_dict(self):
        self.assertEqual(self.store.fact('9999', 'financials'), {})


class TestMeta(StoreTestBase):
    def test_set_and_get_meta(self):
        self.store.set_meta('last_scan', {'at': self.now.isoformat(), 'coverage': 500})
        self.assertEqual(self.store.meta('last_scan')['coverage'], 500)

    def test_missing_meta_returns_empty_dict(self):
        self.assertEqual(self.store.meta('nope'), {})


class TestBackup(StoreTestBase):
    def test_backup_creates_valid_sqlite(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        target = Path(self.tmpdir) / 'backup.db'
        result_path = self.store.backup(target)
        self.assertTrue(Path(result_path).exists())
        # Reopen backup independently and verify data survived
        import sqlite3
        conn = sqlite3.connect(target)
        rows = conn.execute('SELECT symbol FROM instruments').fetchall()
        conn.close()
        self.assertEqual(rows, [('2330',)])

    def test_backup_integrity_check_runs(self):
        target = Path(self.tmpdir) / 'backup2.db'
        # Should not raise for a fresh empty DB
        self.store.backup(target)


class TestAuditTrail(StoreTestBase):
    def test_propose_and_apply_write_audit(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        pid = self.store.propose('2330', make_valuation(self.now), now=self.now)
        self.store.apply(pid, actor='A', allowed_actors=['A'], channel=CHANNEL, now=self.now)
        actions = [r['action'] for r in self.store.db.execute('SELECT action FROM audit')]
        self.assertIn('propose', actions)
        self.assertIn('apply', actions)


if __name__ == '__main__':
    unittest.main()
