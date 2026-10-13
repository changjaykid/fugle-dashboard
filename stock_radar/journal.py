"""Every order the scan proposed, and what the rule would have done with it. The forward evidence base.

The account fills only what its slots and cash allow, so it records a handful of trades a month. The
journal records every candidate the scan proposed -- and, on days the market gate held them back, those
too, marked as such -- and follows each through `swing.follow`, the same arithmetic as the replay. That
is the number the weekly review argues from, and the one that says whether the measured edge is holding.

Table `scan_journal`, one row per (day, symbol). `record` adds tonight's; `evaluate` settles every row
that can now be settled. Both are idempotent.
"""
import argparse
import json
import statistics
from pathlib import Path

from .store import Store, dumps
from . import market, swing


def record(store):
    payload = store.meta('daily_scan') or {}
    if not payload:
        return {'status': 'no_scan'}
    added = 0
    rows = [(c, False) for c in payload['candidates']] + [(c, True) for c in payload.get('held_back') or []]
    with store.transaction():
        for c, held_back in rows:
            key = f"{payload['day']}:{c['symbol']}"
            if store.db.execute('SELECT 1 FROM scan_journal WHERE id=?', (key,)).fetchone():
                continue
            store.db.execute('INSERT INTO scan_journal VALUES(?,?,?,?)', (key, payload['day'], c['symbol'], dumps({
                'day': payload['day'], 'symbol': c['symbol'], 'name': c['name'], 'version': payload['version'],
                'held_back': held_back, 'close': c['close'], 'limit': c['limit'], 'target': c['target'],
                'stop': c['stop'], 'flow_share': c['flow_share'], 'why': c['why'], 'outcome': None})))
            added += 1
    return {'status': 'ok', 'day': payload['day'], 'added': added}


def evaluate(store):
    open_rows = [(k, json.loads(p)) for k, p in store.db.execute('SELECT id,payload FROM scan_journal')
                 if not (json.loads(p).get('outcome') or {}).get('status') in ('closed', 'unfilled')]
    if not open_rows:
        return {'status': 'ok', 'settled': 0}
    since = min(r['day'] for _, r in open_rows)
    prices = market.split_adjust(market.load(store, 'prices', since=since))
    index = {d: k for k, (d, _) in enumerate(prices)}
    settled = 0
    with store.transaction():
        for key, row in open_rows:
            k = index.get(row['day'])
            if k is None:
                continue
            row['outcome'] = swing.follow(prices, k, row['symbol'], row['limit'])
            settled += row['outcome']['status'] in ('closed', 'unfilled')
            store.db.execute('UPDATE scan_journal SET payload=? WHERE id=?', (dumps(row), key))
    return {'status': 'ok', 'settled': settled}


def stats(store, version=swing.VERSION):
    """What the rule's own proposals have done so far, under the current version only."""
    rows = [json.loads(p) for (p,) in store.db.execute('SELECT payload FROM scan_journal')]
    rows = [r for r in rows if r.get('version') == version and not r.get('held_back')]
    out = {'proposed': len(rows)}
    closed = [r['outcome'] for r in rows if (r.get('outcome') or {}).get('status') == 'closed']
    out['unfilled'] = sum((r.get('outcome') or {}).get('status') == 'unfilled' for r in rows)
    out['open'] = sum((r.get('outcome') or {}).get('status') == 'open' for r in rows)
    out['closed'] = len(closed)
    if closed:
        nets = [c['net_pct'] for c in closed]
        out.update({'mean_pct': round(statistics.fmean(nets), 2),
                    'median_pct': round(statistics.median(nets), 2),
                    'win_pct': round(sum(n > 0 for n in nets) / len(nets) * 100, 1),
                    'held': round(statistics.fmean(c['held'] for c in closed), 1),
                    'exits': {h: sum(c['how'] == h for c in closed) for h in ('target', 'stop', 'time')}})
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('action', choices=('record', 'evaluate', 'stats'))
    a = p.parse_args(argv)
    s = Store(a.db)
    try:
        print(json.dumps({'record': record, 'evaluate': evaluate, 'stats': stats}[a.action](s),
                         ensure_ascii=False))
    finally:
        s.close()


if __name__ == '__main__':
    main()
