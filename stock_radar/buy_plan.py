"""A dated closing-price plan, distinct from an executable intraday signal."""
from datetime import datetime,timedelta
from .domain import TW,positive,stamp,validate_valuation,floor_tick
from .calendar import is_trading_day
# Sanity bounds against the last 60 trading days: buy cap below 85% of the low, or fair value 30% above the high.
GAP_LOW,GAP_HIGH=.85,1.3

def last_completed_day(now,schedule):
    d=now.date() if now.hour>=14 else now.date()-timedelta(days=1)
    for _ in range(16):
        if is_trading_day(d,schedule):return d
        d-=timedelta(days=1)
    raise ValueError('交易日曆無法確認最近收盤日')

def make_plan(instrument,valuation,close,technical,risk,schedule,now):
    p={'band':'stale','status':'missing','label':'等待資料','entry':None,'buy_max':None,'close_date':(close or {}).get('trade_date'),'valid_until':None,'reason':'等待完整且有效的分析資料','is_live_order':False}
    from .valuation_quality import assess
    quality=assess(instrument,valuation,technical)
    if quality:
        p.update(band='blocked',status='valuation_gap',label='價位需重新評估',reason=quality['reason'])
        return p
    try:
        validate_valuation(valuation or {},now,kind=instrument.get('kind'))
        if not close or close.get('trade_date')!=str(last_completed_day(now,schedule)):return p
        n=positive(close.get('close'))
        if not n:return p
        p.update(band=next((k for k in ('sweet','add','buy') if n<=valuation[k]),'avoid'),buy_max=valuation['buy'])
        # Closing bands describe valuation only. Trade readiness additionally needs risk and structure.
        d=last_completed_day(now,schedule)+timedelta(days=1)
        while not is_trading_day(d,schedule):d+=timedelta(days=1)
        expiry=min(datetime.combine(d,datetime.min.time(),TW).replace(hour=14),stamp(valuation['valid_until']))
        p['valid_until']=expiry.isoformat()
        checked=stamp((risk or {}).get('checked_at'))
        if not checked or not timedelta(0)<=now-checked<=timedelta(hours=30) or risk.get('cleared') is not True or risk.get('events'):
            p['reason']='交易限制或重大事件尚待確認';return p
        if not technical or technical.get('as_of')!=close['trade_date'] or technical.get('flow_as_of')!=close['trade_date']:
            p['reason']='價格結構或法人資料尚待更新';return p
        p.update(levels(instrument,valuation,n,technical,p['band']))
        return p
    except (ValueError,TypeError,KeyError):return p

def levels(instrument,valuation,n,technical,band):
    """Where this price sits against its bands and its structure, and the support to buy at.

    Extracted from make_plan so the backtest reads exactly the levels production does: it supplies the
    same fields from market_scan.structure, and a backtest computing its own supports would stop
    predicting the thing it is meant to predict. Everything make_plan does around this -- validity,
    expiry, trading restrictions, data freshness -- stays there, because a backtest has none of it."""
    # A valuation far outside the traded range is a model problem, not a buy/no-chase signal.
    low,high,fair=positive(technical.get('low_60d')),positive(technical.get('high_60d')),positive(valuation.get('fair'))
    if low and valuation['buy']<low*GAP_LOW:
        etf=valuation.get('method')=='etf_band'
        return {'band':'avoid','status':'overvalued','label':'不追：價格在一年高檔' if etf else '不追：評價過高','reason':(f"價格已遠高於過去一年的中位數，短期難回到買進區（近 60 日最低 {low:g} 元），不追" if etf else f"股價已遠高於過去一年的常態評價，照一年評價水準的買點短期難以到達（近 60 日最低 {low:g} 元），不追")}
    if high and fair and fair>high*GAP_HIGH:
        return {'band':'blocked','status':'valuation_gap','label':'估值待重新研究','reason':f"合理價 {fair:g} 元高於近 60 日最高 {high:g} 元逾三成，獲利外推可能過度樂觀，暫不提供買點"}
    support=positive(technical.get('support'))
    if not support or n<support:
        return {'status':'broken','label':'跌破支撐，先等','reason':'已跌破原先支撐，暫停買進計畫並重新檢查'}
    out={'support':support}
    supports=[positive(x) for x in technical.get('supports',[])]
    entries=[x for x in supports if x and x<=min(n,valuation['buy'])]
    if band=='avoid':
        return {**out,'status':'no_chase','label':'不追，等回落','reason':f"收盤高於買進上限 {valuation['buy']:g} 元，等待回落再確認"}
    if not entries:
        return {**out,'status':'waiting','label':'等承接位置','reason':'估值符合，但上限內尚無可確認的支撐'}
    wait=technical.get('requires_stabilization') is True
    return {**out,'entry':floor_tick(max(entries),instrument['kind']),
            'status':'wait_stabilize' if wait else 'conditional',
            'label':'等止穩再考慮' if wait else '可列入分批計畫',
            'reason':'低於月線且近五日外資與投信合計賣超，先等收盤站回月線或近五日賣壓改善' if wait else '估值與承接位置符合；盤前仍須確認試撮、五檔與交易限制'}

def technical_for(s,symbol):
    """Hand-researched structure (with its invalidation anchor) when current, else the full-market scan's."""
    r=(s.fact(symbol,'research_detail') or {}).get('technical');m=s.fact(symbol,'technical') or None
    return r if r and (not m or r.get('as_of','')>=m.get('as_of','')) else m

def store_plan(s,i,now):
    from .valuation_quality import for_store
    quality=for_store(s,i)
    if quality:
        return {'band':'blocked','status':'valuation_gap','label':'價位需重新評估','entry':None,'buy_max':None,'close_date':(s.fact(i['symbol'],'public_close') or {}).get('trade_date'),'valid_until':None,'reason':quality['reason'],'is_live_order':False}
    return make_plan(i,s.active(i['symbol']),s.fact(i['symbol'],'public_close'),technical_for(s,i['symbol']),s.fact(i['symbol'],'risk'),s.meta('trading_calendar'),now)

def gate_signal(signal,plan,quote=None):
    if plan['status']=='valuation_gap' or (signal['status'] in ('sweet','add','buy') and plan['status']!='conditional'):
        return {**signal,'status':'blocked','status_label':'暫停','suggested':None,'conservative':None,'extreme':None,'valid_until':None,'reason':plan['reason'],'action':plan['reason']}
    if signal['status'] in ('sweet','add','buy') and quote:
        current=positive(quote.get('trial_price') if quote.get('is_trial') else quote.get('price'))
        if current and positive(plan.get('support')) and current<plan['support']:
            return {**signal,'status':'blocked','status_label':'暫停','suggested':None,'conservative':None,'extreme':None,'valid_until':None,'reason':'當日價格已跌破研究支撐，暫停掛價','action':'當日價格已跌破研究支撐，暫停掛價'}
    return signal
