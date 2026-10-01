"""Every decision the scanner made, and what the market did about it afterwards.

The simulated account records what was bought. That is five to thirty trades a month, and a month of it
cannot answer a single one of the twelve questions the weekly review is supposed to ask -- one stock was
40% of a window's return in the replay, so a handful of fills is noise wearing the costume of evidence.

This records the other 95%: every stock that scored, whether it was bought, waited on, or scored too low,
with its seven component scores and the forward return it went on to have against the market over the
same days. Eighty-odd candidates a day is roughly 1,600 observations a month, which is enough to ask
whether the score predicts anything, whether a breakout is worth more than a pullback, and which of the
seven components is doing work rather than decoration.

A WAIT is recorded as carefully as a buy, because "we were right not to buy it" and "we talked ourselves
out of a winner" are different failures and only the forward outcome tells them apart.
"""
import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

from .domain import TW
from .store import Store, dumps
from . import buy_plan, market

HORIZONS = (1, 5, 20)


def record(store, now):
    """Write today's scan to the journal. One row per candidate, keyed by day and symbol."""
    payload = store.meta('v1_candidates') or {}
    if not payload:
        return {'status': 'no_scan'}
    day = payload['day']
    rows = ([{**c, 'decision': 'buy'} for c in payload.get('candidates', [])]
            + [{**w, 'decision': 'wait'} for w in payload.get('waits', [])])
    written = 0
    with store.transaction():
        for r in rows:
            key = f"{day}:{r['symbol']}"
            if store.db.execute('SELECT 1 FROM v1_journal WHERE id=?', (key,)).fetchone():
                continue
            store.db.execute('INSERT INTO v1_journal VALUES(?,?,?,?)', (key, day, r['symbol'], dumps({
                'day': day, 'symbol': r['symbol'], 'name': r['name'], 'industry': r.get('industry'),
                'decision': r['decision'], 'setup': r.get('setup'), 'total': r.get('total'),
                'grade': r.get('grade'), 'components': r.get('components'),
                'scored_out_of': r.get('scored_out_of'), 'missing': r.get('missing'),
                'close': r.get('close'), 'entry': r.get('entry'), 'stop': r.get('stop'),
                'target': r.get('target'), 'rr': r.get('rr'),
                'wait_reason': r.get('wait_reason'), 'setup_why': r.get('setup_why'),
                'range_position': r.get('range_position'),
                'relative_volume': r.get('relative_volume'),
                'min_score': payload.get('min_score'), 'recorded_at': now.isoformat()})))
            written += 1
    return {'status': 'ok', 'day': day, 'written': written, 'rows': len(rows)}


def evaluate(store):
    """Fill in forward returns against the market. Results are kept once known."""
    prices = market.split_adjust(market.load(store, 'prices', market.SCAN_DAYS))
    days = [d for d, _ in prices]
    index = {d: k for k, d in enumerate(days)}
    insts = {i['symbol']: i for i in store.instruments() if i['kind'] == 'stock'}
    changed = 0
    with store.transaction():
        for key, payload in store.db.execute('SELECT id,payload FROM v1_journal').fetchall():
            row = json.loads(payload)
            k = index.get(row['day'])
            if k is None:
                continue
            start = prices[k][1].get(row['symbol'])
            if not start or not start[3]:
                continue
            before = dumps(row)
            out = row.setdefault('forward', {})
            liquid = market.liquidity(prices[:k + 1])
            for h in HORIZONS:
                if str(h) in out or k + h >= len(days):
                    continue
                end = prices[k + h][1].get(row['symbol'])
                if not end or not end[3]:
                    continue
                moves = [prices[k + h][1][x][3] / prices[k][1][x][3] - 1 for x in insts
                         if liquid.get(x, 0) >= market.LIQUIDITY_LOTS
                         and x in prices[k][1] and x in prices[k + h][1]
                         and prices[k][1][x][3] and prices[k + h][1][x][3]]
                mkt = statistics.mean(moves) if moves else 0.0
                own = end[3] / start[3] - 1
                out[str(h)] = {'own': round(own * 100, 2), 'market': round(mkt * 100, 2),
                               'excess': round((own - mkt) * 100, 2)}
            if dumps(row) != before:
                store.db.execute('UPDATE v1_journal SET payload=? WHERE id=?', (dumps(row), key))
                changed += 1
    return changed


def rows(store, since=None):
    q = 'SELECT payload FROM v1_journal' + (' WHERE day>=?' if since else '') + ' ORDER BY day,symbol'
    return [json.loads(r[0]) for r in store.db.execute(q, (since,) if since else ())]


def stats(store, since=None, horizon=5):
    """What the review reads: does the score predict anything, and which decisions were right?"""
    data = [r for r in rows(store, since) if str(horizon) in (r.get('forward') or {})]
    if not data:
        return {'observations': 0}
    def group(items):
        if not items:
            return None
        ex = [r['forward'][str(horizon)]['excess'] for r in items]
        return {'n': len(ex), 'mean_excess': round(statistics.mean(ex), 2),
                'median_excess': round(statistics.median(ex), 2),
                'beat_market_pct': round(sum(1 for x in ex if x > 0) / len(ex) * 100, 1)}
    out = {'observations': len(data), 'horizon_days': horizon,
           'days': len({r['day'] for r in data}),
           'all': group(data),
           'by_decision': {d: group([r for r in data if r['decision'] == d])
                           for d in ('buy', 'wait')},
           'by_setup': {k: group([r for r in data if r.get('setup') == k])
                        for k in ('breakout', 'pullback')}}
    # Does a higher score actually do better? The question the whole 100-point scale rests on.
    scored = sorted((r for r in data if r.get('total') is not None), key=lambda r: r['total'])
    if len(scored) >= 20:
        third = len(scored) // 3
        out['by_score'] = {'bottom_third': group(scored[:third]),
                           'middle_third': group(scored[third:2 * third]),
                           'top_third': group(scored[2 * third:])}
    # And which components carry it: correlation of each component with forward excess.
    comps = {}
    for name in ('structure', 'relative_strength', 'participation', 'institutional',
                 'sector', 'fundamental', 'risk_quality'):
        pairs = [(r['components'][name], r['forward'][str(horizon)]['excess'])
                 for r in data if (r.get('components') or {}).get(name) is not None]
        if len(pairs) >= 30:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            if statistics.pstdev(xs) > 0 and statistics.pstdev(ys) > 0:
                comps[name] = {'n': len(pairs), 'correlation': round(statistics.correlation(xs, ys), 3)}
    out['by_component'] = comps
    return out


def text(store, since=None, horizon=5):
    st = stats(store, since, horizon)
    if not st['observations']:
        return 'Strategy v1 決策日誌：尚無可評估的觀測。'
    lines = [f"Strategy v1 決策日誌｜{st['days']} 個交易日、{st['observations']} 個觀測"
             f"（{horizon} 日後對同期全市場等權）"]
    a = st['all']
    lines.append(f"全部候選：平均超額 {a['mean_excess']:+.2f}%、中位數 {a['median_excess']:+.2f}%、"
                 f"勝過市場 {a['beat_market_pct']:.0f}%")
    for name, label in (('buy', '可下單'), ('wait', '不下單')):
        g = st['by_decision'].get(name)
        if g:
            lines.append(f"  {label} {g['n']} 筆：中位數超額 {g['median_excess']:+.2f}%、"
                         f"勝過市場 {g['beat_market_pct']:.0f}%")
    for name, label in (('breakout', '突破'), ('pullback', '回檔')):
        g = st['by_setup'].get(name)
        if g:
            lines.append(f"  {label}型 {g['n']} 筆：中位數超額 {g['median_excess']:+.2f}%")
    if st.get('by_score'):
        s3 = st['by_score']
        lines.append('分數三等分的中位數超額：低 %+.2f%%／中 %+.2f%%／高 %+.2f%%'
                     % (s3['bottom_third']['median_excess'], s3['middle_third']['median_excess'],
                        s3['top_third']['median_excess']))
    if st.get('by_component'):
        best = sorted(st['by_component'].items(), key=lambda x: -abs(x[1]['correlation']))
        lines.append('分項與超額的相關性：' + '、'.join(
            f"{k} {v['correlation']:+.2f}" for k, v in best))
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--since')
    p.add_argument('--horizon', type=int, default=5)
    sub = p.add_subparsers(dest='cmd')
    sub.add_parser('record')
    sub.add_parser('evaluate')
    sub.add_parser('summary')
    a = p.parse_args(argv)
    s = Store(a.db)
    try:
        now = datetime.now(TW)
        if a.cmd == 'record':
            print(json.dumps(record(s, now), ensure_ascii=False))
        elif a.cmd == 'evaluate':
            print(json.dumps({'updated': evaluate(s)}, ensure_ascii=False))
        else:
            print(text(s, a.since, a.horizon))
    finally:
        s.close()


if __name__ == '__main__':
    main()
