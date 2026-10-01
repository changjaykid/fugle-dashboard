"""Walk the exchange backwards and keep every trading day it will give us.

The research that needed this: 250 cached trading days are one market regime -- 2026-03 to 09, in which
0050 returned 47.05% while the median liquid stock returned 13.41%. Momentum wins in a trend and value
loses in one, so a factor measured only there has been measured once. Everything the entry-rule research
could establish, it established; what it could not do is tell the difference between a signal and that
six months.

Cost, measured rather than guessed: prices and ratios are about 22 MB a year, so five years is roughly
112 MB against a database that is already 60 MB. Two requests per trading day at the same 2.5 second
pause market.sync uses is about five seconds a day, so five years is about an hour and three quarters,
once, in the background. The analysis afterwards is cheap and cached: a full scan of 130 days took 126
seconds and reruns in two.

It is resumable, it never refetches a day it already has, and it stops at the first stretch of days the
exchange has nothing for -- which is how it finds the beginning of the available history rather than
being told. Running it twice is safe. It touches only `market_days`, so nothing else in the database can
be affected by it, and the live pipeline keeps working while it runs.
"""
import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .domain import TW
from .store import Store
from . import market

PAUSE = 2.5
# Stop after this many consecutive requested days return nothing. Long enough to step over Lunar New
# Year, which closes the exchange for about nine days.
GIVE_UP_AFTER = 20


def fetch_day(http, kind, day):
    """One day of one dataset, or None when the exchange has nothing for that date.

    market.parse is the production parser and this uses it rather than a second copy: a backfill whose
    rows are shaped even slightly differently from the live sync's would poison every measurement taken
    on the combined history, and silently."""
    body = http.get(market.URLS[kind],
                    params={'response': 'json', 'date': day.strftime('%Y%m%d'), **market.PARAMS[kind]},
                    headers=market.HEADERS, timeout=30).json()
    return market.parse(kind, body, day)


def run(store, kinds=('prices', 'ratios'), years=5, pause=PAUSE, http=None, log=print):
    import requests
    http = http or requests
    oldest = min(min(market.stored(store, k) or {str(date.today())}) for k in kinds)
    cursor = date.fromisoformat(oldest) - timedelta(days=1)
    floor = date.today() - timedelta(days=int(years * 365.25))
    have = {k: market.stored(store, k) for k in kinds}
    added = {k: 0 for k in kinds}
    empty_run = 0
    log(json.dumps({'status': 'start', 'from': str(cursor), 'until': str(floor),
                    'already_have': {k: len(v) for k, v in have.items()}}, ensure_ascii=False), flush=True)
    while cursor >= floor and empty_run < GIVE_UP_AFTER:
        if cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
            continue
        day = str(cursor)
        got_any = False
        for kind in kinds:
            if day in have[kind]:
                got_any = True
                continue
            try:
                rows = fetch_day(http, kind, cursor)
            except Exception as error:
                log(json.dumps({'status': 'error', 'day': day, 'kind': kind,
                                'error_type': type(error).__name__}, ensure_ascii=False), flush=True)
                time.sleep(pause * 4)
                continue
            time.sleep(pause)
            if not rows:
                continue
            with store.transaction():
                store.db.execute('INSERT OR REPLACE INTO market_days VALUES(?,?,?)',
                                 (day, kind, json.dumps(rows, separators=(',', ':'))))
            have[kind].add(day)
            added[kind] += 1
            got_any = True
        empty_run = 0 if got_any else empty_run + 1
        if added['prices'] and added['prices'] % 50 == 0 and got_any:
            log(json.dumps({'status': 'progress', 'at': day, **added}, ensure_ascii=False), flush=True)
        cursor -= timedelta(days=1)
    log(json.dumps({'status': 'done', 'stopped_at': str(cursor), 'added': added,
                    'total': {k: len(v) for k, v in have.items()}}, ensure_ascii=False), flush=True)
    return added


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--years', type=float, default=5)
    p.add_argument('--pause', type=float, default=PAUSE)
    a = p.parse_args(argv)
    s = Store(a.db)
    try:
        run(s, years=a.years, pause=a.pause)
    finally:
        s.close()


if __name__ == '__main__':
    main()
