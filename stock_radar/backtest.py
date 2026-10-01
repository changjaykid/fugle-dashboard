"""Replay the stored market against the real rules, so the exit parameters are measured rather than chosen.

Why this exists. On 2026-10-01 the agent had eleven trading days of evidence, and it said the picks
returned +1.35% over five days while the names it told the user not to chase returned +5.17%. Nineteen
samples decide nothing, and waiting for a paper account to accumulate enough would cost months before
anyone knew whether the exit rules were sane. The database already holds 250 trading days of full-market
bars and the matching daily P/E and P/B series, which is enough to ask the question now.

Every decision here comes from the production modules, not from a copy of them:
  valuation_engine.compute / market_scan.stock_valuation  -- the valuation and its bands
  market_scan.structure                                   -- supports, the 20-day low, the 60-day range
  buy_plan.levels                                         -- which band, which support to buy at
  valuation_quality.assess                                -- whether the model applies at all
  strategy / paper / costs                                -- the stop, the target, the size, the money
A backtest that re-implemented any of those would stop predicting the thing it is meant to predict.

What it cannot model, and therefore overstates:
  Institutional flow. The flow cache holds ten days, so `requires_stabilization` -- production's "below
  the 20-day average and foreigners plus trusts were net sellers, so wait" gate -- is never applied here.
  That gate only ever delays or cancels entries, so the backtest takes trades production would have
  skipped. This is the largest known optimism in these numbers.
  Trading restrictions and corporate events (the `risk` fact), and the 08:55 trial-price recheck.
  Dividends and splits: bars are as filed, so a position through an ex-dividend date shows the drop
  without the cash. That understates returns, in the opposite direction.

The earliest day it can value anything is governed by MIN_SAMPLES: the P/E median needs 120 days inside
the prior year, so with a cache starting 2025-09-17 nothing before roughly 2026-03 can be valued, and
the first weeks of that use a shorter window than production's full year. `--from-index` exists to run
the headline on the part of the window where the inputs match production.
"""
import argparse
import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

from .domain import TW
from .store import Store
from . import buy_plan, costs, market, market_scan, paper, strategy, valuation_quality

ETF_KINDS = ('etf_equity', 'etf_other')
DECAYING_SUFFIX = ('L', 'R', 'U')
STOCK_SLOTS = 5   # the pre-open message's own cap on stock picks (research_brief.MAX_STOCKS)
ETF_SLOTS = 3


def _roc(ds):
    d = date.fromisoformat(ds)
    return f'{d.year - 1911}年{d.month:02d}月{d.day:02d}日'


def inputs(store):
    """Everything the replay needs, read once: bars, ratio rows per symbol, instruments, financials."""
    prices = market.load(store, 'prices')
    ratios = market.load(store, 'ratios')
    days = [d for d, _ in prices]
    insts = {i['symbol']: i for i in store.instruments()
             if i.get('market') == 'TSE' and (i['kind'] == 'stock' or (
                 i['kind'] in ETF_KINDS and not i['symbol'].endswith(DECAYING_SUFFIX)))}
    # One pre-built TWSE-shaped row list per symbol, so each day is a slice rather than a rebuild.
    rows = {}
    for day, table in ratios:
        for sym, r in table.items():
            rows.setdefault(sym, []).append([_roc(day), str(r[2] or ''), '', str(r[0] or '-'),
                                             str(r[1] or '-'), ''])
    fins = {sym: store.fact(sym, 'financials') for sym in insts}
    return {'prices': prices, 'days': days, 'ratios': ratios, 'rows': rows,
            'insts': insts, 'fins': fins}


def strength_picks_on(data, k, settings=None):
    """The strength model's list for day k: the whole liquid universe ranked by range position.

    Deliberately not valued. The scan that produced this model ranked on price structure alone, and
    adding a valuation gate on top would be testing a third thing while claiming to test the second."""
    from . import entry as entry_model
    prices, days = data['prices'], data['days']
    window = prices[:k + 1]
    liquid = market.liquidity(window)
    out = []
    for sym, inst in data['insts'].items():
        if liquid.get(sym, 0) < market.LIQUIDITY_LOTS:
            continue
        bars = [prices[j][1].get(sym) for j in range(max(0, k - 59), k + 1)]
        if not bars or not bars[-1]:
            continue
        pick, why = entry_model.strength_candidate(sym, inst.get('name') or sym, inst['kind'],
                                                   bars, settings)
        if pick:
            out.append({**pick, 'band': 'strength', 'discount': pick['rank']})
    out.sort(key=lambda x: -x['rank'])
    slots = {**entry_model.SETTINGS, **(settings or {})}['slots']
    return out[:slots]


def picks_on(data, k):
    """What the close of day k put in the buy zone, cheapest first. Independent of the exit settings.

    Mirrors the nightly scan: liquidity gate, then valuation, then buy_plan's own geometry. Separated
    from the strategy so a parameter sweep pays for the valuations once instead of once per run -- the
    whole replay costs about 90 seconds, and nearly all of it is here."""
    prices, days = data['prices'], data['days']
    window = prices[:k + 1]
    liquid = market.liquidity(window)
    out = []
    for sym, inst in data['insts'].items():
        if liquid.get(sym, 0) < market.LIQUIDITY_LOTS:
            continue
        bar = prices[k][1].get(sym)
        if not bar or not bar[3]:
            continue
        close = {'close': bar[3], 'trade_date': days[k]}
        struct = market_scan.structure(sym, window)
        if not struct:
            continue
        if inst['kind'] == 'stock':
            v, _ = market_scan.stock_valuation(inst, close, data['fins'].get(sym),
                                               data['ratios'][:k + 1])
        else:
            v, _ = market_scan.etf_valuation(inst, close, window)
        if not v:
            continue
        if valuation_quality.assess(inst, v, struct):
            continue
        n = bar[3]
        band = next((key for key in ('sweet', 'add', 'buy') if n <= v[key]), 'avoid')
        plan = buy_plan.levels(inst, v, n, struct, band)
        if plan.get('status') != 'conditional' or not plan.get('entry'):
            continue
        out.append({'symbol': sym, 'name': inst.get('name') or sym, 'kind': inst['kind'],
                    'discount': (v['fair'] - n) / v['fair'] if v.get('fair') else 0,
                    'band': band, 'support': plan['support'], 'entry': plan['entry'],
                    'fair': v.get('fair'), 'buy_max': v['buy'], 'resistance': struct['resistance'],
                    'close': n})
    out.sort(key=lambda x: -x['discount'])
    return ([x for x in out if x['kind'] == 'stock'][:STOCK_SLOTS]
            + [x for x in out if x['kind'] != 'stock'][:ETF_SLOTS])


def scan(data, start, cache=None, model='value_band', settings=None):
    """Every day's candidate list, computed once. Written to `cache` so a sweep reuses it."""
    if model == 'strength':
        return {k: strength_picks_on(data, k, settings) for k in range(start, len(data['days']))}
    if cache and Path(cache).is_file():
        stored = json.loads(Path(cache).read_text())
        if stored.get('days') == data['days'][start:]:
            return {int(k): v for k, v in stored['picks'].items()}
    picks = {k: picks_on(data, k) for k in range(start, len(data['days']))}
    if cache:
        Path(cache).write_text(json.dumps({'days': data['days'][start:],
                                           'picks': {str(k): v for k, v in picks.items()}},
                                          ensure_ascii=False))
    return picks


def orders_from(picks, settings):
    """Apply the exit geometry to a day's list, dropping what cannot be priced under these settings."""
    out = []
    for pick in picks:
        geometry, _ = strategy.plan_for(pick['entry'], pick['support'], pick.get('fair'), settings)
        if geometry:
            out.append({**pick, **geometry, 'fill': pick.get('fill', 'limit')})
    return out


def run(store, settings=None, from_index=None, verbose=False, data=None, picks=None,
        source='replay', to_index=None):
    settings = {**strategy.SETTINGS, **(settings or {})}
    data = data or inputs(store)
    days = data['days']
    # The first day the P/E median can be built from the required number of samples.
    start = from_index if from_index is not None else 120
    picks = picks if picks is not None else scan(data, start, model=settings.get('entry_model', 'value_band'))
    book = paper.Book(settings.get('starting_cash', 100000), started_day=days[start])
    orders, log = [], []
    # `to_index` lets the replay stop short of the newest bar, so the live driver has a day left to
    # advance against real data -- which is the only way to test the live path end to end.
    stop = len(days) if to_index is None else min(to_index + 1, len(days))
    for k in range(start, stop):
        day = days[k]
        bars = data['prices'][k][1]
        at = f'{day}T13:30:00+08:00'
        book.settle(day)

        # 1. Yesterday's list becomes today's limit orders.
        for order in orders:
            if len(book.positions) >= settings['max_positions']:
                break
            if order['symbol'] in book.positions:
                continue
            bar = bars.get(order['symbol'])
            if not bar:
                continue
            # Production's 08:55 recheck blocks a pick that has already broken its support.
            if bar[0] and bar[0] < order['support']:
                continue
            # A market-on-open order pays the open; a limit waits for the price to come to it.
            fill = bar[0] if order.get('fill') == 'open' and bar[0] else book.fill_price(bar, order['entry'], 'buy')
            if fill is None:
                continue
            shares, why = strategy.size_for(book.equity(), book.available(), fill, order['stop'], settings)
            if not shares:
                continue
            position = book.buy(order['symbol'], order['name'], order['kind'], shares, fill, day, at,
                                plan={'stop': order['stop'], 'target': order['target'],
                                      'support': order['support'], 'fair': order['fair'],
                                      'r_multiple': order['r_multiple'], 'band': order['band'],
                                      'exit_mode': order.get('exit_mode')},
                                entry_index=k)
            position['source'] = source
            book.trades[-1]['source'] = source
            log.append((day, 'buy', order['symbol'], fill, shares))
        orders = []

        # 2. Exits, on today's own bar. The stop is ratcheted first, then tested.
        for symbol in list(book.positions):
            bar = bars.get(symbol)
            if not bar:
                continue
            position = book.positions[symbol]
            position['stop_now'] = strategy.ratchet(position, bar, settings)
            leave = strategy.exit_check(position, bar, k - position['entry_index'], settings)
            if leave:
                release = days[min(k + paper.SETTLEMENT_DAYS, len(days) - 1)]
                record = book.sell(symbol, leave['price'], day, at, leave['reason'],
                                   release_day=release, exit_index=k)
                record['source'] = source
                book.trades[-1]['source'] = source
                log.append((day, 'sell:' + leave['reason'], symbol, leave['price'], record['profit']))

        # 3. Mark, then build tomorrow's list.
        book.mark(bars, day)
        if len(book.positions) < settings['max_positions'] and k + 1 < stop:
            orders = orders_from(picks.get(k, []), settings)
        if verbose and (k - start) % 20 == 0:
            print(f'  {day}  淨值 {book.net_equity():,.0f}  持倉 {len(book.positions)}  已結 {len(book.closed)}')
    return book, log, days[start], days[stop - 1]


def report(book, first, last, settings):
    p = paper.performance(book)
    held = [c['held_days'] for c in book.closed if c['held_days'] is not None]
    lines = [f"回測 {first} ～ {last}",
             f"起始 {p['starting_cash']:,.0f} → 淨值 {p['net_equity']:,.0f}（{p['total_return_pct']:+.2f}%，已扣手續費與證交稅）",
             f"已結交易 {p['closed_trades']} 筆｜勝 {p['wins']} 敗 {p['losses']}"
             + (f"｜勝率 {p['win_rate_pct']:.0f}%" if p['win_rate_pct'] is not None else ''),
             f"手續費與稅合計 {p['fees_and_tax']:,.0f} 元"
             + (f"（佔起始資金 {p['fees_and_tax'] / p['starting_cash'] * 100:.2f}%）" if p['starting_cash'] else ''),
             f"出場原因：{p['exit_reasons'] or '無'}"]
    if p.get('average_win_pct') is not None:
        lines.append(f"平均獲利 {p['average_win_pct']:+.2f}%｜平均虧損 {p.get('average_loss_pct', 0):+.2f}%")
    if held:
        lines.append(f"平均持有 {statistics.mean(held):.0f} 個交易日（中位數 {statistics.median(held):.0f}）")
    lines.append(f"目前現金 {p['cash']:,.0f}｜未交割 {p['unsettled']:,.0f}｜持倉市值 {p['holdings_value']:,.0f}")
    lines.append(f"參數：目標 {settings['target_r_multiple']}R、停損下限 {settings['min_stop_fraction']:.0%}、"
                 f"上限 {settings['max_stop_fraction']:.0%}、持有上限 {settings['max_holding_days']} 日、"
                 f"保本啟動 {settings['breakeven_after_gain']:.0%}")
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--from-index', type=int)
    p.add_argument('--set', action='append', default=[], help='覆寫一個 SETTINGS 鍵，例如 target_r_multiple=2.5')
    p.add_argument('--json', action='store_true')
    p.add_argument('--trades', action='store_true')
    p.add_argument('--verbose', action='store_true')
    p.add_argument('--cache', type=Path, help='存放每日買進區清單，供參數掃描重用')
    a = p.parse_args(argv)
    override = {}
    for item in a.set:
        key, _, value = item.partition('=')
        override[key] = float(value) if '.' in value or 'e' in value.lower() else int(value)
    settings = {**strategy.SETTINGS, **override}
    s = Store(a.db)
    try:
        data = inputs(s)
        start = a.from_index if a.from_index is not None else 120
        book, log, first, last = run(s, settings, a.from_index, a.verbose, data,
                                     scan(data, start, a.cache))
        if a.json:
            print(json.dumps({'first': first, 'last': last, 'settings': override,
                              **paper.performance(book)}, ensure_ascii=False))
        else:
            print(report(book, first, last, settings))
        if a.trades:
            print()
            for c in sorted(book.closed, key=lambda x: x['exit_day']):
                print(f"  {c['entry_day']} → {c['exit_day']}  {c['symbol']} {c['name'][:6]}  "
                      f"{c['shares']}股 {c['entry']:g}→{c['exit']:g}  {c['profit']:+,.0f} 元 "
                      f"({c['return_pct']:+.2f}%)  {c['exit_reason']}")
    finally:
        s.close()


if __name__ == '__main__':
    main()
