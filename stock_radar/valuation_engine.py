"""Daily rule-based valuation for watched stocks. Deterministic, sourced, no LLM.

Consistent basis: TWSE's daily P/E is price / trailing-four-quarter EPS, so
trailing EPS = close / P/E, and fair value is that EPS at the median of the same
daily P/E over the past year. Growth is NOT added: the historical multiple already
carries the market's growth expectations, and real growth enters when a new
quarter lifts trailing EPS. Revenue YoY is reported as context only.
Companies without a usable P/E fall back to book value x median P/B.
"""
import argparse,statistics,time
from datetime import date,datetime,timedelta
from pathlib import Path
from .domain import TW,positive,floor_tick,stamp
from .store import Store

URL='https://www.twse.com.tw/exchangeReport/BWIBBU'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; StockRadar/1.0)'}
# Above this median P/E, earnings are too thin relative to price for a P/E band to be stable.
MAX_FAIR_PE=50
# Per the stock skill: financials are valued on book (P/B), and cyclicals are not valued on the P/E of
# the current cycle -- peak earnings make them look cheap exactly when they are not. Both use the P/B band.
BOOK_INDUSTRIES={'金融保險業':'金融業以淨值（P/B）評價','航運業':'景氣循環股','鋼鐵工業':'景氣循環股','塑膠工業':'景氣循環股','水泥工業':'景氣循環股',
                 '玻璃陶瓷':'景氣循環股','造紙工業':'景氣循環股','化學工業':'景氣循環股','橡膠工業':'景氣循環股','油電燃氣業':'景氣循環股'}
MONTHS=13;MIN_SAMPLES=120;BANDS=(.65,.75,.85);VALID_DAYS=3;PAUSE=2.0

def _num(v):
    try:n=float(str(v).replace(',',''))
    except (TypeError,ValueError):return None
    return n if n>0 else None
def _roc(v):
    y,m,d=int(v[:3]),int(v[4:6]),int(v[7:9]);return date(y+1911,m,d)
def _months(today):
    y,m=today.year,today.month;out=[]
    for _ in range(MONTHS):
        out.append(f'{y}{m:02d}');y,m=(y,m-1) if m>1 else (y-1,12)
    return sorted(out)

def fetch_month(sym,name,ym,http):
    """One month of daily ratios, rejected unless the response is provably that stock and month."""
    b=http.get(URL,params={'date':ym+'01','stockNo':sym,'response':'json'},headers=HEADERS,timeout=20).json()
    rows=b.get('data') or []
    if b.get('stat')!='OK' or b.get('date')!=ym+'01' or name not in (b.get('title') or '') or not rows:
        raise ValueError(f'{sym} {ym} 本益比資料不符')
    for r in rows:
        d=_roc(r[0])
        if f'{d.year}{d.month:02d}'!=ym:raise ValueError(f'{sym} {ym} 日期不符')
    return rows

def sync_history(s,now,http=None,pause=PAUSE):
    """A month is final only once fetched after it ended; until then (and the current month) it is re-fetched."""
    import requests
    http=http or requests;current=f'{now.year}{now.month:02d}';failed=[]
    for i in [i for i in s.instruments() if i.get('watched') and i['kind']=='stock']:
        h=s.fact(i['symbol'],'ratio_history') or {'months':{}}
        months=_months(now.date());h['months']={k:v for k,v in h['months'].items() if k in months}
        h['complete']=[m for m in h.get('complete',[]) if m in months]
        for ym in months:
            if ym in h['complete']:continue
            try:
                h['months'][ym]=fetch_month(i['symbol'],i.get('name') or '',ym,http)
                if ym!=current:h['complete'].append(ym)
            except Exception:failed.append(f"{i['symbol']}:{ym}")
            time.sleep(pause)
        s.set_fact(i['symbol'],'ratio_history',h)
    return failed

def compute(inst,close,fin,history):
    """Returns (valuation fields, None) or (None, reason). Pure: no I/O."""
    if not close or not positive(close.get('close')):return None,'缺少收盤價'
    px,day=close['close'],date.fromisoformat(close['trade_date'])
    rows=sorted([r for m in history.get('months',{}).values() for r in m],key=lambda r:_roc(r[0]))
    rows=[r for r in rows if day-timedelta(days=366)<_roc(r[0])<=day]
    today=next((r for r in rows if _roc(r[0])==day),None)
    if not today:return None,f"缺少 {close['trade_date']} 的本益比資料"
    pes=[_num(r[3]) for r in rows if _num(r[3])];pbs=[_num(r[4]) for r in rows if _num(r[4])]
    rev=(fin or {}).get('revenue') or {}
    period=f"{rev.get('period_roc_ym','')[:3]} 年 {rev.get('period_roc_ym','')[3:]} 月"
    thin=len(pes)>=MIN_SAMPLES and statistics.median(pes)>MAX_FAIR_PE
    book=BOOK_INDUSTRIES.get(inst.get('industry_code') or inst.get('industry') or '')
    if _num(today[3]) and len(pes)>=MIN_SAMPLES and not thin and not book:
        ttm=px/_num(today[3]);ytd=(rev['cumulative_revenue']/rev['cumulative_revenue_last_year']-1) if positive(rev.get('cumulative_revenue')) and positive(rev.get('cumulative_revenue_last_year')) else None
        month=rev.get('yoy_pct')/100 if isinstance(rev.get('yoy_pct'),(int,float)) else None
        fair_pe=statistics.median(pes);fair=ttm*fair_pe
        lo,hi=statistics.quantiles(pes,n=4)[0],statistics.quantiles(pes,n=4)[2]
        growth=[t for t in (f"今年累計 {ytd:+.1%}" if ytd is not None else '',f"{period} 單月 {month:+.1%}" if month is not None else '') if t]
        reason=(f"過去四季 EPS {ttm:.2f} 元（收盤 {px:g} ÷ 本益比 {_num(today[3]):g}）× 過去一年本益比中位數 {fair_pe:.2f} 倍（四分位 {lo:.1f}～{hi:.1f}）＝ 合理價 {fair:.2f} 元。"
                f"成長不另外加價：歷史本益比已含市場對成長的預期，實際成長會在新季報提高 EPS 後反映。景氣循環股在獲利高峰時本益比會偏低，看似便宜未必是真便宜。"+(f"參考：營收年增 {'、'.join(growth)}。" if growth else ''))
        v={'method':'pe_band','inputs':{'ttm_eps':round(ttm,4),'forward_eps':round(ttm,4),'fair_pe':round(fair_pe,4),'pe_today':_num(today[3]),'pe_q1':round(lo,2),'pe_q3':round(hi,2),'samples':len(pes),'revenue_ytd_yoy':None if ytd is None else round(ytd,4),'revenue_month_yoy':None if month is None else round(month,4)}}
    elif _num(today[4]) and len(pbs)>=MIN_SAMPLES:
        bvps=px/_num(today[4]);fair_pb=statistics.median(pbs);fair=bvps*fair_pb
        why=(f"{book}，獲利高低會隨景氣或利率大幅波動，不用當期本益比" if book and book.startswith('景氣') else book) if book else f"過去一年本益比中位數 {statistics.median(pes):.0f} 倍，獲利相對股價太薄，本益比估值不穩定" if thin else '過去一年多數時間沒有可用本益比（虧損或無資料）'
        reason=f"{why}，改用淨值：每股淨值 {bvps:.2f} 元（收盤 {px:g} ÷ 股價淨值比 {_num(today[4]):g}）× 過去一年股價淨值比中位數 {fair_pb:.2f} 倍 ＝ 合理價 {fair:.2f} 元。"
        v={'method':'pb_band','inputs':{'book_value_per_share':round(bvps,4),'fair_pb':round(fair_pb,4),'pb_today':_num(today[4]),'samples':len(pbs)}}
    else:return None,f'過去一年本益比／淨值比樣本不足 {MIN_SAMPLES} 筆'
    v.update(fair=round(fair,2),**{k:floor_tick(fair*b,inst['kind']) for k,b in zip(('sweet','add','buy'),BANDS)})
    v['reason']=reason+f" 甜甜、加碼、買進分別取合理價的 {BANDS[0]:.0%}、{BANDS[1]:.0%}、{BANDS[2]:.0%}，為安全邊際政策，非回測最佳參數。"
    return v,None

def finish(s,inst,v,now,source):
    """Adds the fields every published valuation carries: identity, validity, thesis, review and sources."""
    sym=inst['symbol'];fin=s.fact(sym,'financials') or {};detail=s.fact(sym,'research_detail') or {};etf=v['method']=='etf_band'
    v.update(id=f"RULE-{now:%Y%m%d}-{sym}",as_of=now.isoformat(),valid_until=(now+timedelta(days=VALID_DAYS)).isoformat(),
             thesis=detail.get('thesis') or ('ETF 依一年價格位置判斷，不做個股研究。' if etf else '依規則估值；尚無個股研究論述。'),evidence_reviewed=True,review_mode='rule-engine-v1',
             analysis_review={k:True for k in ('financials','outlook','valuation_basis','price_structure','risks')},
             invalidation=['每日依最新收盤與全市場資料重算；資料缺漏時不更新，舊估值於效期後停用']+([] if etf else ['新季報公布後過去四季 EPS 自動更新']),
             sources=[{'url':source,'as_of':now.isoformat(),'title':'證交所｜全市場每日收盤行情（過去一年）' if etf else '證交所｜本益比、股價淨值比（過去一年）'}]
                     +([] if etf else [{'url':'https://openapi.twse.com.tw/v1/opendata/t187ap05_L','as_of':fin.get('fetched_at') or now.isoformat(),'title':'證交所｜月營收'}]))
    return v

def build(s,inst,now):
    sym=inst['symbol'];close=s.fact(sym,'public_close')
    v,why=compute(inst,close,s.fact(sym,'financials'),s.fact(sym,'ratio_history'))
    if not v:return None,why
    return finish(s,inst,v,now,f"{URL}?date={close['trade_date'][:7].replace('-','')}01&stockNo={sym}&response=json"),None

def publish_all(s,now,built):
    """built: [(inst, valuation or None, reason)]. Republish only when prices change or validity runs short."""
    out=[]
    for inst,v,why in built:
        if not v:out.append((inst['symbol'],'skip',why));continue
        old=s.active(inst['symbol']);left=stamp((old or {}).get('valid_until'))
        # Same prices and the same basis (method and the reason's leading clause) need no new version.
        basis=lambda x:(x.get('method'),(x.get('reason') or '').split('：')[0])
        if old and old.get('review_mode')==v['review_mode'] and all(old.get(k)==v[k] for k in ('sweet','add','buy')) and basis(old)==basis(v) and left and left-now>timedelta(hours=36):
            out.append((inst['symbol'],'same',v['buy']));continue
        # One bad symbol must not stop the others; its previous valuation simply expires.
        try:s.publish_reviewed_analysis(s.propose(inst['symbol'],v,now=now),now=now);out.append((inst['symbol'],'published',v['buy']))
        except (ValueError,PermissionError) as e:out.append((inst['symbol'],'error',str(e)))
    return out

def publish(s,now):
    return publish_all(s,now,[(i,*build(s,i,now)) for i in s.instruments() if i.get('watched') and i['kind']=='stock'])

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--no-fetch',action='store_true');a=p.parse_args()
    s=Store(a.db);now=datetime.now(TW)
    try:
        failed=[] if a.no_fetch else sync_history(s,now)
        if failed:print('ratio history fetch failed:',','.join(failed))
        for row in publish(s,now):print(*row)
    finally:s.close()
if __name__=='__main__':main()
