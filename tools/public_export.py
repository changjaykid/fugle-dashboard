"""Export public research and licensed closing prices; never private Fugle quotes."""
import json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from stock_radar.store import Store
from stock_radar.domain import TW
from stock_radar.research_brief import valid_research
from stock_radar.buy_plan import store_plan
from datetime import datetime

def basic_fundamentals(f):
    import math
    if not f:return None
    inc=f.get('income') or {};rev=f.get('revenue') or {}
    def num(x):return x if isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) else None
    sales=num(inc.get('revenue'));op=num(inc.get('operating_income'));eps=num(inc.get('eps_basic'));yoy=num(rev.get('yoy_pct'));monthly=num(rev.get('revenue_this_month'))
    margin=round(op/sales*100,2) if op is not None and sales and sales>0 else None
    try:income_period=f"{int(inc['year_roc'])+1911} 年第 {inc['quarter']} 季累計"
    except (ValueError,KeyError,TypeError):income_period=None
    try:
        period=str(rev['period_roc_ym']);revenue_period=f"{int(period[:-2])+1911} 年 {int(period[-2:])} 月"
    except (ValueError,KeyError,TypeError):revenue_period=None
    reading=[]
    if yoy is not None:reading.append('月營收較去年同期增加' if yoy>0 else '月營收未較去年同期成長')
    if op is not None:reading.append('本業仍有營業利益' if op>0 else '本業尚未獲利')
    if eps is not None and eps<0:reading.append('每股盈餘為負，不能直接用一般本益比判斷便宜')
    if not reading:reading=['資料尚不足以形成基本面判斷']
    sources=[{'url':x['source_url'],'title':title,'as_of':f.get('fetched_at')} for x,title in [(inc,'證交所公開財報'),(rev,'證交所公開月營收')] if x.get('source_url','').startswith('https://')]
    return {'income_period':income_period,'revenue_period':revenue_period,'eps':eps,'operating_margin':margin,'monthly_revenue_100m':round(monthly/100000,2) if monthly is not None else None,'revenue_yoy':yoy,'reading':'；'.join(reading)+'。仍需檢查產業前景與合理價位。','sources':sources}

def sanitize(r,s,now):
    r['coverage']['quotes']=0
    counts={'watched':0,'research':0,'proposals':0,'public_closes':0}
    for i in r['items']:
        i['quote']=None
        i['fundamentals']=basic_fundamentals(s.fact(i['symbol'],'financials')) if i.get('kind')=='stock' else None
        i['signal'].update(status='stale' if i.get('valuation') else 'pending',status_label='待確認' if i.get('valuation') else '待估值',suggested=None,conservative=None,extreme=None,action='買進價尚未確認，先觀察；收盤價不是盤前掛價')
        c=s.fact(i['symbol'],'public_close') or None
        i['public_close']=c
        i['plan']=store_plan(s,i,now) if i.get('valuation') else None
        if c:counts['public_closes']+=1
        if i.get('watched'):counts['watched']+=1
        detail=s.fact(i['symbol'],'research_detail') or None
        if detail:
            detail={k:v for k,v in detail.items() if k not in ('proposal','proposal_db_id','research_version')}
        i['research_detail']=detail
        i['valuation_history']=[]
        if detail:
            i['description']=detail['description']
            if valid_research(detail,now):
                counts['research']+=1
                if detail.get('proposal'):counts['proposals']+=1
    r['coverage'].update(counts)
    from stock_radar.track import stats as track_stats
    r['track']=track_stats(s)
    from stock_radar.research_brief import research_coverage
    r['research_coverage']=research_coverage(s,now)
    r['health']=[h for h in r.get('health',[]) if h.get('name') not in ('試撮','報價','公開頁範圍','公開收盤')]
    r['health'].append({'name':'公開收盤','status':'ok' if counts['public_closes'] else 'blocked','detail':'證交所開放資料；每筆顯示實際交易日，不是即時行情。','as_of':(s.meta('public_close_sync') or {}).get('at')})
    r['health'].append({'name':'研究涵蓋','status':'partial','detail':f"觀察 {counts['watched']} 檔；研究 {counts['research']} 檔；以收盤資料建立買進計畫；即時掛價由盤前通知確認。母表不是已完成分析數量。",'as_of':r['generated_at']})
    return r

def main():
    h=pathlib.Path.home();r=json.loads((h/'kid-agent-os/services/stock-radar/docs/radar.json').read_text());s=Store(h/'kid-agent-data/stock-radar/prod.db')
    try:print(json.dumps(sanitize(r,s,datetime.now(TW)),ensure_ascii=False,allow_nan=False))
    finally:s.close()
if __name__=='__main__':main()
