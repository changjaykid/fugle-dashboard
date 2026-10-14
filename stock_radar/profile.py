"""What a company does, in its own filing's words, for the stocks the page shows.

The page listed a name and a code. The user asked on 2026-10-15 to see the industry and what the
company does before deciding to buy. The industry is already on the instrument; the business line is
the 「主要經營業務」 field of the company's basic data on MOPS (公開資訊觀測站), which every listed
company files itself.

Fetched only for what the page and the account need -- tonight's candidates, the near misses, resting
orders and holdings, a few dozen at most -- one request a second, and kept as the fact `profile` for
ninety days, since a company's line of business does not change week to week. A failed fetch leaves
the previous text, or none; it never blocks the night's work.
"""
import argparse
import html
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from .domain import TW
from .store import Store

URL = 'https://mopsov.twse.com.tw/mops/web/ajax_t05st03'
KEEP_DAYS = 90
PAUSE = 1.0
FIELD = re.compile(r'主要經營業務\s*</th>\s*<td[^>]*>(.*?)</td>', re.S)


def parse(page):
    """The main-business text from a t05st03 page, tidied to one line, or None."""
    match = FIELD.search(page)
    if not match:
        return None
    text = html.unescape(re.sub(r'<[^>]+>', ' ', match.group(1)))
    text = re.sub(r'\s+', ' ', text).strip(' 　')
    return text[:200] or None


def fetch(symbol, opener=None):
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode({'encodeURIComponent': '1', 'step': '1', 'firstin': '1', 'off': '1',
                                   'queryName': 'co_id', 'inpuType': 'co_id', 'TYPEK': 'all',
                                   'co_id': symbol}).encode()
    request = urllib.request.Request(URL, data=data, headers={'User-Agent': 'Mozilla/5.0 (StockRadar)'})
    raw = (opener or urllib.request.urlopen)(request, timeout=20).read()
    return parse(raw.decode('utf-8', 'replace'))


def wanted(store):
    """Every symbol the page or the account shows."""
    scan = store.meta('daily_scan') or {}
    symbols = [c['symbol'] for key in ('candidates', 'held_back', 'near') for c in scan.get(key) or []]
    runner = store.meta('paper_runner') or {}
    symbols += [o['symbol'] for o in runner.get('orders') or []]
    from .paper import Book
    book = Book.load(store)
    symbols += list(book.positions) if book else []
    return list(dict.fromkeys(symbols))


def refresh(store, now, fetcher=fetch, pause=PAUSE):
    done, failed, kept = [], [], 0
    for symbol in wanted(store):
        known = store.fact(symbol, 'profile') or {}
        at = known.get('fetched_at')
        if at and datetime.fromisoformat(at) > now - timedelta(days=KEEP_DAYS):
            kept += 1
            continue
        try:
            business = fetcher(symbol)
        except Exception as error:
            failed.append(f'{symbol}:{type(error).__name__}')
            continue
        finally:
            time.sleep(pause)
        if business:
            store.set_fact(symbol, 'profile', {'business': business, 'fetched_at': now.isoformat(),
                                               'source': URL})
            done.append(symbol)
        else:
            failed.append(f'{symbol}:no_field')
    return {'status': 'ok', 'fetched': done, 'kept': kept, 'failed': failed}


def business(store, symbol):
    return (store.fact(symbol, 'profile') or {}).get('business')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, required=True)
    a = p.parse_args(argv)
    s = Store(a.db)
    try:
        print(json.dumps(refresh(s, datetime.now(TW)), ensure_ascii=False))
    finally:
        s.close()


if __name__ == '__main__':
    main()
