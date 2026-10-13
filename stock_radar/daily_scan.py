"""The nightly scan that decides what is recommended and what the simulated account orders. No LLM.

It runs `swing.scan` (the one set of entry and exit rules) over the last completed session and stores the
result as the meta row `daily_scan`. Three things read it: the pre-open message, the simulated account and
the website. Idempotent per day.

Risk facts are read here and not in a replay: today's disposition status is exactly what a decision
about today should use, and exactly what a decision about a day in 2023 must not.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from .domain import TW
from .store import Store
from . import buy_plan, market, swing


def run(store, now, settings=None):
    schedule = store.meta('trading_calendar')
    day = str(buy_plan.last_completed_day(now, schedule))
    prices = market.split_adjust(market.load(store, 'prices', market.SCAN_DAYS))
    if not prices or prices[-1][0] != day:
        return {'status': 'no_bars', 'day': day, 'have': prices[-1][0] if prices else None}
    flows = market.load(store, 'flows', 10)
    instruments = {i['symbol']: i for i in store.instruments() if i['kind'] == 'stock'}
    result = swing.scan(prices[-130:], flows, instruments,
                        risk=lambda symbol: store.fact(symbol, 'risk'), settings=settings)
    store.set_meta('daily_scan', {**result, 'at': now.isoformat()})
    return {'status': 'ok', 'day': day, 'market_open': result['market_open'],
            'candidates': len(result['candidates']), 'tally': result['tally']}


def text(store):
    """The scan in a few lines, for the log and for a human reading the job output."""
    payload = store.meta('daily_scan') or {}
    if not payload:
        return '尚未產生任何掃描結果。'
    lines = [f"台股掃描｜{payload['day']} 收盤（{swing.VERSION}）"]
    if not payload['market_open']:
        lines.append('0050 在 120 日線之下：大盤走弱，今天不掛新單。')
    for c in payload['candidates']:
        lines.append(f"{c['name']} {c['symbol']}：掛 {c['limit']:g}（收盤 {c['close']:g}），"
                     f"上緣 {c['target']:g}，停損 {c['stop']:g}｜{c['why']}")
    if payload['market_open'] and not payload['candidates']:
        lines.append('今天沒有符合條件的標的。')
    t = payload['tally']
    lines.append(f"掃描 {t['universe']} 檔，可判斷 {t['judged']}，符合 {t['buy']}")
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--text', action='store_true')
    a = p.parse_args(argv)
    s = Store(a.db)
    try:
        result = run(s, datetime.now(TW))
        print(text(s) if a.text and result['status'] == 'ok' else json.dumps(result, ensure_ascii=False))
    finally:
        s.close()


if __name__ == '__main__':
    main()
