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


    def test_as_of_normalized_to_plus_08_00(self):
        """Regression: as_of must be stored in a single canonical offset form
        so string ORDER BY matches real chronological order."""
        q = {'symbol': '2330', 'as_of': self.now.isoformat(), 'price': 100.0}
        self.store.observe(q)
        got = self.store.quote('2330')
        self.assertTrue(got['as_of'].endswith('+08:00'))


    def test_book_as_of_also_normalized(self):
        q = {'symbol': '2330', 'as_of': self.now.isoformat(),
            'book_as_of': (self.now + timedelta(minutes=1)).astimezone(
                __import__('datetime').timezone.utc).isoformat().replace('+00:00', 'Z'),
            'price': 100.0}
        self.store.observe(q)
        got = self.store.quote('2330')
        self.assertTrue(got['book_as_of'].endswith('+08:00'))

    def test_mixed_z_and_offset_timestamps_sort_correctly(self):
        """A 'Z' timestamp later in real time than an earlier-looking +08:00 one must be the latest quote."""
        later_utc_z = (self.now + timedelta(minutes=30)).astimezone(
            __import__('datetime').timezone.utc).isoformat().replace('+00:00', 'Z')
        self.store.observe({'symbol': '2330', 'as_of': self.now.isoformat(), 'price': 100.0})
        self.store.observe({'symbol': '2330', 'as_of': later_utc_z, 'price': 101.0})
        self.assertEqual(self.store.quote('2330')['price'], 101.0)


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
    def test_backup_integrity_check_runs(self):
        self.store.backup(Path(self.tmpdir) / 'backup2.db')  # a fresh empty DB must not raise

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


class TestBackupRestore(StoreTestBase):
    def test_restore_roundtrip_preserves_data(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock', 'name': '台積電'}])
        backup_path = Path(self.tmpdir) / 'backup.db'
        self.store.backup(backup_path)
        restored_path = Path(self.tmpdir) / 'restored.db'
        Store.restore(backup_path, restored_path)
        restored = Store(restored_path)
        try:
            self.assertEqual(len(restored.instruments()), 1)
            self.assertEqual(restored.instruments()[0]['symbol'], '2330')
        finally:
            restored.close()

    def test_restore_refuses_missing_backup_file(self):
        with self.assertRaises(FileNotFoundError):
            Store.restore(Path(self.tmpdir) / 'nope.db', Path(self.tmpdir) / 'target.db')

    def test_restore_refuses_nonempty_target_without_force(self):
        backup_path = Path(self.tmpdir) / 'backup.db'
        self.store.backup(backup_path)
        target_path = Path(self.tmpdir) / 'existing.db'
        other = Store(target_path)
        other.upsert_instruments([{'symbol': '9999', 'kind': 'stock'}])
        other.close()
        with self.assertRaises(FileExistsError):
            Store.restore(backup_path, target_path)

    def test_restore_allows_nonempty_target_with_force(self):
        self.store.upsert_instruments([{'symbol': '2330', 'kind': 'stock'}])
        backup_path = Path(self.tmpdir) / 'backup.db'
        self.store.backup(backup_path)
        target_path = Path(self.tmpdir) / 'existing.db'
        other = Store(target_path)
        other.upsert_instruments([{'symbol': '9999', 'kind': 'stock'}])
        other.close()
        Store.restore(backup_path, target_path, force=True)
        restored = Store(target_path)
        try:
            symbols = {i['symbol'] for i in restored.instruments()}
            self.assertEqual(symbols, {'2330'})  # 9999 replaced, not merged
        finally:
            restored.close()

    def test_restore_to_empty_existing_file_does_not_need_force(self):
        backup_path = Path(self.tmpdir) / 'backup.db'
        self.store.backup(backup_path)
        target_path = Path(self.tmpdir) / 'empty.db'
        target_path.touch()  # exists but 0 bytes
        Store.restore(backup_path, target_path)  # must not raise


class TestDeliveryOutbox(StoreTestBase):
    """Regression suite for the notification outbox/dedup/retry-count
    support added 2026-09-10 (Codex review item 6)."""

    def test_record_delivery_creates_a_row(self):
        did = self.store.record_delivery('hash1', 'sent', {'text': 'hello'}, now=self.now)
        self.assertIsNotNone(did)
        row = self.store.db.execute('SELECT state FROM deliveries WHERE id=?', (did,)).fetchone()
        self.assertEqual(row['state'], 'sent')

    def test_find_sent_delivery_matches_by_content_hash(self):
        self.store.record_delivery('hash-a', 'sent', {'text': 'x'}, now=self.now)
        found = self.store.find_sent_delivery('hash-a', since=self.now - timedelta(hours=1))
        self.assertIsNotNone(found)
        self.assertEqual(found['payload']['content_hash'], 'hash-a')

    def test_find_sent_delivery_returns_none_for_unknown_hash(self):
        self.store.record_delivery('hash-a', 'sent', {'text': 'x'}, now=self.now)
        found = self.store.find_sent_delivery('hash-b', since=self.now - timedelta(hours=1))
        self.assertIsNone(found)

    def test_find_sent_delivery_ignores_failed_state(self):
        """A failed attempt must not count as 'already sent' -- a retry
        must still be allowed to actually send."""
        self.store.record_delivery('hash-a', 'failed', {'text': 'x', 'error': 'timeout'}, now=self.now)
        found = self.store.find_sent_delivery('hash-a', since=self.now - timedelta(hours=1))
        self.assertIsNone(found)

    def test_find_sent_delivery_respects_since_cutoff(self):
        old = self.now - timedelta(days=2)
        self.store.record_delivery('hash-a', 'sent', {'text': 'x'}, now=old)
        found = self.store.find_sent_delivery('hash-a', since=self.now - timedelta(hours=1))
        self.assertIsNone(found)  # too old, outside the since window

    def test_recent_delivery_failures_counts_only_failed_state(self):
        self.store.record_delivery('hash-a', 'sent', {'text': 'x'}, now=self.now)
        self.store.record_delivery('hash-a', 'failed', {'text': 'x'}, now=self.now)
        self.store.record_delivery('hash-a', 'failed', {'text': 'x'}, now=self.now)
        n = self.store.recent_delivery_failures(since=self.now - timedelta(hours=1))
        self.assertEqual(n, 2)

    def test_recent_delivery_failures_scoped_to_content_hash_when_given(self):
        self.store.record_delivery('hash-a', 'failed', {'text': 'x'}, now=self.now)
        self.store.record_delivery('hash-b', 'failed', {'text': 'y'}, now=self.now)
        n = self.store.recent_delivery_failures(since=self.now - timedelta(hours=1), content_hash='hash-a')
        self.assertEqual(n, 1)

    def test_multiple_attempts_all_preserved_not_overwritten(self):
        self.store.record_delivery('hash-a', 'failed', {'text': 'x', 'attempt': 1}, now=self.now)
        self.store.record_delivery('hash-a', 'failed', {'text': 'x', 'attempt': 2}, now=self.now)
        self.store.record_delivery('hash-a', 'sent', {'text': 'x', 'attempt': 3}, now=self.now)
        rows = self.store.db.execute('SELECT COUNT(*) as n FROM deliveries').fetchone()
        self.assertEqual(rows['n'], 3)


if __name__ == '__main__':
    unittest.main()
