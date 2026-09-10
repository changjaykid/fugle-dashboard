"""Validate a public radar snapshot before publishing. No network, no secrets."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stock_radar.domain import TW, stamp, validate_valuation


def verify(data, production=False):
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    require(data.get('schema_version') == 1, 'schema_version must be 1')
    require(data.get('mode') in ('live', 'simulation', 'initializing'), 'invalid mode')
    generated = stamp(data.get('generated_at'))
    require(data.get('mode') == 'initializing' or generated is not None, 'generated_at missing timezone')
    if production:
        require(data.get('mode') == 'live', 'production cannot publish simulation or initialization')
        if generated:
            require(0 <= (datetime.now(TW)-generated).total_seconds() <= 3600, 'publication snapshot must be generated in the past hour')
    items = data.get('items')
    if not isinstance(items, list):
        return errors + ['items must be an array']
    symbols = set()
    for i in items:
        symbol = i.get('symbol')
        require(isinstance(symbol, str) and symbol and symbol not in symbols, f'duplicate/invalid symbol: {symbol}')
        symbols.add(symbol)
        require(i.get('kind') in ('stock', 'etf_equity', 'etf_other', 'etn'), f'{symbol}: invalid kind')
        require(bool(i.get('name')), f'{symbol}: missing name')
        s = i.get('signal') or {}
        q = i.get('quote') or {}
        require(s.get('status') in ('sweet', 'add', 'buy', 'avoid', 'pending', 'blocked', 'stale'), f'{symbol}: invalid signal status')
        if s.get('status') not in ('sweet', 'add', 'buy'):
            require(s.get('suggested') is None, f'{symbol}: inactive signal has suggested price')
            continue
        try:
            price=s['suggested']
            require(type(price) in (float, int) and price > 0, f'{symbol}: invalid suggested price')
            calculated, expiry = stamp(s['calculated_at']), stamp(s['valid_until'])
            require(bool(calculated and expiry and calculated < expiry), f'{symbol}: invalid signal lifetime')
            v=i['valuation']
            validate_valuation(v, calculated)
            require(price <= v['buy'], f'{symbol}: price exceeds buy anchor')
            quote_at, book_at = stamp(q['as_of']), stamp(q['book_as_of'])
            require(bool(quote_at and book_at), f'{symbol}: quote/book time missing')
            if calculated and expiry and quote_at and book_at:
                require(0 <= (calculated-quote_at).total_seconds() <= 120, f'{symbol}: stale quote at calculation')
                require(0 <= (calculated-book_at).total_seconds() <= 120, f'{symbol}: stale book at calculation')
                require((expiry-quote_at).total_seconds() <= 120 and (expiry-book_at).total_seconds() <= 120, f'{symbol}: signal outlives input')
                require(q.get('trade_date') == calculated.date().isoformat(), f'{symbol}: wrong trade date')
            require(price <= q['limit_up'] and price >= q['limit_down'], f'{symbol}: outside trading limits')
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            errors.append(f'{symbol}: incomplete actionable record ({type(exc).__name__})')
    coverage=data.get('coverage') or {}
    require(coverage.get('universe') == len(items), 'coverage/universe does not match rows')
    require(coverage.get('stocks') == sum(i.get('kind') == 'stock' for i in items), 'coverage/stocks mismatch')
    require(coverage.get('etfs') == sum(i.get('kind','').startswith('etf') for i in items), 'coverage/etfs mismatch')
    require(coverage.get('valued') == sum(bool(i.get('valuation')) for i in items), 'coverage/valued mismatch')
    return errors


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('path', nargs='?', default=str(ROOT/'docs/radar.json'))
    parser.add_argument('--production', action='store_true')
    args=parser.parse_args()
    errors=verify(json.loads(Path(args.path).read_text()), args.production)
    print(json.dumps({'valid':not errors,'errors':errors}, ensure_ascii=False, indent=2))
    sys.exit(bool(errors))
