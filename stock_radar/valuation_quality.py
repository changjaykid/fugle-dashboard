"""Model applicability, separate from price freshness and whether a stock is expensive."""
from .domain import positive

def assess(inst, valuation, technical=None, review=None):
    if not valuation or inst.get('kind') != 'stock':
        return None
    if review and review.get('status') == 'needs_research':
        return dict(review)
    t = technical or {}
    low, high = positive(t.get('low_60d')), positive(t.get('high_60d'))
    buy, fair = positive(valuation.get('buy')), positive(valuation.get('fair'))
    if low and buy and buy < low * .85:
        reason = '歷史估值與近期交易區間落差過大，需確認獲利與產業前景是否改變；舊公式價格不作為買進目標，也不能僅據此判定股價過高。'
    elif high and fair and fair > high * 1.3:
        reason = '估值明顯高於近期交易區間，需確認獲利是否可持續及資料是否可比；完成複核前不提供買進價。'
    else:
        return None
    return {'status': 'needs_research', 'reason': reason, 'sources': [],
            'checks': {'buy': buy, 'fair': fair, 'low_60d': low, 'high_60d': high}}

def for_store(s, inst):
    from .buy_plan import technical_for
    return assess(inst, s.active(inst['symbol']), technical_for(s, inst['symbol']),
                  s.fact(inst['symbol'], 'valuation_review'))

def researched_anchor(v):
    required = ('financials', 'outlook', 'valuation_basis', 'price_structure', 'risks')
    return bool(v and not v.get('review_mode', '').startswith('rule-engine') and
                all((v.get('analysis_review') or {}).get(k) is True for k in required))
