"""Fugle marketdata REST fetcher -- the only source with a real, data-driven
trial-match (試撮) signal and an explicit previousClose/referencePrice split.
Replaces mis.twse.com.tw (quotes.py) as the primary quote source once a
working API key is confirmed; quotes.py is kept only as an offline-tested
fallback whose own docstring already says it cannot separate
previous_close from reference_price and cannot detect trial-match state.

Endpoints (official schemas, verified against developer.fugle.tw docs
2026-09-10, both endpoints require `X-API-KEY`):
  GET https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/{symbol}
      -> previousClose, referencePrice, lastPrice, lastTrade{price,time},
         lastTrial{price,time}, bids, asks, isClose, lastUpdated
      Both previousClose and referencePrice are present in /quote itself
      (per the documented example payload) and are NOT assumed equal --
      unlike mis.twse.com.tw (quotes.py), which only exposes one field and
      forces that assumption, silently hiding ex-dividend/split deltas.
  GET https://api.fugle.tw/marketdata/v1.0/stock/intraday/ticker/{symbol}
      -> limitUpPrice, limitDownPrice, isDisposition, securityStatus
      (used only for limit-up/down and disposition flag; its own
      previousClose/referencePrice are ignored in favor of /quote's, so
      there is exactly one source of truth for that pair, not two that
      could silently drift apart)

Trial-match detection (data-driven, NOT clock-based, per
STOCK_RADAR_SPEC.md's explicit ban on inferring is_trial from wall-clock
time): compare `lastTrial.time` vs `lastTrade.time` from the /quote
response. If lastTrial exists and is the same or more recent than any
lastTrade (or no lastTrade exists yet today), the venue's own data says the
most recent observation is a trial match, so is_trial=True with
price/as_of taken from lastTrial. Otherwise is_trial=False using lastTrade.
If neither object is present, quote is unusable (as_of=None) rather than
guessed.

as_of / book_as_of: derived from the API's own `lastUpdated` /
lastTrade.time / lastTrial.time (microsecond epoch, per docs), NEVER from
our local fetch wall-clock -- a network delay or retry must not disguise
old venue data as fresh. `fetched_at` is recorded separately for
diagnostics only and is never read by domain.decide().
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

TW = timezone(timedelta(hours=8))
API_BASE = 'https://api.fugle.tw/marketdata/v1.0/stock'


def _epoch_micros_to_iso(value) -> str | None:
    """Fugle timestamps are documented as microsecond epoch integers
    (e.g. 1685338200000000). Reject anything that doesn't parse as a
    positive int rather than guessing a different unit."""
    try:
        micros = int(value)
    except (TypeError, ValueError):
        return None
    if micros <= 0:
        return None
    return datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc).astimezone(TW).isoformat()


def _num(v):
    try:
        n = float(v)
        return n if n == n else None  # reject NaN
    except (TypeError, ValueError):
        return None


def _headers(api_key: str) -> dict:
    return {'X-API-KEY': api_key, 'User-Agent': 'StockRadar/1.0'}


def fetch_ticker(symbol: str, api_key: str, *, session=None, timeout=15) -> dict:
    """Raises on HTTP failure (incl. 401/403/429) -- caller must record a
    health entry, never silently reuse a previous ticker response."""
    import requests
    http = session or requests
    resp = http.get(f'{API_BASE}/intraday/ticker/{symbol}', headers=_headers(api_key), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_quote(symbol: str, api_key: str, *, session=None, timeout=15) -> dict:
    import requests
    http = session or requests
    resp = http.get(f'{API_BASE}/intraday/quote/{symbol}', headers=_headers(api_key), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _book(levels) -> list[dict]:
    out = []
    for lvl in levels or []:
        p, s = _num(lvl.get('price')), _num(lvl.get('size'))
        if p is not None and s is not None:
            out.append({'price': p, 'size': s})
    return out


def build_quote(symbol: str, ticker_payload: dict, quote_payload: dict) -> dict:
    """Pure function: combine one /ticker + one /quote response into our
    internal quote shape. Kept separate from the network calls so this is
    fully unit-testable against the official documented example payloads
    without any mocking of requests.

    IMPORTANT (fixed per Codex review of an earlier draft): `price` and
    `trial_price` are populated INDEPENDENTLY from lastTrade/lastTrial --
    never collapsed into a single field gated by `is_trial`. domain.decide()
    reads `quote['trial_price']` during the pre-market window and
    `quote['price']` otherwise as two separate dict keys on the SAME quote
    object (see cli.py cmd_export: the raw stored quote is passed straight
    into decide(), it is not the export-reshaped view); a single shared
    `price` field would silently make every real trial-match tick
    undetectable by decide() even though the data was fetched correctly.
    `is_trial` is a data-driven corroborating flag (which of the two ticks
    is more recent per the venue's own timestamps), used by decide() as a
    cross-check against wall-clock time -- not as the sole discriminator
    for which price field holds data.
    """
    last_trial = quote_payload.get('lastTrial') or {}
    last_trade = quote_payload.get('lastTrade') or {}
    trial_time = last_trial.get('time')
    trade_time = last_trade.get('time')
    trial_price = _num(last_trial.get('price')) if trial_time is not None else None
    trade_price = _num(last_trade.get('price')) if trade_time is not None else None
    if trial_time is not None and (trade_time is None or int(trial_time) >= int(trade_time)):
        is_trial = True
        as_of = _epoch_micros_to_iso(trial_time)
    elif trade_time is not None:
        is_trial = False
        as_of = _epoch_micros_to_iso(trade_time)
    else:
        is_trial = None  # neither tick present -- genuinely unknown, not guessed
        as_of = None
    book_as_of = _epoch_micros_to_iso(quote_payload.get('lastUpdated'))
    trade_date = quote_payload.get('date') or ticker_payload.get('date')
    return {
        'symbol': symbol,
        'name': ticker_payload.get('name') or quote_payload.get('name'),
        'as_of': as_of,
        'trade_date': trade_date,
        'is_trial': is_trial,
        'price': trade_price,
        'trial_price': trial_price,
        # Both fields sourced from /quote alone (see module docstring) --
        # decide() compares previous_close vs reference_price to detect
        # ex-dividend/split events; they must come from one response so a
        # timing mismatch between two separate API calls can never create
        # a false corporate-action alarm (or hide a real one).
        'previous_close': _num(quote_payload.get('previousClose')),
        'reference_price': _num(quote_payload.get('referencePrice')),
        'limit_up': _num(ticker_payload.get('limitUpPrice')),
        'limit_down': _num(ticker_payload.get('limitDownPrice')),
        'book_as_of': book_as_of,
        'bids': _book(quote_payload.get('bids')),
        'asks': _book(quote_payload.get('asks')),
        'halted': None,  # not in ticker's documented example fields; securityStatus
                        # covers "NORMAL" but not an explicit halt boolean we've verified
        'disposition': ticker_payload.get('isDisposition'),
        'source': 'api.fugle.tw marketdata v1.0',
        'source_url': f'{API_BASE}/intraday/quote/{symbol}',
    }


def fetch_quotes(instruments, api_key: str, *, session=None, timeout=15) -> dict[str, dict]:
    """One ticker+quote call pair per symbol (Fugle's REST API has no
    documented batch endpoint like mis.twse's `|`-joined ex_ch). Raises
    immediately on the first HTTP failure (401/403/429/5xx) rather than
    returning partial silently-degraded results, so callers can fall back
    to quotes.py or abort per the caller's own policy."""
    out = {}
    for inst in instruments:
        symbol = inst['symbol']
        ticker = fetch_ticker(symbol, api_key, session=session, timeout=timeout)
        quote = fetch_quote(symbol, api_key, session=session, timeout=timeout)
        out[symbol] = build_quote(symbol, ticker, quote)
    return out
