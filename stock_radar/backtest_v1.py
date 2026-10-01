"""Replay Strategy v1 over the cached history, through the same book and the same costs as everything else.

Three things this does differently from the earlier replay, and each is deliberate:

  Nothing that only holds today's value is read. `screen.run` is called with fundamentals and the risk
  fact switched off, because both are snapshots -- this quarter's EPS, today's disposition status -- and
  scoring a day in 2022 with them is reading the future. The score renormalises over the 75 or 90 points
  that remain, which is why it renormalises at all.

  Entry is market-on-open the next session, not a limit under yesterday's close. A limit below the close
  fills only when a rising stock pulls back, so it selects the ones that stopped working -- measured
  earlier on exactly this data, and the reason the first strength model looked worse than it was.

  Stop and target come from the structure, not from a multiple. That is the document's section 12 and 14,
  and it means the risk per trade varies, which is what position sizing is for.

The per-day candidate list is cached: the screen costs about two seconds a day over 1,084 stocks, so a
five-year scan is half an hour and every parameter change after it is seconds.
"""
import json
import statistics
from pathlib import Path

from . import market, paper, screen, selection, strategy

SETTINGS = {
    'slots': 20,
    'min_score': 65,            # the document's C floor; A and B are rarer than the thresholds suggest
    'require_grade': None,      # set to 'B' or 'A' to test the document's "only trade A" reading
    'max_holding_days': 60,
    # Section 17 calls a missing follow-through a "Review / Exit Candidate", not an exit. Making it
    # automatic was mine, and it fired 17 times in 70 days, so it is a switch to be tested rather than
    # an assumption. None disables it.
    'follow_through_days': None,
    'starting_cash': 100000,
}


def scan_days(store, prices, flows, start, cache=None, settings=None):
    """{day_index: [candidate, ...]} with entry, stop and target already attached."""
    if cache and Path(cache).is_file():
        stored = json.loads(Path(cache).read_text())
        if stored.get('days') == [d for d, _ in prices][start:]:
            return {int(k): v for k, v in stored['picks'].items()}
    out = {}
    for k in range(start, len(prices)):
        rows, _ = screen.run(store, prices, flows, k, settings,
                             fundamentals=False, risk_facts=False)
        picks = []
        for r in rows:
            plan, _ = selection.trade_plan(r['features'], r['setup'], settings)
            if not plan:
                continue
            picks.append({'symbol': r['symbol'], 'name': r['name'], 'kind': 'stock',
                          'setup': r['setup'], 'total': r['total'], 'grade': r['grade'],
                          'entry': plan['entry'], 'stop': plan['stop'], 'target': plan['target'],
                          'rr': plan['rr'], 'stop_fraction': plan['stop_fraction'],
                          'industry': r['industry']})
        out[k] = picks
        if cache and (k - start) % 100 == 0:
            print('  掃描 %d/%d 天' % (k - start, len(prices) - start), flush=True)
    if cache:
        Path(cache).write_text(json.dumps({'days': [d for d, _ in prices][start:],
                                           'picks': {str(k): v for k, v in out.items()}},
                                          ensure_ascii=False))
    return out


def run(prices, picks, lo, hi, settings=None):
    """One pass of the account over [lo, hi). Returns the book."""
    s = {**SETTINGS, **(settings or {})}
    days = [d for d, _ in prices]
    book = paper.Book(s['starting_cash'], started_day=days[lo])
    orders = []
    for k in range(lo, hi):
        day = days[k]
        bars = prices[k][1]
        at = day + 'T13:30:00+08:00'
        book.settle(day)

        # 1. Yesterday's list, bought at today's open: a confirmed setup is not waited for.
        for o in orders:
            if len(book.positions) >= s['slots'] or o['symbol'] in book.positions:
                continue
            bar = bars.get(o['symbol'])
            if not bar or not bar[0]:
                continue
            fill = bar[0]
            if fill <= o['stop']:
                continue                      # opened below its own invalidation: the setup is gone
            shares, _ = strategy.size_for(book.equity(), book.available(), fill, o['stop'],
                                          {**strategy.SETTINGS, 'max_positions': s['slots']})
            if not shares:
                continue
            book.buy(o['symbol'], o['name'], 'stock', shares, fill, day, at,
                     plan={'stop': o['stop'], 'target': o['target'], 'setup': o['setup'],
                           'score': o['total'], 'grade': o['grade'], 'rr': o['rr'],
                           'exit_mode': 'structural'},
                     entry_index=k)
        orders = []

        # 2. Exits. Stop first when a bar spans both, because a daily bar cannot say which came first.
        for symbol in list(book.positions):
            bar = bars.get(symbol)
            if not bar:
                continue
            position = book.positions[symbol]
            held = k - position['entry_index']
            leave = None
            if bar[2] <= position['stop']:
                leave = ('stop', min(position['stop'], bar[0]))
            elif bar[1] >= position['target']:
                leave = ('target', max(position['target'], bar[0]))
            elif s['follow_through_days'] and held == s['follow_through_days'] \
                    and bar[3] < position['entry']:
                leave = ('no_follow_through', bar[3])
            elif s['max_holding_days'] and held >= s['max_holding_days']:
                leave = ('time', bar[3])
            if leave:
                book.sell(symbol, leave[1], day, at, leave[0],
                          release_day=days[min(k + 2, len(days) - 1)], exit_index=k)
        book.mark(bars, day)

        # 3. Tomorrow's orders: the best scores that are also buyable here.
        if len(book.positions) < s['slots'] and k + 1 < hi:
            candidates = [p for p in picks.get(k, []) if p['total'] >= s['min_score']]
            if s['require_grade']:
                wanted = {'A': ('A',), 'B': ('A', 'B'), 'C': ('A', 'B', 'C')}[s['require_grade']]
                candidates = [p for p in candidates if p['grade'] in wanted]
            orders = candidates[:s['slots']]
    return book


def context(prices, lo, hi):
    a, b = prices[lo][1], prices[hi - 1][1]
    liq = market.liquidity(prices[:lo + 1])
    moves = [b[x][3] / a[x][3] - 1 for x, v in liq.items()
             if v >= market.LIQUIDITY_LOTS and x in a and x in b and a[x][3] and b[x][3]]
    ew = statistics.mean(moves) * 100 if moves else 0.0
    etf = (b['0050'][3] / a['0050'][3] - 1) * 100 if '0050' in a and '0050' in b else 0.0
    return ew, etf
