"""Local SQLite state with auditable proposals and atomic public snapshots."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import uuid
from .domain import TW, validate_valuation

SCHEMA = '''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS instruments(symbol TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations(symbol TEXT, as_of TEXT, payload TEXT NOT NULL,
 PRIMARY KEY(symbol,as_of));
CREATE TABLE IF NOT EXISTS valuations(id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
 parent_id TEXT, status TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
 applied_at TEXT, applied_by TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active ON valuations(symbol) WHERE status='active';
CREATE TABLE IF NOT EXISTS facts(symbol TEXT, category TEXT, payload TEXT NOT NULL,
 PRIMARY KEY(symbol,category));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT, action TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, at TEXT, task TEXT, status TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, at TEXT, state TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, symbol TEXT, at TEXT, reason TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
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

    def audit(self, action, payload):
        self.db.execute('INSERT INTO audit(at,action,payload) VALUES(?,?,?)',
                        (datetime.now(TW).isoformat(), action, dumps(payload)))

    def instruments(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM instruments ORDER BY symbol')]

    def upsert_instruments(self, items):
        with self.transaction():
            self.db.executemany('INSERT INTO instruments VALUES(?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload',
                                [(i['symbol'], dumps(i)) for i in items])

    def quote(self, symbol):
        r = self.db.execute('SELECT payload FROM observations WHERE symbol=? ORDER BY as_of DESC LIMIT 1', (symbol,)).fetchone()
        return json.loads(r[0]) if r else None

    def observe(self, quote):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?)',
                            (quote['symbol'], quote['as_of'], dumps(quote)))

    def history(self, symbol, limit=60):
        rows = self.db.execute('SELECT payload FROM observations WHERE symbol=? ORDER BY as_of DESC LIMIT ?', (symbol, limit)).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]

    def fact(self, symbol, category):
        r = self.db.execute('SELECT payload FROM facts WHERE symbol=? AND category=?', (symbol, category)).fetchone()
        return json.loads(r[0]) if r else {}

    def set_fact(self, symbol, category, payload):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO facts VALUES(?,?,?)', (symbol, category, dumps(payload)))
            self.audit('fact', {'symbol': symbol, 'category': category})

    def active(self, symbol):
        r = self.db.execute("SELECT id,payload FROM valuations WHERE symbol=? AND status='active'", (symbol,)).fetchone()
        return dict(json.loads(r['payload']), id=r['id']) if r else None

    def versions(self, symbol):
        return [{**dict(r), 'payload': json.loads(r['payload'])} for r in self.db.execute(
            'SELECT id,parent_id,status,payload,created_at,applied_at FROM valuations WHERE symbol=? ORDER BY created_at DESC', (symbol,))]

    def propose(self, symbol, valuation, now=None):
        now = now or datetime.now(TW)
        validate_valuation(valuation, now)
        if not self.db.execute('SELECT 1 FROM instruments WHERE symbol=?', (symbol,)).fetchone():
            raise ValueError('請先建立標的母表')
        proposal_id = uuid.uuid4().hex
        with self.transaction():
            old = self.active(symbol)
            self.db.execute('INSERT INTO valuations VALUES(?,?,?,?,?,?,NULL,NULL)',
                            (proposal_id, symbol, old['id'] if old else None, 'proposed', dumps(valuation), now.isoformat()))
            self.audit('propose', {'id': proposal_id, 'symbol': symbol})
        return proposal_id

    def apply(self, proposal_id, actor, allowed_actors, channel, now=None):
        if str(actor) not in {str(a) for a in allowed_actors} or str(channel) != '1493898877970153532':
            raise PermissionError('只有指定股票頻道的授權使用者能套用估值')
        now = now or datetime.now(TW)
        with self.transaction():
            r = self.db.execute('SELECT * FROM valuations WHERE id=?', (proposal_id,)).fetchone()
            if not r or r['status'] != 'proposed':
                raise ValueError('提案不存在或已處理')
            validate_valuation(json.loads(r['payload']), now)
            old = self.active(r['symbol'])
            if (old['id'] if old else None) != r['parent_id']:
                raise ValueError('基準版本已改變，需重新產生提案')
            self.db.execute("UPDATE valuations SET status='superseded' WHERE symbol=? AND status='active'", (r['symbol'],))
            self.db.execute("UPDATE valuations SET status='active',applied_at=?,applied_by=? WHERE id=?", (now.isoformat(), str(actor), proposal_id))
            self.audit('apply', {'id': proposal_id, 'actor': str(actor), 'channel': str(channel)})

    def meta(self, key):
        r = self.db.execute('SELECT payload FROM metadata WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else {}

    def set_meta(self, key, value):
        with self.transaction():
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (key, dumps(value)))

    def backup(self, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as dest:
            self.db.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('備份完整性檢查失敗')
        return str(target)
