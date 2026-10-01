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
CREATE TABLE IF NOT EXISTS recommendations(id TEXT PRIMARY KEY, day TEXT NOT NULL, symbol TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_days(day TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(day,kind));
-- Simulated trading. Every buy, every sell and every completed round trip, each with the day it
-- happened on, so the record can be read back as "what did it trade, when, and what did that cost".
-- `side` is 'buy', 'sell' or 'closed'; a 'closed' row is the pair, carrying the realised result.
CREATE TABLE IF NOT EXISTS paper_trades(id TEXT PRIMARY KEY, at TEXT NOT NULL, day TEXT NOT NULL,
 symbol TEXT NOT NULL, side TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_positions(symbol TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS paper_trades_day ON paper_trades(day);
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
        row = self.db.execute('SELECT payload FROM instruments WHERE symbol=?', (symbol,)).fetchone()
        if not row:
            raise ValueError('請先建立標的母表')
        kind = json.loads(row[0]).get('kind')
        validate_valuation(valuation, now, kind=kind)
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
            inst_row = self.db.execute('SELECT payload FROM instruments WHERE symbol=?', (r['symbol'],)).fetchone()
            inst_kind = json.loads(inst_row[0]).get('kind') if inst_row else None
            validate_valuation(json.loads(r['payload']), now, kind=inst_kind)
            old = self.active(r['symbol'])
            if (old['id'] if old else None) != r['parent_id']:
                raise ValueError('基準版本已改變，需重新產生提案')
            self.db.execute("UPDATE valuations SET status='superseded' WHERE symbol=? AND status='active'", (r['symbol'],))
            self.db.execute("UPDATE valuations SET status='active',applied_at=?,applied_by=? WHERE id=?", (now.isoformat(), str(actor), proposal_id))
            self.audit('apply', {'id': proposal_id, 'actor': str(actor), 'channel': str(channel)})

    def publish_reviewed_analysis(self, proposal_id, *, now=None):
        """Publish researched prices under the user's explicit delegated authority.

        This is an internal research operation, never a Discord sender identity.
        It does not authorize an order and does not weaken validate_valuation.
        """
        now = now or datetime.now(TW)
        authority = self.meta('analysis_authority')
        if authority.get('decision') != 'user-confirmed-2026-09-14' or authority.get('autonomous_research') is not True:
            raise PermissionError('尚未取得自主更新分析價位的授權')
        with self.transaction():
            r = self.db.execute('SELECT * FROM valuations WHERE id=?', (proposal_id,)).fetchone()
            if not r or r['status'] != 'proposed':
                raise ValueError('分析版本不存在或已處理')
            payload = json.loads(r['payload'])
            if payload.get('id', '').startswith('V20260914-'):
                raise ValueError('使用者拒絕的簡化估值不得自動發布')
            review = payload.get('analysis_review') or {}
            required = ('financials', 'outlook', 'valuation_basis', 'price_structure', 'risks')
            if any(review.get(k) is not True for k in required):
                raise ValueError('分析複核尚未完成')
            inst = json.loads(self.db.execute('SELECT payload FROM instruments WHERE symbol=?', (r['symbol'],)).fetchone()[0])
            validate_valuation(payload, now, kind=inst.get('kind'))
            old = self.active(r['symbol'])
            if (old['id'] if old else None) != r['parent_id']:
                raise ValueError('基準版本已改變，需重新分析')
            self.db.execute("UPDATE valuations SET status='superseded' WHERE symbol=? AND status='active'", (r['symbol'],))
            self.db.execute("UPDATE valuations SET status='active',applied_at=?,applied_by=? WHERE id=?", (now.isoformat(), 'codex-research', proposal_id))
            self.audit('publish_reviewed_analysis', {'id': proposal_id, 'actor': 'codex-research', 'authority': authority['decision'], 'review': review})

    def publish_rule_valuation(self, proposal_id, *, now=None):
        """Publish a deterministic rule-engine valuation under the same delegated authority.

        Unlike publish_reviewed_analysis it does not claim company research: the payload carries
        review_mode='rule-engine-v1' and no analysis_review, so nothing downstream can mistake a
        screen for researched analysis. Sources must still be dated official data (validate_valuation).
        """
        now = now or datetime.now(TW)
        authority = self.meta('analysis_authority')
        if authority.get('decision') != 'user-confirmed-2026-09-14' or authority.get('autonomous_research') is not True:
            raise PermissionError('尚未取得自主更新分析價位的授權')
        with self.transaction():
            r = self.db.execute('SELECT * FROM valuations WHERE id=?', (proposal_id,)).fetchone()
            if not r or r['status'] != 'proposed':
                raise ValueError('估值版本不存在或已處理')
            payload = json.loads(r['payload'])
            if payload.get('review_mode') != 'rule-engine-v1' or payload.get('analysis_review'):
                raise ValueError('只有規則引擎估值可用此方式發布，且不得宣稱已完成研究複核')
            inst = json.loads(self.db.execute('SELECT payload FROM instruments WHERE symbol=?', (r['symbol'],)).fetchone()[0])
            validate_valuation(payload, now, kind=inst.get('kind'))
            old = self.active(r['symbol'])
            from .valuation_quality import researched_anchor, anchor_protected
            if anchor_protected(old, now):
                raise ValueError('規則掃描不得覆蓋個股研究價位')
            if researched_anchor(old):
                # The protection has lapsed: this is a recorded handover from research to the screen,
                # not a screen quietly outranking research. See valuation_quality.anchor_protected.
                self.audit('valuation_anchor_handover',
                           {'symbol': r['symbol'], 'from': old.get('id'), 'to': proposal_id,
                            'anchor_valid_until': old.get('valid_until')})
            if (old['id'] if old else None) != r['parent_id']:
                raise ValueError('基準版本已改變，需重新計算')
            self.db.execute("UPDATE valuations SET status='superseded' WHERE symbol=? AND status='active'", (r['symbol'],))
            self.db.execute("UPDATE valuations SET status='active',applied_at=?,applied_by=? WHERE id=?", (now.isoformat(), 'rule-engine', proposal_id))
            self.audit('publish_rule_valuation', {'id': proposal_id, 'authority': authority['decision']})

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
