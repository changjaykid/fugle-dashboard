"""Daily risk/liquidity gate feeding the `risk` fact that domain.decide()
requires before it will ever compute an actionable price (per
STOCK_RADAR_SPEC.md: 資料不足、停牌、處置、全額交割、流動性不足、財務異常
或投資論述失效 -> 待研究／暫停). Without this fact populated, decide()
correctly stays 'blocked' rather than fabricating a signal -- this module
exists to fill that gap with real TWSE data, not to relax the gate.

Sources (all TWSE OpenAPI, TSE only -- OTC halted/disposition lists are a
documented open gap, same limitation as financials.py):
  https://openapi.twse.com.tw/v1/exchangeReport/TWTAWU   (暫停交易證券)
  https://openapi.twse.com.tw/v1/announcement/punish     (處置股票, with 處置期間)

V1 does NOT attempt to detect 全額交割 (full-cash-delivery) or "投資論述
失效" (thesis invalidated) automatically -- no free machine-readable TWSE
feed for the former was found, and the latter is inherently a human
research judgment. Both remain open gaps, explicitly not guessed at.
"""
from __future__ import annotations

from datetime import datetime, date, timezone, timedelta
from typing import Iterable

TW = timezone(timedelta(hours=8))
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; StockRadar/1.0)'}
TWTAWU_URL = 'https://openapi.twse.com.tw/v1/exchangeReport/TWTAWU'
PUNISH_URL = 'https://openapi.twse.com.tw/v1/announcement/punish'


def _roc_date(s: str) -> date | None:
    """Parse an ROC-calendar date like '1150910' (7 digits, YYYMMDD) or
    '115/09/07' into a proleptic Gregorian date.today"""
    if not s:
        return None
    s = s.strip()
    try:
        if '/' in s:
            y, m, d = s.split('/')
        elif len(s) == 7:
            y, m, d = s[:3], s[3:5], s[5:7]
        else:
            return None
        return date(int(y) + 1911, int(m), int(d))
    except (ValueError, IndexError):
        return None


def fetch_halted_symbols(*, session=None, timeout=15, today=None) -> dict[str, dict]:
    """Returns {symbol: {'halt_date':..., 'resume_date':...}} for symbols
    whose TradingHaltDate <= today <= TradingResumptionDate (or resumption
    date missing/unparseable -- treat as still halted rather than assume
    resumed)."""
    import requests
    http = session or requests
    today = today or datetime.now(TW).date()
    resp = http.get(TWTAWU_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    out = {}
    for row in resp.json():
        code = row.get('Code')
        if not code:
            continue
        halt = _roc_date(row.get('TradingHaltDate'))
        resume = _roc_date(row.get('TradingResumptionDate'))
        if halt and halt <= today and (resume is None or today <= resume):
            out[code] = {'halt_date': halt.isoformat(),
                        'resume_date': resume.isoformat() if resume else None}
    return out


def fetch_disposition_symbols(*, session=None, timeout=15, today=None) -> dict[str, dict]:
    """Returns {symbol: {'period':..., 'reason':..., 'measures':...}} for
    symbols currently inside their DispositionPeriod (parsed 'YYY/MM/DD~
    YYY/MM/DD' ROC range, inclusive)."""
    import requests
    http = session or requests
    today = today or datetime.now(TW).date()
    resp = http.get(PUNISH_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    out = {}
    for row in resp.json():
        code = row.get('Code')
        period = row.get('DispositionPeriod', '')
        if not code or '～' not in period:
            continue
        start_s, end_s = period.split('～', 1)
        start, end = _roc_date(start_s), _roc_date(end_s)
        if start and end and start <= today <= end:
            out[code] = {'period': period, 'reason': row.get('ReasonsOfDisposition'),
                        'measures': row.get('DispositionMeasures')}
    return out


def build_risk_facts(instruments: Iterable[dict], *, session=None, now=None) -> dict[str, dict]:
    """Returns {symbol: risk_fact} for every TSE instrument passed in.

    `cleared` means "today's automated check actually ran for this symbol",
    NOT "no problems found" -- domain.decide() treats cleared=True + fresh
    checked_at as the gate to even look at `events`; if `events` is
    non-empty decide() blocks separately with the event detail. So a
    halted/disposed symbol still gets cleared=True (the check ran) with its
    problem listed in `events`, which is what actually blocks it downstream.

    OTC instruments get cleared=False (the check genuinely did NOT run --
    no OTC halted/disposition data source exists in V1) rather than
    cleared=True with a fabricated 'no events' result. This correctly keeps
    OTC symbols blocked via the '尚未完成當日檢查' path, distinct from a TSE
    symbol that was checked and found clean.

    A network failure on either source aborts the whole batch (raises)
    rather than clearing risk for symbols we could not actually check --
    caller must surface this as a health entry, not partial success."""
    now = (now or datetime.now(TW)).astimezone(TW)
    today = now.date()
    halted = fetch_halted_symbols(session=session, today=today)
    disposed = fetch_disposition_symbols(session=session, today=today)
    checked_at = now.isoformat()
    out = {}
    for inst in instruments:
        symbol = inst['symbol']
        if inst.get('market') != 'TSE':
            out[symbol] = {
                'cleared': False, 'checked_at': checked_at,
                'events': ['OTC 尚無停牌/處置資料源，V1 不予放行'],
            }
            continue
        events = []
        if symbol in halted:
            events.append(f"停牌中（自 {halted[symbol]['halt_date']}）")
        if symbol in disposed:
            d = disposed[symbol]
            events.append(f"處置中（{d['period']}，{d['reason']}）")
        out[symbol] = {
            'cleared': True,
            'checked_at': checked_at,
            'events': events,
        }
    return out
