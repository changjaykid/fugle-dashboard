"""Replay stock_radar.swing over the stored history, trade by trade, with the production arithmetic.

Every signal day is scanned with `swing.scan` on the history up to that close and each pick is followed
with `swing.follow` -- the journal's arithmetic. One trade per (day, symbol), the way the study counted, so
the two can be compared: if this does not reproduce the study's figure, the production code is not the
rule that was measured. On 2026-10-13, before prices were put on the tick grid, it gave +1.85% a trade,
61.6% won, 18.6 sessions against the study's +1.88%, 61.6%, 18.6; with ticks, +2.00%, 62.3%, 18.7
(2022-08-04 .. 2026-07-07, 1,283 trades, about six minutes).
"""
import argparse, json, statistics, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stock_radar import market, swing
from stock_radar.store import Store

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', default=str(Path.home() / 'kid-agent-data/stock-radar/prod.db'))
    p.add_argument('--start', default='2022-08-04')
    p.add_argument('--end', default='2026-07-07')
    a = p.parse_args()
    store = Store(a.db)
    prices = market.split_adjust(market.load(store, 'prices'))
    flows = market.load(store, 'flows')
    insts = {i['symbol']: i for i in store.instruments() if i['kind'] == 'stock'}
    s = swing.SETTINGS
    days = [d for d, _ in prices]
    trades, began = [], time.time()
    for k, day in enumerate(days):
        if day < a.start or day > a.end or k < 130:
            continue
        lo = k - 125
        result = swing.scan(prices[lo:k + 1], flows[max(0, lo - 1):k + 1], insts, settings=s)
        for c in result['candidates']:
            r = swing.follow(prices, k, c['symbol'], c['limit'], s)
            if r['status'] == 'closed':
                trades.append({'day': day, 'symbol': c['symbol'], 'net': r['net_pct'] / 100,
                               'held': r['held'], 'how': r['how']})
    returns = [t['net'] for t in trades]
    by_year = {}
    for t in trades:
        by_year.setdefault(t['day'][:4], []).append(t['net'])
    print(json.dumps({'trades': len(trades), 'mean_pct': round(statistics.fmean(returns) * 100, 2),
                      'win_pct': round(sum(r > 0 for r in returns) / len(returns) * 100, 1),
                      'held': round(statistics.fmean(t['held'] for t in trades), 1),
                      'exits': {h: sum(t['how'] == h for t in trades) for h in ('target', 'stop', 'time')},
                      'by_year': {y: [round(statistics.fmean(v) * 100, 2), round(sum(x > 0 for x in v) / len(v) * 100), len(v)]
                                  for y, v in sorted(by_year.items())},
                      'seconds': round(time.time() - began)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
