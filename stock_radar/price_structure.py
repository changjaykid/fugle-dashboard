"""Bounded, cached daily structure refresh. No LLM or valuation changes."""
import argparse,json,statistics
from datetime import datetime,timedelta,date
from .domain import TW
from .store import Store
from .buy_plan import last_completed_day

def num(v):return float(str(v).replace(',',''))
def roc(v):
    y,m,d=map(int,v.split('/'));return date(y+1911,m,d)
def calculate(bars,flows,as_of):
    rows=sorted({x[0]:x for x in bars if roc(x[0])<=as_of}.values(),key=lambda x:roc(x[0]))[-60:]
    if len(rows)<20 or roc(rows[-1][0])!=as_of:raise ValueError('價格結構日期不完整')
    cl=[num(x[6]) for x in rows];lo=[num(x[5]) for x in rows];ma=statistics.mean(cl[-20:])
    supports=sorted(set([min(lo[-20:])]+[lo[k] for k in range(max(1,len(rows)-20),len(rows)-1) if lo[k]<=lo[k-1] and lo[k]<lo[k+1]]))
    days=sorted(flows)[-5:]
    if len(days)!=5 or days[-1]!=str(as_of):raise ValueError('近五日法人資料不完整')
    foreign=sum(flows[d]['foreign'] for d in days);trust=sum(flows[d]['trust'] for d in days)
    return {'as_of':str(as_of),'flow_as_of':days[-1],'ma20':round(ma,2),'support':min(lo[-20:]),'resistance':max(num(x[4]) for x in rows[-20:]),'supports':supports,'requires_stabilization':cl[-1]<ma and foreign+trust<0,'foreign_5d_lots':round(foreign,3),'trust_5d_lots':round(trust,3),'flow_start':days[0]}

def sync(s,now):
    import requests
    day=last_completed_day(now,s.meta('trading_calendar'));watched=[i for i in s.instruments() if i.get('watched') and i['kind']=='stock'];symbols={i['symbol'] for i in watched}
    cache=s.meta('structure_flows') or {};needed=[];d=day
    from .calendar import is_trading_day
    while len(needed)<5:
        if is_trading_day(d,s.meta('trading_calendar')):needed.append(str(d))
        d-=timedelta(days=1)
    for ds in needed:
        if ds in cache:continue
        r=requests.get('https://www.twse.com.tw/fund/T86',params={'date':ds.replace('-',''),'selectType':'ALLBUT0999','response':'json'},timeout=20);r.raise_for_status();body=r.json()
        if body.get('date')!=ds.replace('-','') or not body.get('data'):raise ValueError('法人日期不符')
        cache[ds]={x[0]:{'foreign':num(x[4])/1000,'trust':num(x[10])/1000} for x in body['data'] if x[0] in symbols}
    cache={d:cache[d] for d in sorted(cache)[-10:]};s.set_meta('structure_flows',cache)
    for i in watched:
        sym=i['symbol'];bars=s.fact(sym,'price_bars').get('rows',[]);months={day.strftime('%Y%m01')}
        if len(bars)<20:
            first=day.replace(day=1)
            for _ in range(2):first=(first-timedelta(days=1)).replace(day=1);months.add(first.strftime('%Y%m01'))
        for month in sorted(months):
            r=requests.get('https://www.twse.com.tw/exchangeReport/STOCK_DAY',params={'date':month,'stockNo':sym,'response':'json'},timeout=20);r.raise_for_status();body=r.json()
            fresh=body.get('data',[])
            if body.get('date')!=month or not fresh or any(roc(x[0]).strftime('%Y%m')!=month[:6] for x in fresh):raise ValueError('日成交月份不符')
            bars+=fresh
        bars=sorted({x[0]:x for x in bars if roc(x[0])<=day}.values(),key=lambda x:roc(x[0]))[-60:]
        s.set_fact(sym,'price_bars',{'rows':bars})
        flows={d:cache[d][sym] for d in needed if sym in cache[d]};t=calculate(bars,flows,day)
        detail=s.fact(sym,'research_detail') or {}
        if not detail:continue
        # Never silently roll a breached research support lower after a breakdown.
        anchor=detail.get('technical',{}).get('invalidation_support',detail.get('technical',{}).get('support'))
        if anchor:t['invalidation_support']=anchor;t['support']=max(t['support'],anchor)
        detail['technical']=t;detail['chips']=f"{t['flow_start']}～{t['flow_as_of']}，外資 {t['foreign_5d_lots']:+,.0f} 張、投信 {t['trust_5d_lots']:+,.0f} 張；為已公布的近五日合計。"
        s.set_fact(sym,'research_detail',detail);research=s.fact(sym,'research');research['chips']=detail['chips'];s.set_fact(sym,'research',research)
    s.set_meta('structure_sync',{'at':now.isoformat(),'trade_date':str(day),'count':len(watched)})
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);a=p.parse_args();s=Store(a.db)
    try:sync(s,datetime.now(TW))
    finally:s.close()
