"""TWSE trading-day calendar: replaces the manual --assume-market-open
flag with a real data source.

Source: TWSE's own OpenAPI holidaySchedule feed (JSON, free, no key):
    https://openapi.twse.com.tw/v1/opendata/holidaySchedule
Verified live (2026-09-10): returns the full ROC-year holiday list (27
entries for ROC 115 / 2026), each row like:
    {"Name":"中華民國開國紀念日","Date":"1150101","Weekday":"四",
     "Description":"依規定放假1日。"}
Date is ROC year+month+day, no separator (e.g. '1150101' = 2026-01-01).

Important nuance found while inspecting real data: this feed lists NON-
trading days (market holidays), not a positive list of trading days. Some
entries carry a Name like "農曆春節前最後交易日" (last trading day before
holiday) or "農曆春節後開始交易日" (first trading day after holiday) which
describe an adjacent TRADING day for context, but the Date on those rows
still IS a trading day -- only rows whose Description says something is
actually closed represent a real holiday. Two distinct real-world phrasings
were found for an actual closure: "放假" (regular holiday, e.g. "依規定放假
1日。") and "補假" (make-up holiday for a holiday that fell on a weekend,
e.g. "...於2月27日（星期五）補假。" -- this phrasing does NOT contain
"放假" at all, so both must be checked). To stay simple and conservative,
is_trading_day() treats a date as closed ONLY if that exact date appears
as a Date field on a row whose Description/Name contains "放假", "補假",
or "市場無交易". Ordinary weekends (Sat/Sun) are also closed regardless of
whether they appear in this feed (this feed only lists the make-up/holiday
days, not routine weekends).

The feed only covers the CURRENT ROC year at the endpoint's default call
(no year parameter is honored server-side per live test: passing
?year=2025 returned identical ROC-115 data) -- so this module caches
whatever year the feed currently serves and callers must NOT assume
future/past years are covered. When a queried date's ROC year does not
match any cached row's year, is_trading_day() cannot determine holiday
status and raises, rather than silently guessing "open".
"""
from __future__ import annotations

from datetime import date, timedelta

HOLIDAY_URL = 'https://openapi.twse.com.tw/v1/opendata/holidaySchedule'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; StockRadar/1.0)'}


def _roc_to_iso(roc_date: str) -> str:
    """'1150101' -> '2026-01-01'. TWSE ROC year = Gregorian year - 1911."""
    year = int(roc_date[:3]) + 1911
    month = roc_date[3:5]
    day = roc_date[5:7]
    return f'{year}-{month}-{day}'


def fetch_holiday_schedule(*, session=None, timeout=20) -> dict:
    """Returns {'closed_dates': {iso_date: name}, 'roc_years': [year_int,...]}
    (JSON-serializable so callers can store the result directly via
    Store.set_meta). closed_dates only includes rows whose
    Description/Name actually signals no trading (放假/市場無交易); rows
    that merely describe an adjacent trading day (最後交易日/開始交易日)
    are excluded on purpose."""
    import requests
    http = session or requests
    resp = http.get(HOLIDAY_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    rows = resp.json()
    closed = {}
    roc_years = set()
    for r in rows:
        raw_date = r.get('Date')
        if not raw_date or len(raw_date) != 7:
            continue
        roc_years.add(int(raw_date[:3]))
        name = r.get('Name', '')
        desc = r.get('Description', '')
        if any(sig in desc or sig in name for sig in ('放假', '補假', '市場無交易')):
            closed[_roc_to_iso(raw_date)] = name
    return {'closed_dates': closed, 'roc_years': sorted(roc_years)}


def is_trading_day(d: date, schedule: dict) -> bool:
    """schedule must be the dict returned by fetch_holiday_schedule() (or
    Store.meta('trading_calendar')). Raises ValueError if d's ROC year
    isn't covered by the cached schedule, rather than silently assuming
    open/closed for a year we have no data for."""
    roc_year = d.year - 1911
    if roc_year not in schedule.get('roc_years', set()):
        raise ValueError(
            f'trading calendar has no data for ROC year {roc_year} '
            f'({d.year}) -- refresh via sync-calendar before trusting this date'
        )
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return d.isoformat() not in schedule.get('closed_dates', {})
