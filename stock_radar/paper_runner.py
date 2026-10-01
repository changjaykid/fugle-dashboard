"""One trading day through the simulated account, run by the close job. No LLM, no network.

This is the live driver of the same `paper.Book` the replay uses, so the record the user reads forward
and the record the replay produced are the same kind of object, produced by the same arithmetic. The
only difference is that this one advances a day at a time and cannot see the future.

The day's sequence, which mirrors the replay exactly:

  settle     credit sale proceeds whose T+2 has arrived.
  fill       yesterday's limit orders, against today's real bar, only at prices the day reached.
  exit       ratchet each stop, then test it; a bar spanning both stop and target counts as the stop.
  mark       value what is still held at today's close.
  queue      decide tomorrow's limit orders from today's close.

Why the account acts at the close rather than at 08:55. The orders are decided from closing bars and
filled against the next day's bar, which is exactly what the replay did -- so a live week and a replayed
week are comparable. Driving it from the pre-open trial price would make the live record incomparable
with the only evidence base this strategy has.

It is idempotent per trading day: a second run on the same day does nothing, so a retried close job
cannot trade twice.
"""
import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .calendar import is_trading_day
from .domain import TW
from .store import Store
from . import backtest, buy_plan, market, paper, strategy


def trading_days_between(start, end, schedule):
    """Sessions from `start` (exclusive) to `end` (inclusive). The live account outlives the price cache."""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if b <= a:
        return 0
    count, cursor = 0, a + timedelta(days=1)
    while cursor <= b:
        if is_trading_day(cursor, schedule):
            count += 1
        cursor += timedelta(days=1)
    return count


def next_trading_day(day, schedule, skip=1):
    cursor = date.fromisoformat(day)
    while skip:
        cursor += timedelta(days=1)
        if is_trading_day(cursor, schedule):
            skip -= 1
    return str(cursor)


def benchmark(store, since):
    """What an equal-weight basket of the whole tradable universe did over the same period.

    An absolute return means nothing on its own. The replay returned +34.17% while this benchmark
    returned +27.92% over the same days, and the 108-cell sweep's median returned +5.71% against the
    same +27.92% -- the comparison is the only thing that distinguishes a strategy from a rising market,
    so it travels with every number this account reports."""
    prices = market.load(store, 'prices', since=since)
    start = next((k for k, (d, _) in enumerate(prices) if d >= since), None)
    if start is None or start >= len(prices) - 1:
        return None
    liquid = market.liquidity(prices[:start + 1])
    first, last = prices[start][1], prices[-1][1]
    moves = [last[sym][3] / first[sym][3] - 1 for sym, lots in liquid.items()
             if lots >= market.LIQUIDITY_LOTS and sym in first and sym in last
             and first[sym][3] and last[sym][3]]
    if not moves:
        return None
    moves.sort()
    return {'from': prices[start][0], 'to': prices[-1][0], 'symbols': len(moves),
            'equal_weight_pct': round(sum(moves) / len(moves) * 100, 2),
            'median_pct': round(moves[len(moves) // 2] * 100, 2)}


def advance(store, book, day, bars, orders, settings, source, schedule, data, index):
    """One trading day against one day's real bars. Returns what happened and tomorrow's orders."""
    at = f'{day}T13:30:00+08:00'
    out = {'day': day, 'settled': [], 'bought': [], 'sold': [], 'stale_marks': [], 'queued': []}
    out['settled'] = [p['symbol'] for p in book.settle(day)]

    # 1. Yesterday's orders, against today's real bar.
    for order in orders:
        if len(book.positions) >= settings['max_positions'] or order['symbol'] in book.positions:
            continue
        bar = bars.get(order['symbol'])
        if not bar:
            continue
        if bar[0] and order.get('support') and bar[0] < order['support']:
            out['queued'].append({'symbol': order['symbol'], 'skipped': 'opened_below_support'})
            continue
        fill = book.fill_price(bar, order['entry'], 'buy')
        if fill is None:
            continue
        shares, why = strategy.size_for(book.equity(), book.available(), fill, order.get('stop'),
                                        settings, order.get('kind', 'stock'))
        if not shares:
            out['queued'].append({'symbol': order['symbol'], 'skipped': why})
            continue
        position = book.buy(order['symbol'], order['name'], order['kind'], shares, fill, day, at,
                            plan={k: order.get(k) for k in
                                  ('stop', 'target', 'support', 'fair', 'r_multiple', 'band',
                                   'exit_mode')})
        position['source'] = source
        book.trades[-1]['source'] = source
        out['bought'].append({'symbol': order['symbol'], 'name': order['name'], 'shares': shares,
                              'price': fill, 'value': round(shares * fill, 2),
                              'order_type': position['order_type'],
                              'stop': order.get('stop'), 'target': order.get('target')})

    # 2. Exits.
    for symbol in list(book.positions):
        bar = bars.get(symbol)
        if not bar:
            continue
        position = book.positions[symbol]
        position['stop_now'] = strategy.ratchet(position, bar, settings)
        held = trading_days_between(position['entry_day'], day, schedule)
        leave = strategy.exit_check(position, bar, held, settings)
        if not leave:
            continue
        record = book.sell(symbol, leave['price'], day, at, leave['reason'],
                           release_day=next_trading_day(day, schedule, paper.SETTLEMENT_DAYS))
        record['held_days'] = held
        record['source'] = source
        book.trades[-1]['source'] = source
        out['sold'].append({'symbol': symbol, 'name': record['name'], 'shares': record['shares'],
                            'price': leave['price'], 'reason': leave['reason'], 'why': leave['why'],
                            'profit': record['profit'], 'return_pct': record['return_pct'],
                            'held_days': held})

    # 3. Mark, then queue tomorrow.
    out['stale_marks'] = book.mark(bars, day)
    next_orders = []
    if len(book.positions) < settings['max_positions']:
        next_orders = [{k: o.get(k) for k in ('symbol', 'name', 'kind', 'entry', 'stop', 'target',
                                              'support', 'fair', 'band', 'r_multiple', 'exit_mode')}
                       for o in backtest.orders_from(backtest.picks_on(data, index), settings)]
        out['queued'] += [{'symbol': o['symbol'], 'name': o['name'], 'entry': o['entry'],
                           'stop': o['stop'], 'target': o['target']} for o in next_orders]
    return out, next_orders


def session(store, now, settings=None, source='live'):
    """Advance the account through every cached trading day it has not processed yet.

    Not just the newest one. On 2026-10-01 the close job died on its first step (sync-universe, a
    ConnectionReset from the exchange) and never fetched that day's bars; the next close would have
    found two unprocessed days in the cache and silently acted on only the later one, losing a day of
    fills and exits. A simulation that skips days is not simulating anything, so missed days are caught
    up in order, each against its own bars."""
    settings = {**strategy.SETTINGS, **(settings or {})}
    schedule = store.meta('trading_calendar')
    today = str(buy_plan.last_completed_day(now, schedule))
    state = store.meta('paper_runner') or {}
    last = state.get('last_day')
    if last and last >= today:
        return {'status': 'already_done', 'day': today, 'last_day': last}

    # Enough to cover any run of missed sessions without reading the whole archive.
    prices = market.load(store, 'prices', market.SCAN_DAYS)
    days = [d for d, _ in prices]
    pending = [d for d in days if (not last or d > last) and d <= today]
    if not pending:
        # Acting on stale bars would invent fills. The account simply does not move.
        return {'status': 'no_bars', 'day': today, 'have': days[-1] if days else None,
                'last_day': last}

    book = paper.Book.load(store) or paper.Book(settings['starting_cash'], started_day=pending[0])
    data = backtest.inputs(store)
    orders = state.get('orders') or []
    steps = []
    for day in pending:
        index = days.index(day)
        result, orders = advance(store, book, day, prices[index][1], orders, settings, source,
                                 schedule, data, index)
        steps.append(result)

    merge = lambda key: [item for step in steps for item in step[key]]
    out = {'status': 'ok', 'day': pending[-1], 'source': source, 'days': pending,
           'caught_up': pending[:-1], 'settled': merge('settled'), 'bought': merge('bought'),
           'sold': merge('sold'), 'stale_marks': steps[-1]['stale_marks'],
           'queued': steps[-1]['queued'],
           # The cache may still be behind the market when an upstream fetch failed earlier.
           'behind': None if pending[-1] == today else today}

    book.save(store)
    store.set_meta('paper_runner', {'at': now.isoformat(), 'last_day': pending[-1], 'orders': orders,
                                    'source': source, 'strategy_version': strategy.VERSION,
                                    'settings': {k: settings[k] for k in sorted(strategy.SETTINGS)}})
    out['performance'] = paper.performance(book)
    out['benchmark'] = benchmark(store, book.started_day)
    store.set_meta('paper_performance', {'at': now.isoformat(), 'day': pending[-1], **out['performance'],
                                         'benchmark': out['benchmark'],
                                         'started_day': book.started_day,
                                         'seeded_from': (store.meta('paper_runner') or {}).get('seeded_from')})
    return out


def text(result, store=None):
    """Answer first: what the account is worth, then what it did today. Nothing else."""
    if result['status'] != 'ok':
        reason = {'already_done': '今天已經結算過', 'no_bars': '今天的收盤 K 線尚未入庫，帳戶不動'}
        return f"模擬帳戶 {result['day']}：{reason.get(result['status'], result['status'])}"
    p = result['performance']
    sign = '+' if p['total_return_pct'] >= 0 else ''
    caught = result.get('caught_up') or []
    lines = [f"模擬帳戶 {result['day']}（起始 {p['starting_cash']:,.0f} 元）"
             + (f"｜補跑 {len(caught) + 1} 個交易日：{'、'.join(result['days'])}" if caught else ''),
             f"淨值 {p['net_equity']:,.0f} 元（{sign}{p['total_return_pct']:.2f}%，已扣手續費與證交稅）"]
    bench = result.get('benchmark')
    if bench:
        gap = p['total_return_pct'] - bench['equal_weight_pct']
        lines.append(f"同期大盤同樣條件：全市場等權 {bench['equal_weight_pct']:+.2f}%"
                     f"（{bench['symbols']} 檔）→ 超額 {gap:+.2f} 個百分點")
    if result['sold']:
        for x in result['sold']:
            lines.append(f"賣出 {x['name']} {x['symbol']}｜{x['shares']} 股 @ {x['price']:g}"
                         f"｜{x['profit']:+,.0f} 元（{x['return_pct']:+.2f}%）"
                         f"｜持有 {x['held_days']} 日｜{x['why']}")
    if result['bought']:
        for x in result['bought']:
            kind = '整股' if x['order_type'] == 'lot' else '零股'
            lines.append(f"買進 {x['name']} {x['symbol']}｜{x['shares']} 股（{kind}）@ {x['price']:g}"
                         f"｜{x['value']:,.0f} 元｜停損 {x['stop']:g} 目標 {x['target']:g}")
    if not result['sold'] and not result['bought']:
        lines.append('今天沒有成交。')
    if p['closed_trades']:
        lines.append(f"累計已結 {p['closed_trades']} 筆，勝 {p['wins']} 敗 {p['losses']}"
                     f"（勝率 {p['win_rate_pct']:.0f}%），已付手續費與稅 {p['fees_and_tax']:,.0f} 元")
    lines.append(f"現金 {p['cash']:,.0f}｜未交割 {p['unsettled']:,.0f}｜持倉 {p['open_positions']} 檔"
                 f"，市值 {p['holdings_value']:,.0f}")
    if result['queued']:
        ready = [q for q in result['queued'] if q.get('entry')]
        if ready:
            lines.append('明天掛單：' + '、'.join(f"{q['name']} {q['entry']:g}" for q in ready[:5]))
    if result['stale_marks']:
        lines.append('無當日行情、沿用前一標價：' + '、'.join(result['stale_marks']))
    if result.get('behind'):
        lines.append(f"注意：{result['behind']} 的收盤 K 線尚未入庫，帳戶只推進到 {result['day']}。")
    return '\n'.join(lines)


def seed(store, now, settings=None, from_index=None, to_index=None):
    """Start the account from the replay of the stored history instead of from an empty book.

    The alternative is an account with no trades in it for months, which answers none of the questions
    the user asked it to answer. Every row the replay writes carries source='replay' so it can never be
    read as something that happened in real time, and the live driver appends source='live' from the
    next close onwards. The engine, the bars, the fills and the costs are identical either way."""
    settings = {**strategy.SETTINGS, **(settings or {})}
    if store.meta('paper_runner'):
        return {'status': 'already_started', 'runner': store.meta('paper_runner').get('last_day')}
    book, log, first, last = backtest.run(store, settings, from_index, source='replay',
                                         to_index=to_index)
    book.save(store)
    store.set_meta('paper_runner', {'at': now.isoformat(), 'last_day': last, 'orders': [],
                                   'source': 'replay', 'seeded_from': first,
                                   'strategy_version': strategy.VERSION,
                                   'settings': {k: settings[k] for k in sorted(strategy.SETTINGS)}})
    performance = paper.performance(book)
    store.set_meta('paper_performance', {'at': now.isoformat(), 'day': last, **performance})
    return {'status': 'seeded', 'from': first, 'to': last, 'trades': len(book.trades),
            'closed': len(book.closed), 'performance': performance}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--seed', action='store_true', help='以儲存的歷史回放開帳，只能做一次')
    p.add_argument('--from-index', type=int)
    p.add_argument('--to-index', type=int, help='回放到這一天為止，留下後面的日子給每日結算')
    p.add_argument('--json', action='store_true')
    p.add_argument('--out', type=Path, help='把今天的帳戶摘要寫成 JSON，供收盤檢討與網站讀取')
    a = p.parse_args(argv)
    s = Store(a.db)
    try:
        now = datetime.now(TW)
        if a.seed:
            result = seed(s, now, from_index=a.from_index, to_index=a.to_index)
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return
        result = session(s, now)
        body = text(result, s)
        if a.out:
            from .store import atomic_json
            atomic_json(a.out, {'generated_at': now.isoformat(), 'day': result['day'],
                                'status': result['status'], 'text': body,
                                'performance': result.get('performance'),
                                'benchmark': result.get('benchmark'),
                                'bought': result.get('bought'), 'sold': result.get('sold')})
        print(json.dumps(result, ensure_ascii=False) if a.json else body)
    finally:
        s.close()


if __name__ == '__main__':
    main()
