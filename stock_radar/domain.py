"""Pure decisions. Market observations never silently become valuation evidence."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
import math

TW = timezone(timedelta(hours=8))
STATUSES = {'sweet': '甜甜價', 'add': '加碼區', 'buy': '買進區',
            'avoid': '不追', 'pending': '待估值', 'blocked': '暫停', 'stale': '資料不足'}


def number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(str(value).replace(',', '').strip())
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def positive(value):
    n = number(value)
    return n if n is not None and n > 0 else None


def stamp(value):
    try:
        d = datetime.fromisoformat(value)
        return d.astimezone(TW) if d.tzinfo else None
    except (TypeError, ValueError):
        return None


def fresh(value, now, seconds):
    d = stamp(value)
    return bool(d and d.date() == now.date() and 0 <= (now-d).total_seconds() <= seconds)


def floor_tick(value, kind):
    """TWD ordinary shares and domestic equity ETFs only; reject other types."""
    if not positive(value) or kind not in ('stock', 'etf_equity'):
        raise ValueError('價格或跳動單位類型不支援')
    p = Decimal(str(value))
    bands = [(10, '.01'), (50, '.05'), (100, '.1'), (500, '.5'), (1000, '1')]
    step = Decimal('.01' if p < 50 else '.05') if kind == 'etf_equity' else next(
        (Decimal(s) for b, s in bands if p < b), Decimal('5'))
    return float((p / step).to_integral_value(rounding=ROUND_FLOOR) * step)


# Method -> required instrument kind, shared by validate_valuation() and model_prices()
# so a stock method (e.g. forward_pe) can never be applied to an ETF instrument or
# vice versa. Methods not listed here are narrative/manual (e.g. biotech risk-adjusted)
# and are not kind-restricted at this layer; they still require evidence/thesis like any
# other valuation.
METHOD_KIND = {
    'forward_pe': 'stock', 'financial_pb': 'stock', 'normalized_pe': 'stock',
    'etf_nav': 'etf_equity',
}
METHOD_INPUTS = {
    'forward_pe': ('forward_eps', 'fair_pe'),
    'financial_pb': ('book_value_per_share', 'fair_pb'),
    'normalized_pe': ('normalized_eps', 'fair_pe'),
    'etf_nav': ('nav', 'fair_nav_ratio'),
}


def validate_valuation(v, now, kind=None):
    """Validate a valuation payload. If `kind` (instrument kind) is given, reject any
    known stock-only method applied to an ETF instrument or vice versa. On success,
    numeric fields are coerced to float **in place** on `v` so that callers (decide(),
    Store.propose/apply, JSON export) never hold a valuation dict where sweet/add/buy
    passed validation as numeric-looking strings but would crash on a later `p > v['buy']`
    float/str comparison.
    """
    prices = [positive(v.get(k)) for k in ('sweet', 'add', 'buy')]
    if None in prices or prices != sorted(prices):
        raise ValueError('估值必須為正數且甜甜 <= 加碼 <= 買進')
    if not v.get('reason') or not v.get('method') or not v.get('thesis'):
        raise ValueError('缺少估值方法、修改原因或投資論述')
    method = v.get('method')
    expected_kind = METHOD_KIND.get(method)
    if kind is not None and expected_kind is not None and expected_kind != kind:
        raise ValueError(f'估值方法「{method}」不適用於 {kind} 類型標的')
    expires, asof = stamp(v.get('valid_until')), stamp(v.get('as_of'))
    if not expires or not asof or not asof <= now < expires:
        raise ValueError('估值日期或效期不合法')
    evidence = v.get('sources', [])
    if not evidence or any(not isinstance(s, dict) or not s.get('url', '').startswith('https://')
                           or not stamp(s.get('as_of')) or stamp(s['as_of']) > now for s in evidence):
        raise ValueError('估值需要有資料日期的 HTTPS 來源')
    if v.get('evidence_reviewed') is not True:
        raise ValueError('證據尚未複核')
    # Normalize in place: sweet/add/buy are required and already validated positive above.
    for key, val in zip(('sweet', 'add', 'buy'), prices):
        v[key] = val
    # Optional extra thresholds: coerce if present, reject if present-but-unparseable
    # (silently keeping a bad string here is worse than failing validation loudly).
    for key in ('fair', 'avoid', 'sell', 'reduce'):
        if key in v and v[key] is not None:
            n = positive(v[key])
            if n is None:
                raise ValueError(f'{key} 必須為正數')
            v[key] = n


def model_prices(method, inputs, kind):
    """Parameters come from sourced research, not a price-derived automatic PE."""
    if method not in METHOD_KIND:
        raise ValueError('此產業模型需要人工研究，尚不支援自動計算')
    a, b = METHOD_INPUTS[method]
    expected = METHOD_KIND[method]
    if kind != expected or not positive(inputs.get(a)) or not positive(inputs.get(b)):
        raise ValueError('估值模型與標的類型不符或缺必要輸入')
    factors = [positive(inputs.get(k)) for k in ('sweet_factor', 'add_factor', 'buy_factor')]
    if None in factors or factors != sorted(factors) or factors[-1] > 1:
        raise ValueError('安全邊際參數需介於 0 和 1 且依序遞增')
    fair = positive(inputs[a]) * positive(inputs[b])
    return {'fair': round(fair, 4), **{k: floor_tick(fair*f, kind)
            for k, f in zip(('sweet', 'add', 'buy'), factors)}}


def review_reasons(previous, current, events=(), threshold=.10):
    reasons = [str(e) for e in events if e]
    for key in ('forward_eps', 'fair_pe', 'nav', 'fair_nav_ratio', 'expected_distribution'):
        a, b = positive(previous.get(key)), positive(current.get(key))
        if a and b and abs(b/a-1) >= threshold:
            reasons.append(f'{key} 變動 {(b/a-1)*100:+.1f}%')
    return reasons


def decide(instrument, quote, valuation, now=None, *, market_open=False, risk=None,
           history=(), supports=(), max_age=120):
    now = (now or datetime.now(TW)).astimezone(TW)
    result = {'status': 'stale', 'status_label': STATUSES['stale'], 'suggested': None,
              'conservative': None, 'extreme': None, 'valid_until': None,
              'reason': '', 'action': '等待有效資料', 'calculated_at': now.isoformat()}

    def stop(status, reason):
        result.update(status=status, status_label=STATUSES[status], reason=reason, action=reason)
        return result

    if not valuation:
        return stop('pending', '尚無經複核及套用的估值')
    if instrument.get('kind') not in ('stock', 'etf_equity'):
        return stop('blocked', '此證券類型尚無適用模型')
    try:
        validate_valuation(valuation, now, kind=instrument.get('kind'))
    except ValueError as exc:
        return stop('blocked', str(exc))
    if not risk or risk.get('cleared') is not True or not fresh(risk.get('checked_at'), now, 86400):
        return stop('blocked', '基本面、交易限制與流動性尚未完成當日檢查')
    if risk.get('events'):
        return stop('blocked', '重大事件待重新估值：' + '；'.join(risk['events']))
    if not market_open:
        return stop('blocked', '尚未確認今天為交易日')
    if not time(8, 30) <= now.time().replace(tzinfo=None) <= time(13, 30):
        return stop('stale', '非盤前或交易時段，不提供即時掛價')
    if not quote or not fresh(quote.get('as_of'), now, max_age):
        return stop('stale', '行情未取得或已過期')
    if quote.get('trade_date') != now.date().isoformat():
        return stop('stale', '行情交易日不符')
    trial = now.time().replace(tzinfo=None) < time(9)
    if trial and quote.get('is_trial') is not True:
        return stop('stale', '盤前缺少明確試撮資料')
    if not trial and quote.get('is_trial'):
        return stop('stale', '開盤後仍為試撮，等待真實成交')
    p = positive(quote.get('trial_price' if trial else 'price'))
    prev, ref = positive(quote.get('previous_close')), positive(quote.get('reference_price'))
    lo, hi = positive(quote.get('limit_down')), positive(quote.get('limit_up'))
    if not all((p, prev, ref, lo, hi)) or not lo <= p <= hi:
        return stop('stale', '缺昨收、參考價或當日有效價格限制')
    if abs(ref/prev-1) > .0001 and risk.get('corporate_action_reviewed') is not True:
        return stop('blocked', '除權息／分割或參考價異動，需校正定錨')
    if quote.get('halted') or quote.get('disposition'):
        return stop('blocked', '停牌或處置，暫停掛價')
    if abs(p/ref-1) >= .07:
        return stop('blocked', '大幅跳空，需重新檢查事件與價格')
    if p > valuation['buy']:
        return stop('avoid', '高於基本面買進價，不追')
    if not fresh(quote.get('book_as_of'), now, max_age):
        return stop('stale', '五檔資料未取得或已過期')
    bids = [b for b in quote.get('bids', []) if positive(b.get('price')) and positive(b.get('size'))]
    asks = [a for a in quote.get('asks', []) if positive(a.get('price')) and positive(a.get('size'))]
    if not bids or not asks:
        return stop('stale', '買賣委託簿不完整')
    status = next(k for k in ('sweet', 'add', 'buy') if p <= valuation[k])
    cap = min(p, valuation['buy'])
    candidates = [valuation[k] for k in ('sweet', 'add', 'buy') if lo <= valuation[k] <= cap]
    # Anchor preference within 2%; otherwise use supported bids. Threshold is explicit V1 policy.
    near = [v for v in candidates if v >= cap*.98]
    if near:
        target = max(near)
        reason = '接近既有估值定錨，優先等待定錨價'
    else:
        eligible = [b for b in bids if lo <= b['price'] <= cap]
        if not eligible:
            return stop('stale', '上限內沒有有效委買價')
        target = max(eligible, key=lambda b: (b['size'], -b['price']))['price']
        reason = '採上限內有量委買，掛單量不保證成交或支撐'
    past = [positive(h.get('price')) for h in history
            if fresh(h.get('as_of'), now, 1200) and h.get('is_trial') == trial]
    past = [v for v in past if v]
    if len(past) >= 3 and p < past[0]*.99:
        lower = [b['price'] for b in bids if lo <= b['price'] < target]
        if lower:
            target = max(lower)
            reason += '；試撮走弱，降低承接價'
    target = floor_tick(min(target, cap, hi), instrument['kind'])
    if target < lo:
        return stop('blocked', '取整後價格低於當日下限')
    lower_supports = sorted({floor_tick(s, instrument['kind']) for s in supports
                            if positive(s) and lo <= s < target}, reverse=True)
    validity = min(stamp(quote['as_of'])+timedelta(seconds=max_age),
                   stamp(quote['book_as_of'])+timedelta(seconds=max_age),
                   stamp(valuation['valid_until']),
                   now.replace(hour=9 if trial else 13, minute=0 if trial else 30, second=0, microsecond=0))
    result.update(status=status, status_label=STATUSES[status], suggested=target,
                  conservative=lower_supports[0] if lower_supports else None,
                  extreme=lower_supports[-1] if len(lower_supports)>1 else None,
                  valid_until=validity.isoformat(), reason=reason, action=f'可考慮掛 {target:g}，不追價')
    return result
