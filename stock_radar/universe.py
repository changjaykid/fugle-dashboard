"""Universe (master list): all-market TSE/OTC stocks + equity ETFs from TWSE ISIN pages.

Data source: https://isin.twse.com.tw/isin/C_public.jsp?strMode=N (official
TWSE ISIN registry, free, no key required).
  strMode=2 -> TSE listed (上市): stocks, ETF, ETN, warrants, REITs,
               preferred shares, TDR
  strMode=4 -> OTC listed (上櫃/TPEx): same categories

Classification uses TWO independent signals so a single mistake in either
does not misclassify a warrant as tradeable equity:
  1. The section header printed in each page (e.g. "股票" / "ETF" /
     "上市認購(售)權證") -- this is the page's own authoritative grouping,
     not something we infer.
  2. The ISIN CFI code in column 6 (e.g. ESVUFR for ordinary shares,
     CEOGEU for ETF, RWSCCA for warrants). CFI codes starting with 'R'
     are rights/warrants per ISO 10962 and are excluded even if a row
     somehow appeared outside a warrant section.

This explicitly does NOT classify instruments by code length or code
prefix ranges (e.g. "4-digit numeric = stock", "00xxxx = ETF") because
that heuristic breaks for TDRs, new warrant series, and depositary
receipts that share the numeric-code space. Only exchange-provided
section metadata + CFI are used.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import Iterable

try:
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover - hard dependency, fail loud
    raise ImportError('stock_radar.universe requires beautifulsoup4 (pip install beautifulsoup4)') from exc

ISIN_URL = 'https://isin.twse.com.tw/isin/C_public.jsp'

# Section header (as printed verbatim on the page) -> instrument kind.
# Only 'stock' and 'etf_equity' are currently eligible for radar valuation
# per STOCK_RADAR_SPEC.md; everything else is kept in the universe table
# for completeness/search but marked with its own kind so decide() will
# correctly refuse to produce a live signal for it (kind not in
# ('stock', 'etf_equity')).
SECTION_KIND = {
    '股票': 'stock',
    '創新板': 'stock',
    'ETF': 'etf_equity',
    'ETN': 'etf_other',
    '特別股': 'preferred',
    '臺灣存託憑證(TDR)': 'tdr',
    '受益證券-不動產投資信託': 'reit',
    '受益證券-資產基礎證券': 'abs',
}

WARRANT_SECTION_MARKERS = ('認購', '認售', '權證')


def _is_warrant_cfi(cfi: str) -> bool:
    """CFI codes for rights/warrants start with 'R' per the ISIN registry's own
    encoding (e.g. RWSCCA). Used as a second, independent check beyond the
    section header so a stray warrant row can never slip into the equity/ETF
    universe."""
    return bool(cfi) and cfi.startswith('R')


@dataclass(frozen=True)
class RawFetch:
    market_label: str
    html_bytes: bytes


def fetch_isin_page(market: str, *, session=None, timeout=20) -> RawFetch:
    """market: 'TSE' -> strMode=2, 'OTC' -> strMode=4.

    Uses `requests` (verified reachable for isin.twse.com.tw with default
    certificate verification -- no SSL bypass). Caller decides retry policy;
    this raises on failure rather than silently returning stale/empty data.
    """
    import requests
    mode = {'TSE': '2', 'OTC': '4'}[market]
    http = session or requests
    resp = http.get(ISIN_URL, params={'strMode': mode},
                     headers={'User-Agent': 'Mozilla/5.0 (compatible; StockRadar/1.0)'},
                     timeout=timeout)
    resp.raise_for_status()
    return RawFetch(market_label=market, html_bytes=resp.content)


def parse_isin_html(html_bytes: bytes, market_label: str) -> list[dict]:
    """Parse one market's ISIN page into instrument dicts. Pure function,
    no network -- fully unit-testable against fixture HTML."""
    soup = BeautifulSoup(html_bytes, 'html.parser', from_encoding='big5')
    rows = soup.find_all('tr')
    section = None
    out = []
    for tr in rows:
        tds = tr.find_all('td')
        if len(tds) == 1 or (tds and tds[0].get('colspan')):
            section = tds[0].get_text(strip=True)
            continue
        if len(tds) < 6:
            continue
        code_name = tds[0].get_text(strip=True)
        isin = tds[1].get_text(strip=True)
        listed_date = tds[2].get_text(strip=True)
        market = tds[3].get_text(strip=True)
        industry = tds[4].get_text(strip=True)
        cfi = tds[5].get_text(strip=True)
        if not code_name or '\u3000' not in code_name:
            continue
        code, name = code_name.split('\u3000', 1)
        code, name = code.strip(), name.strip()

        if section and any(marker in section for marker in WARRANT_SECTION_MARKERS):
            continue
        if _is_warrant_cfi(cfi):
            continue
        kind = SECTION_KIND.get(section)
        if kind is None:
            continue

        out.append({
            'symbol': code, 'name': name, 'isin': isin,
            'listed_date': listed_date, 'market': market_label,
            'section': section, 'industry_code': industry or None,
            'cfi': cfi, 'kind': kind,
        })
    return out


def build_universe(*, session=None, sleep_between=1.0) -> list[dict]:
    """Fetch + parse both TSE and OTC ISIN pages into one combined universe.
    Network errors propagate (caller must record a `health` entry and must
    NOT fall back to stale data silently, per RADAR_DATA_CONTRACT.md)."""
    items = []
    for i, market in enumerate(('TSE', 'OTC')):
        if i > 0 and sleep_between:
            time.sleep(sleep_between)
        raw = fetch_isin_page(market, session=session)
        items.extend(parse_isin_html(raw.html_bytes, market))
    return items


def equity_universe(items: Iterable[dict]) -> list[dict]:
    """Filter to only the kinds the radar can currently value/signal on:
    ordinary stocks and equity-style ETFs. Everything else (ETN, preferred,
    TDR, REIT, ABS) stays in the full universe table for search but is
    excluded from the scan target list."""
    return [i for i in items if i['kind'] in ('stock', 'etf_equity')]
