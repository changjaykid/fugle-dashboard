"""What the page shows: tonight's scan, the simulated account that trades it, and the health of the data
they were built from. Nothing else leaves the host.

The rules are swing.py (2026-10-13); the user asked on 2026-10-12 for one set of entry and exit rules, so
the valuation rows that once filled this payload are gone. Never private Fugle quotes: the scan and the
account are built from public closing data. The payload is still encrypted before it is published
(tools/encrypt_payload.py) -- GitHub Pages answers 200 to anyone with the URL.
"""
from __future__ import annotations

from . import daily_scan, paper_runner
from .calendar import is_trading_day
from .domain import TW
from .paper import Book, performance


def _account(store):
    runner = store.meta('paper_runner') or {}
    book = Book.load(store) if runner else None
    if not book:
        return None
    return {
        'started_day': book.started_day,
        'settings': runner.get('settings'),
        'strategy_version': runner.get('strategy_version'),
        'performance': performance(book),
        'benchmark': paper_runner.benchmark(store, book.started_day),
        'positions': list(book.positions.values()),
        'orders': runner.get('orders') or [],
        'unrealised': book.unrealised(),
        'trades': book.trades,
        'closed': book.closed,
    }


def _health(store, now):
    out = []
    try:
        is_trading_day(now.date(), store.meta('trading_calendar') or {})
    except ValueError as exc:
        out.append({'name': '交易日曆', 'status': 'blocked', 'detail': f'{exc} (run sync-calendar)', 'as_of': None})
    sync = store.meta('public_close_sync') or {}
    out.append({'name': '公開收盤', 'status': 'ok' if sync.get('at') else 'blocked',
                'detail': '證交所開放資料；每筆顯示實際交易日，不是即時行情。', 'as_of': sync.get('at')})
    return out


def build(store, now, mode='live'):
    now = now.astimezone(TW)
    return {
        'schema_version': 1,
        'mode': mode,
        'generated_at': now.isoformat(),
        'market_date': now.date().isoformat(),
        'health': _health(store, now),
        # The simulated account. None when it has not been started.
        'paper': _account(store),
        # Tonight's scan (swing.py): the orders it proposes and the levels they aim at.
        'scan': daily_scan.annotate(store, store.meta('daily_scan')) or None,
    }
