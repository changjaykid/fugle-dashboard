"""Local SQLite state: instruments, quotes, facts, the simulated account, and atomic JSON writes."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import uuid
from .domain import TW


def normalize_iso(value):
    """Parse any valid ISO-8601 timestamp (incl. 'Z' suffix) and re-render it
    in a single canonical +08:00-offset form, so string ordering (used for
    observations.as_of PRIMARY KEY / ORDER BY) matches real chronological
    order regardless of which offset format the upstream source used."""
    v = value.replace('Z', '+00:00') if isinstance(value, str) and value.endswith('Z') else value
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        raise ValueError(f'as_of/book_as_of must be timezone-aware: {value!r}')
    return dt.astimezone(TW).isoformat()

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS instruments(symbol TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations(symbol TEXT, as_of TEXT, payload TEXT NOT NULL,
 PRIMARY KEY(symbol,as_of));
CREATE TABLE IF NOT EXISTS facts(symbol TEXT, category TEXT, payload TEXT NOT NULL,
 PRIMARY KEY(symbol,category));
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, at TEXT, state TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_days(day TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(day,kind));
-- Simulated trading. Every buy, every sell and every completed round trip, each with the day it
-- happened on, so the record can be read back as "what did it trade, when, and what did that cost".
-- `side` is 'buy', 'sell' or 'closed'; a 'closed' row is the pair, carrying the realised result.
CREATE TABLE IF NOT EXISTS paper_trades(id TEXT PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL,
 symbol TEXT NOT NULL, side TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_positions(symbol TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS paper_trades_day ON paper_trades(day);
-- Every order the scan proposed, filled or not, and what the rule did with it (journal.py). The account
-- fills a handful a month; this follows every proposal, which is what the weekly review argues from.
CREATE TABLE IF NOT EXISTS scan_journal(id TEXT PRIMARY KEY, day TEXT NOT NULL, symbol TEXT NOT NULL,
 payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS scan_journal_day ON scan_journal(day);
'''


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dumps(payload)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.radar-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def instruments(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM instruments ORDER BY symbol')]

    def upsert_instruments(self, items):
        with self.transaction():
            self.db.executemany('INSERT INTO instruments VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload',
                                [(i['symbol'], dumps(i)) for i in items])

    def quote(self, symbol):
        if (self.fact(symbol, 'quote_health') or {}).get('status') == 'blocked':
            return None  # keep history, but never silently reuse it after a failed refresh
        r = self.db.execute('SELECT payload FROM observations WHERE symbol=? ORDER BY as_of DESC LIMIT 1', (symbol,)).fetchone()
        return json.loads(r[0]) if r else None

    def observe(self, quote):
        # Normalize as_of/book_as_of to a single ISO-8601 representation (fixed
        # +08:00 offset, not 'Z' or any other offset) before it becomes the
        # sort/primary-key value. observations.as_of is compared with a plain
        # SQL string ORDER BY / PRIMARY KEY, not parsed per-row -- mixing 'Z'
        # and '+08:00' suffixed strings for the same real time sorts wrong
        # (e.g. '...T00:30:00Z' vs '...T08:00:00+08:00' are the same instant
        # but compare in the opposite order as raw strings). Any upstream
        # fetcher that ever returns UTC/'Z' timestamps must not corrupt
        # ordering here.
        quote = dict(quote)
        quote['as_of'] = normalize_iso(quote['as_of'])
        if quote.get('book_as_of'):
            quote['book_as_of'] = normalize_iso(quote['book_as_of'])
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?)',
                            (quote['symbol'], quote['as_of'], dumps(quote)))
            self.db.execute('INSERT INTO facts VALUES (?,?,?) ON CONFLICT(symbol,category) DO UPDATE SET payload=excluded.payload',
                            (quote['symbol'], 'quote_health', dumps({'status':'valid'})))

    def fact(self, symbol, category):
        r = self.db.execute('SELECT payload FROM facts WHERE symbol=? AND category=?', (symbol, category)).fetchone()
        return json.loads(r[0]) if r else {}

    def set_fact(self, symbol, category, payload):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO facts VALUES(?,?,?)', (symbol, category, dumps(payload)))

    def meta(self, key):
        r = self.db.execute('SELECT payload FROM metadata WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else {}

    def set_meta(self, key, value):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (key, dumps(value)))

    def record_delivery(self, content_hash, state, payload, now=None):
        """Outbox record for a Discord send attempt. `content_hash` is the
        caller's own dedup key (e.g. a hash of the message text) -- this
        table does not compute it, so callers control exactly what counts
        as "the same notification" (see cli.py's notify commands). Every
        attempt (success or failure) gets its own row; nothing is
        overwritten, so a full retry/audit history survives."""
        now = now or datetime.now(TW)
        delivery_id = uuid.uuid4().hex
        with self.transaction():
            self.db.execute('INSERT INTO deliveries VALUES(?,?,?,?)',
                            (delivery_id, now.isoformat(), state,
                             dumps({**payload, 'content_hash': content_hash})))
        return delivery_id

    def find_sent_delivery(self, content_hash, *, since):
        """Returns the most recent delivery row with state='sent' and a
        matching content_hash, whose `at` is >= `since` (an ISO string or
        datetime), or None. Used by the outbox-dedup check: if a given
        notification content already has a successful delivery today,
        callers must skip re-sending it, even if a duplicate cron tick
        (e.g. a restart-recovery re-run) tries to notify again."""
        since_iso = since.isoformat() if hasattr(since, 'isoformat') else since
        rows = self.db.execute(
            "SELECT id, at, payload FROM deliveries WHERE state='sent' AND at>=? ORDER BY at DESC",
            (since_iso,)).fetchall()
        for r in rows:
            payload = json.loads(r['payload'])
            if payload.get('content_hash') == content_hash:
                return {'id': r['id'], 'at': r['at'], 'payload': payload}
        return None

    def recent_delivery_failures(self, *, since, content_hash=None):
        """Count of failed delivery attempts since a given time, optionally
        scoped to one content_hash. Used to decide whether a retry budget
        has been exhausted across separate process invocations (not just
        within one send_with_retry() call), so a cron schedule that fires
        again a few minutes later doesn't restart the retry counter from
        zero and hammer Discord indefinitely on a persistent failure."""
        since_iso = since.isoformat() if hasattr(since, 'isoformat') else since
        rows = self.db.execute(
            "SELECT payload FROM deliveries WHERE state='failed' AND at>=?", (since_iso,)).fetchall()
        n = 0
        for r in rows:
            payload = json.loads(r['payload'])
            if content_hash is None or payload.get('content_hash') == content_hash:
                n += 1
        return n

    def backup(self, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as dest:
            self.db.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('備份完整性檢查失敗')
        return str(target)

    @staticmethod
    def restore(backup_path, target_db_path, *, force=False):
        """Restore a backup file (produced by Store.backup()) onto
        target_db_path. Refuses to overwrite an existing, non-empty target
        DB unless force=True -- restore is a destructive operation and
        must not be a silent one-liner that clobbers a live production DB
        by accident. Verifies the BACKUP's own integrity before touching
        the target (not the target's, since the target is about to be
        replaced anyway), and verifies the restored copy's integrity
        again after writing, so a corrupt/truncated backup file is caught
        loudly rather than silently installed.

        Returns the target path on success; raises on any integrity
        failure or on an unforced overwrite attempt.
        """
        backup_path = Path(backup_path)
        target_db_path = Path(target_db_path)
        if not backup_path.exists():
            raise FileNotFoundError(f'backup file not found: {backup_path}')
        with sqlite3.connect(backup_path) as src_check:
            if src_check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError(f'備份檔案完整性檢查失敗，拒絕還原: {backup_path}')
        if target_db_path.exists() and target_db_path.stat().st_size > 0 and not force:
            raise FileExistsError(
                f'target DB already exists and is non-empty ({target_db_path}); '
                f'pass force=True (CLI: --force) to overwrite it deliberately')
        target_db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup_path) as src, sqlite3.connect(target_db_path) as dest:
            src.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError(f'還原後完整性檢查失敗: {target_db_path}')
        return str(target_db_path)
