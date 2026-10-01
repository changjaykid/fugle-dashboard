"""Run Strategy v1 selection over the whole market for one day. No LLM, no network.

Assembles what selection.features needs out of the cached market days -- the stock's own bars, an
equal-weight market index, its industry's index and breadth, its institutional flow and whatever
fundamentals are on file -- then applies the hard filter, detects the setup and scores it.

ETFs are not here. The document gives them their own strategy and the reasons are not stylistic: an
equity ETF is a basket of the same stocks, so holding one beside its constituents is the same bet
counted twice by a risk engine that thinks they are independent; its "valuation" is a price position
rather than a valuation; and it has neither a breakout from a base nor relative strength against the
market it largely is.
"""
import statistics

from . import market, selection

ETF_KINDS = ('etf_equity', 'etf_other')


def _index(prices, symbols, k, back):
    """Equal-weight index levels for `symbols` over the last `back` days ending at k, oldest first."""
    out = []
    for j in range(max(0, k - back + 1), k + 1):
        table = prices[j][1]
        values = [table[s][3] for s in symbols if s in table and table[s][3]]
        out.append(statistics.mean(values) if values else None)
    return [x for x in out if x is not None]


def universe(store, prices, k):
    """Listed ordinary shares only: the document's stage one, minus ETFs."""
    liquid = market.liquidity(prices[max(0, k - 19):k + 1])
    out = {}
    for inst in store.instruments():
        if inst['kind'] != 'stock':
            continue
        if liquid.get(inst['symbol'], 0) <= 0:
            continue
        out[inst['symbol']] = inst
    return out


def breadth_by_industry(prices, insts, k):
    """Per industry: what share of its stocks rose over 20 days, and what share sit near a 60-day high."""
    groups = {}
    for sym, inst in insts.items():
        groups.setdefault(inst.get('industry_code') or inst.get('industry') or '其他', []).append(sym)
    out = {}
    for name, symbols in groups.items():
        up = near = seen = 0
        for sym in symbols:
            bars = [prices[j][1].get(sym) for j in range(max(0, k - 59), k + 1)]
            bars = [b for b in bars if b and b[3]]
            if len(bars) < 21:
                continue
            seen += 1
            if bars[-1][3] > bars[-21][3]:
                up += 1
            high = max(b[1] for b in bars)
            if high and (high - bars[-1][3]) / high <= 0.05:
                near += 1
        if seen >= 4:
            out[name] = {'advancing': up / seen, 'near_high': near / seen, 'members': seen,
                         'symbols': symbols}
    return out


def run(store, prices, flows, k, settings=None, fundamentals=True):
    """Every stock the scan can judge on day k: hard filter, setup, score. Returns rows and a tally."""
    insts = universe(store, prices, k)
    market_index = _index(prices, list(insts), k, 61)
    breadth = breadth_by_industry(prices, insts, k)
    sector_index = {name: _index(prices, info['symbols'], k, 22) for name, info in breadth.items()}
    flow_by_day = {d: t for d, t in flows}
    days = [d for d, _ in prices]

    rows, tally = [], {'universe': len(insts), 'filtered': 0, 'no_setup': 0, 'unscored': 0,
                       'breakout': 0, 'pullback': 0}
    for sym, inst in insts.items():
        bars = [prices[j][1].get(sym) for j in range(max(0, k - 119), k + 1)]
        if not bars or not bars[-1]:
            continue
        own_flows = [flow_by_day.get(days[j], {}).get(sym) for j in range(max(0, k - 9), k + 1)]
        own_flows = [f for f in own_flows if f]
        industry = inst.get('industry_code') or inst.get('industry') or '其他'
        fin = None
        if fundamentals:
            raw = store.fact(sym, 'financials') or {}
            inc, rev = raw.get('income') or {}, raw.get('revenue') or {}
            sales, op = inc.get('revenue'), inc.get('operating_income')
            fin = {'revenue_yoy': (rev.get('yoy_pct') / 100) if isinstance(rev.get('yoy_pct'), (int, float)) else None,
                   'eps': inc.get('eps_basic'),
                   'operating_margin': (op / sales) if isinstance(op, (int, float)) and sales else None}
            if not any(v is not None for v in fin.values()):
                fin = None
        f = selection.features(bars, market_index, sector_index.get(industry), own_flows, fin)
        if not f:
            tally['filtered'] += 1
            continue
        why = selection.hard_filter(f, store.fact(sym, 'risk'), settings)
        if why:
            tally['filtered'] += 1
            continue
        kind, note = selection.setup(f, settings)
        if not kind:
            tally['no_setup'] += 1
            continue
        card = selection.score(f, kind, breadth.get(industry), settings)
        if not card:
            tally['unscored'] += 1
            continue
        tally[kind] += 1
        rows.append({'symbol': sym, 'name': inst.get('name') or sym, 'industry': industry,
                     'setup': kind, 'why': note, 'features': f, **card})
    rows.sort(key=lambda r: -r['total'])
    return rows, tally
