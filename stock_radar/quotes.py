"""Real-time-ish quotes for TSE/OTC stocks and equity ETFs.

Source: https://mis.twse.com.tw/stock/api/getStockInfo.jsp (TWSE's own
public "basic market information" site -- the same feed https://mis.twse.com.tw
displays in the browser). Free, no key, covers both TSE (`tse_` prefix) and
OTC/TPEx (`otc_` prefix) symbols in one call.

Verified reachable via Python `requests` with default TLS verification
(2026-09-10). NOT the same host as www.tpex.org.tw (which currently fails
Python's SSL verification -- see fetchers.py for that workaround); this
mis.twse.com.tw endpoint works fine directly.

IMPORTANT / OPEN GAP (documented per Kid's instruction "not to fake trial
price by clock"): the JSON payload from this endpoint does not carry an
explicit, unambiguous "this price is a pre-market trial-match price" flag.
There IS a real trial-match session 08:30-09:00 Asia/Taipei during which
TWSE performs actual trial matching and this endpoint reflects it, but we
have not found a documented boolean field that distinguishes trial vs.
regular-session price at the API level (only wall-clock time would tell us,
and STOCK_RADAR_SPEC.md explicitly forbids inferring is_trial from the
clock). Until a reliable signal is found and verified, `is_trial` is always
reported as None (unknown) and `trial_price` is always None from this
fetcher, so the pre-open message never reads a pre-9am quote from here as
a trial price. This is a known
limitation, not a silent guess.

See stock_radar/fugle.py for a real data-driven trial-match source
(Fugle's lastTrial/lastTrade split, keyed off venue timestamps, not the
clock) -- once a working Fugle API key is confirmed, that module should
replace this one as the primary quote source and this module demoted to a
tested fallback only.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Iterable

TW = timezone(timedelta(hours=8))
MIS_URL = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; StockRadar/1.0)'}


def _num(s):
    try:
        if s in (None, '', '-', '0.00', '0'):
            return None
        v = float(s)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _levels(raw: str) -> list[dict]:
    """Parse mis.twse's underscore-joined 5-level price string like
    '109.15_109.20_109.25_109.30_109.35_' into [{'price':109.15}, ...].
    Sizes come from a separate field (`f`/`g`) with matching underscore
    layout; callers pair prices with sizes by index."""
    if not raw:
        return []
    parts = [p for p in raw.split('_') if p]
    return [_num(p) for p in parts]


def _book(price_str, size_str):
    prices = _levels(price_str)
    sizes = _levels(size_str)
    out = []
    for p, s in zip(prices, sizes):
        if p is not None and s is not None:
            out.append({'price': p, 'size': s})
    return out


def build_symbol_key(symbol: str, market: str) -> str:
    prefix = {'TSE': 'tse', 'OTC': 'otc'}[market]
    return f'{prefix}_{symbol}.tw'


def fetch_quotes(instruments: Iterable[dict], *, session=None, timeout=15, batch_size=100) -> dict[str, dict]:
    """instruments: iterable of {'symbol':..., 'market': 'TSE'|'OTC'}.
    Returns {symbol: quote_dict}. Batches requests (mis.twse accepts many
    `|`-joined ex_ch values per call) to avoid one request per symbol.
    Raises on HTTP/network failure -- caller must record a health entry
    rather than silently reuse stale data."""
    import requests
    http = session or requests
    items = list(instruments)
    out = {}
    now = datetime.now(TW)
    for start in range(0, len(items), batch_size):
        chunk = items[start:start + batch_size]
        keys = [build_symbol_key(i['symbol'], i['market']) for i in chunk]
        resp = http.get(MIS_URL, params={'ex_ch': '|'.join(keys), 'json': 1, 'delay': 0},
                         headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        for row in payload.get('msgArray', []):
            symbol = row.get('c')
            if not symbol:
                continue
            price = _num(row.get('z')) or _num(row.get('pz'))
            trade_date = row.get('d')
            trade_date_iso = None
            if trade_date and len(trade_date) == 8:
                trade_date_iso = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}'
            out[symbol] = {
                'symbol': symbol,
                'name': row.get('n'),
                'as_of': now.isoformat(),
                'trade_date': trade_date_iso,
                'is_trial': None,  # see module docstring: not reliably known from this feed
                'price': price,
                'previous_close': _num(row.get('y')),
                'reference_price': _num(row.get('y')),  # mis.twse does not separately expose an
                                                          # adjusted reference price distinct from
                                                          # previous close; corporate-action deltas
                                                          # must be reconciled via facts, not this feed
                'limit_up': _num(row.get('u')),
                'limit_down': _num(row.get('w')),
                'book_as_of': now.isoformat(),
                'bids': _book(row.get('b'), row.get('g')),
                'asks': _book(row.get('a'), row.get('f')),
                'halted': None,   # no explicit halt flag observed in this feed; leave unknown
                'disposition': None,
                'source': 'mis.twse.com.tw getStockInfo.jsp',
                'source_url': MIS_URL,
            }
    return out
