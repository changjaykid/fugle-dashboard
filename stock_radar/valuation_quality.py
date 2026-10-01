"""Model applicability, separate from price freshness and whether a stock is expensive."""
from datetime import timedelta

from .domain import positive, stamp

# How long a lapsed research price keeps outranking the screen. The original rule was "expired research
# must be renewed by research, never replaced by a screen", which assumed someone renews it. Nothing
# does: research_llm writes narrative facts only and never republishes an anchor, and it cannot even wake
# a symbol whose plan has gone stale, so an unrenewed anchor removed its symbol from the system for good
# (2454 would have gone on 2026-10-05). The grace window keeps the original intent -- research gets first
# refusal, and the lapse is announced so a person can renew it -- without the permanent trap. Nothing is
# recommended during the window either way: an expired valuation already fails validate_valuation.
ANCHOR_GRACE = timedelta(days=7)

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


def anchor_protected(v, now):
    """Whether this researched price may still not be replaced by the rule engine.

    True while it is valid and through the grace window after it expires. An anchor with no readable
    `valid_until` stays protected, because an unreadable date is not evidence that it lapsed."""
    if not researched_anchor(v):
        return False
    until = stamp(v.get('valid_until'))
    return not until or now < until + ANCHOR_GRACE


def anchor_state(v, now):
    """'live', 'grace' or 'lapsed' for a researched anchor; None when there is no anchor."""
    if not researched_anchor(v):
        return None
    until = stamp(v.get('valid_until'))
    if not until or now < until:
        return 'live'
    return 'grace' if now < until + ANCHOR_GRACE else 'lapsed'
