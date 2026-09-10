"""Build docs/radar.json per RADAR_DATA_CONTRACT.md. Pure function of
(instruments, quotes, valuations, signals, research, health) -> dict.
Writing to disk uses store.atomic_json so partial writes never corrupt the
file consumed by the frontend."""
from __future__ import annotations

from datetime import datetime
from .domain import TW


def build_radar_json(*, instruments, quotes, decisions, valuations, research,
                     health, mode='live', now=None) -> dict:
    """
    instruments: list of universe dicts (symbol/name/kind/market/...)
    quotes: {symbol: quote_dict or None}
    decisions: {symbol: decide()-shaped dict or None}
    valuations: {symbol: active valuation dict (with 'id') or None}
    research: {symbol: {'thesis':..., 'why_now':..., 'chips':..., 'catalysts':[...], 'risks':[...], 'sources':[...]}}
    health: list of {'name','status','detail','as_of'}
    """
    now = (now or datetime.now(TW)).astimezone(TW)
    items = []
    n_quotes = 0
    n_valued = 0
    n_stock = 0
    n_etf = 0
    for inst in instruments:
        symbol = inst['symbol']
        kind = inst['kind']
        if kind == 'stock':
            n_stock += 1
        elif kind in ('etf_equity', 'etf_other'):
            n_etf += 1
        # kind == 'etn' is intentionally NOT counted toward n_etf: an ETN
        # (Exchange Traded Note) is a debt-like note issued against the
        # bank's own credit, not a fund/collective investment vehicle, per
        # Kid's explicit 2026-09-10 correction. It still appears in items
        # (kept in coverage.universe) so it's searchable, just not counted
        # as an ETF.
        q = quotes.get(symbol)
        if q and q.get('as_of'):
            n_quotes += 1
        v = valuations.get(symbol)
        if v:
            n_valued += 1
        d = decisions.get(symbol)
        r = research.get(symbol) or {}

        quote_out = {
            'previous_close': None, 'reference_price': None, 'price': None,
            'trial_price': None, 'as_of': None, 'trade_date': None,
            'is_trial': False, 'source': '尚未取得', 'source_url': None,
        }
        if q:
            # Two quote shapes reach this function:
            #  - stock_radar.quotes (mis.twse.com.tw fallback): a single
            #    'price' key, no 'trial_price' key at all, with 'is_trial'
            #    always None (see that module's own docstring: it cannot
            #    reliably tell trial vs regular price apart) -- the OLD
            #    collapsed-field contract, kept for backward compatibility
            #    with that fallback source.
            #  - stock_radar.fugle (Codex's v2 FugleClient adapter):
            #    'price' and 'trial_price' are ALWAYS both present as
            #    independent dict keys (build via normalize_quote()),
            #    'trial_price' only non-None during the real 08:30-09:00
            #    pre-market trial session per the venue's own timestamp
            #    (see fugle.py's `pretrial` check) -- collapsing these back
            #    into one field via is_trial here would silently discard a
            #    real trial-match tick. Detect which shape we have via the
            #    presence of the 'trial_price' KEY itself (mis.twse quotes
            #    never set it), not via is_trial, so a fugle quote with
            #    is_trial=False but a populated trial_price still exports
            #    correctly instead of being dropped.
            if 'trial_price' in q:
                price_out = q.get('price')
                trial_price_out = q.get('trial_price')
            else:
                price_out = q.get('price') if not q.get('is_trial') else None
                trial_price_out = q.get('price') if q.get('is_trial') else None
            quote_out.update({
                'previous_close': q.get('previous_close'),
                'reference_price': q.get('reference_price'),
                'price': price_out,
                'trial_price': trial_price_out,
                'as_of': q.get('as_of'), 'trade_date': q.get('trade_date'),
                'is_trial': bool(q.get('is_trial')),
                'source': q.get('source') or '尚未取得',
                'source_url': q.get('source_url'),
            })

        signal_out = {
            'status': 'pending', 'status_label': '待估值', 'suggested': None,
            'conservative': None, 'extreme': None, 'reason': '尚無經複核估值',
            'action': '等待研究', 'calculated_at': None, 'valid_until': None,
        }
        if d:
            signal_out.update({k: d.get(k) for k in signal_out.keys()})

        valuation_out = None
        if v:
            valuation_out = {
                'id': v.get('id'), 'sweet': v.get('sweet'), 'add': v.get('add'),
                'buy': v.get('buy'), 'fair': v.get('fair'), 'avoid': v.get('avoid'),
                'sell': v.get('sell'), 'reduce': v.get('reduce'),
                'method': v.get('method'), 'inputs': v.get('inputs'),
                'reason': v.get('reason'), 'thesis': v.get('thesis'),
                'as_of': v.get('as_of'), 'valid_until': v.get('valid_until'),
                'sources': v.get('sources'), 'evidence_reviewed': v.get('evidence_reviewed'),
            }

        items.append({
            'symbol': symbol, 'name': inst.get('name'), 'kind': kind,
            'market': inst.get('market'), 'industry': inst.get('industry_code'),
            'description': inst.get('description'), 'watched': inst.get('watched', False),
            'quote': quote_out, 'valuation': valuation_out, 'signal': signal_out,
            'research': {
                'thesis': r.get('thesis'), 'why_now': r.get('why_now'),
                'chips': r.get('chips'), 'catalysts': r.get('catalysts', []),
                'risks': r.get('risks', []), 'sources': r.get('sources', []),
            },
            'valuation_history': r.get('valuation_history', []),
        })

    return {
        'schema_version': 1,
        'generated_at': now.isoformat(),
        'mode': mode,
        'market_date': now.date().isoformat(),
        'coverage': {
            'universe': len(instruments), 'stocks': n_stock, 'etfs': n_etf,
            'quotes': n_quotes, 'valued': n_valued,
        },
        'health': health,
        'items': items,
    }
