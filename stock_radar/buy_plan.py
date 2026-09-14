"""A dated closing-price plan, distinct from an executable intraday signal."""
from datetime import datetime,timedelta
from .domain import TW,positive,stamp,validate_valuation,floor_tick
from .calendar import is_trading_day

def last_completed_day(now,schedule):
    d=now.date() if now.hour>=14 else now.date()-timedelta(days=1)
    for _ in range(16):
        if is_trading_day(d,schedule):return d
        d-=timedelta(days=1)
    raise ValueError('交易日曆無法確認最近收盤日')

def make_plan(instrument,valuation,close,technical,risk,schedule,now):
    p={'band':'stale','status':'missing','label':'等待資料','entry':None,'buy_max':None,'close_date':(close or {}).get('trade_date'),'valid_until':None,'reason':'等待完整且有效的分析資料','is_live_order':False}
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
        support=positive(technical.get('support'))
        if not support or n<support:
            p.update(status='broken',label='跌破支撐，先等',reason='已跌破原先支撐，暫停買進計畫並重新檢查');return p
        p['support']=support
        supports=[positive(x) for x in technical.get('supports',[])]
        entries=[x for x in supports if x and x<=min(n,valuation['buy'])]
        if p['band']=='avoid':
            p.update(status='no_chase',label='不追，等回落',reason=f"收盤高於買進上限 {valuation['buy']:g} 元，等待回落再確認");return p
        if not entries:
            p.update(status='waiting',label='等承接位置',reason='估值符合，但上限內尚無可確認的支撐');return p
        entry=floor_tick(max(entries),instrument['kind'])
        wait=technical.get('requires_stabilization') is True
        p.update(entry=entry,status='wait_stabilize' if wait else 'conditional',label='等止穩再考慮' if wait else '可列入分批計畫',reason='低於月線且近五日外資與投信合計賣超，先等收盤站回月線或近五日賣壓改善' if wait else '估值與承接位置符合；盤前仍須確認試撮、五檔與交易限制')
        return p
    except (ValueError,TypeError,KeyError):return p

def store_plan(s,i,now):
    r=s.fact(i['symbol'],'research_detail') or {}
    return make_plan(i,s.active(i['symbol']),s.fact(i['symbol'],'public_close'),r.get('technical'),s.fact(i['symbol'],'risk'),s.meta('trading_calendar'),now)

def gate_signal(signal,plan,quote=None):
    if signal['status'] in ('sweet','add','buy') and plan['status']!='conditional':
        return {**signal,'status':'blocked','status_label':'暫停','suggested':None,'conservative':None,'extreme':None,'valid_until':None,'reason':plan['reason'],'action':plan['reason']}
    if signal['status'] in ('sweet','add','buy') and quote:
        current=positive(quote.get('trial_price') if quote.get('is_trial') else quote.get('price'))
        if current and positive(plan.get('support')) and current<plan['support']:
            return {**signal,'status':'blocked','status_label':'暫停','suggested':None,'conservative':None,'extreme':None,'valid_until':None,'reason':'當日價格已跌破研究支撐，暫停掛價','action':'當日價格已跌破研究支撐，暫停掛價'}
    return signal

def format_plans(s,now):
    pairs=[(i,store_plan(s,i,now)) for i in s.instruments() if i.get('watched')]
    candidates=[(i,p) for i,p in pairs if p.get('entry')]
    lines=['台股買進計畫｜'+now.strftime('%Y-%m-%d'),'以最近完成交易日收盤規劃；08:50 再確認當日行情。']
    for i,p in candidates[:6]:
        r=s.fact(i['symbol'],'research_detail') or {}
        lines.append(f"\n{i['name']} {i['symbol']}｜{p['label']}\n承接參考 {p['entry']:g} 元／買進上限 {p['buy_max']:g} 元\n{p['reason']}。\n理由：{r.get('thesis','待確認')}\n風險：{(r.get('risks') or ['待確認'])[0]}。")
    rest=[i['name'] for i,p in pairs if p['status']=='no_chase']
    if rest:lines.append('\n高於買進上限，先不追：'+'、'.join(rest)+'。')
    if not candidates:lines.append('目前沒有資料完整的承接計畫，請查看網站各檔等待原因。')
    dates=sorted({p['close_date'] for i,p in pairs if p['close_date']})
    if dates:lines.append('價格依據：'+dates[-1]+' 收盤；承接參考不是即時掛價。')
    lines.append('https://changjaykid.github.io/fugle-dashboard/')
    text='\n'.join(lines)
    if len(text)>1900:raise ValueError('通知內容超過長度限制')
    return text
