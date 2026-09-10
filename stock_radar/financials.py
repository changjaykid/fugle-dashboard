"""Financial facts for valuation research: quarterly income statement (incl.
EPS), monthly revenue, and trailing P/E, dividend yield, P/B for TSE-listed
stocks.

All sources are TWSE official OpenAPI (free, no key):
  - t187ap06_L_ci  : listed-company quarterly comprehensive income statement,
                     general industry (公發公司綜合損益表-一般業). Includes
                     基本每股盈餘（元） (basic EPS). Other industry variants
                     exist (_basi financial, _ins insurance, _fh holding,
                     _bd securities/futures) -- general-industry only is
                     wired up for V1; financial/insurance/holding companies
                     need their own statement shape and are out of scope
                     until requested.
  - t187ap05_L     : monthly revenue (營業收入-當月營收 etc.)
  - BWIBBU_ALL     : daily P/E, dividend yield, P/B by symbol (TSE only)

OTC-listed companies are NOT covered by these TWSE t187ap0x endpoints (they
are TSE-listed-company disclosure feeds); OTC financials would need TPEx's
equivalent MOPS-fed endpoints, which have not been located/verified yet.
Callers must treat missing OTC financials as "not yet available" (None),
never assume TSE data applies cross-market.
"""
from __future__ import annotations

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; StockRadar/1.0)'}
BASE = 'https://openapi.twse.com.tw/v1/opendata'
INCOME_URL = f'{BASE}/t187ap06_L_ci'
REVENUE_URL = f'{BASE}/t187ap05_L'
PE_YIELD_PB_URL = 'https://www.twse.com.tw/exchangeReport/BWIBBU_ALL'


def _num(s):
    try:
        if s in (None, '', '-'):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def fetch_quarterly_income_general(*, session=None, timeout=30) -> dict[str, dict]:
    """Returns {symbol: latest_quarter_row} for TSE general-industry listed
    companies. Only the single most recent quarter this endpoint currently
    serves is returned per symbol (the live endpoint has been observed to
    carry exactly one quarter's worth of data across the whole file, not a
    time series) -- callers needing history must persist snapshots over
    time via Store.observe-style accumulation, not expect one call to
    return multi-quarter history."""
    import requests
    http = session or requests
    resp = http.get(f'{BASE}/t187ap06_L_ci', headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    rows = resp.json()
    out = {}
    for r in rows:
        symbol = r.get('公司代號')
        if not symbol:
            continue
        out[symbol] = {
            'symbol': symbol, 'name': r.get('公司名稱'),
            # year_roc + quarter = the actual REPORT PERIOD this financial
            # statement covers (e.g. year_roc='115', quarter='2' = 115年Q2).
            # This is what valuation research should cite as "as of".
            'year_roc': r.get('年度'), 'quarter': r.get('季別'),
            'revenue': _num(r.get('營業收入')),
            'gross_profit': _num(r.get('營業毛利（毛損）淨額')),
            'operating_income': _num(r.get('營業利益（損失）')),
            'net_income': _num(r.get('本期淨利（淨損）')),
            'eps_basic': _num(r.get('基本每股盈餘（元）')),
            # report_date = 出表日期, i.e. when TWSE PUBLISHED/refreshed this
            # file -- this is NOT the financial reporting period and must
            # never be used as a stand-in for year_roc/quarter (a filing
            # published later does not mean the period it covers changed).
            'report_date': r.get('出表日期'),
            'source_url': INCOME_URL,
        }
    return out


def fetch_monthly_revenue(*, session=None, timeout=30) -> dict[str, dict]:
    """Returns {symbol: latest_month_row} — monthly revenue YoY/MoM for
    listed companies (TSE)."""
    import requests
    http = session or requests
    resp = http.get(f'{BASE}/t187ap05_L', headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    rows = resp.json()
    out = {}
    for r in rows:
        symbol = r.get('公司代號')
        if not symbol:
            continue
        out[symbol] = {
            'symbol': symbol, 'name': r.get('公司名稱'),
            # period_roc_ym = the actual REPORT PERIOD (資料年月, e.g.
            # '11507' = ROC 115 year, July) this monthly revenue figure
            # covers -- this endpoint carries no separate publication-date
            # field, so period_roc_ym is the only "as of" available and
            # must be cited as such (do not fabricate a separate report
            # date for this fetcher).
            'period_roc_ym': r.get('資料年月'),
            'revenue_this_month': _num(r.get('營業收入-當月營收')),
            'revenue_last_month': _num(r.get('營業收入-上月營收')),
            'revenue_yoy_month': _num(r.get('營業收入-去年當月營收')),
            'mom_pct': _num(r.get('營業收入-上月比較增減(%)')),
            'yoy_pct': _num(r.get('營業收入-去年同月增減(%)')),
            'cumulative_revenue': _num(r.get('累計營業收入-當月累計營收')),
            'cumulative_revenue_last_year': _num(r.get('累計營業收入-去年累計營收')),
            'note': r.get('備註') or None,
            'source_url': REVENUE_URL,
        }
    return out


def fetch_pe_yield_pb(*, session=None, timeout=20) -> dict[str, dict]:
    """Returns {symbol: {'pe':..., 'yield_pct':..., 'pb':...}} for TSE-listed
    stocks, sourced from TWSE's own daily BWIBBU_ALL report (本益比、殖利率
    及股價淨值比)."""
    import requests
    http = session or requests
    resp = http.get(PE_YIELD_PB_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    out = {}
    as_of = data.get('date')  # the actual trading day these ratios were computed for (report period, not a publish timestamp)
    for row in data.get('data', []):
        symbol = row[0]
        out[symbol] = {
            'symbol': symbol, 'name': row[1],
            'pe': _num(row[2]), 'yield_pct': _num(row[3]), 'pb': _num(row[4]),
            'as_of_roc_date': as_of,
            'source_url': PE_YIELD_PB_URL,
        }
    return out
