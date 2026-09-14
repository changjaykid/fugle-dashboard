"""Export public research and licensed closing prices; never private Fugle quotes."""
import json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from stock_radar.store import Store
from stock_radar.domain import TW
from stock_radar.research_brief import valid_research
from datetime import datetime

def sanitize(r,s,now):
    r['coverage']['quotes']=0
    counts={'watched':0,'research':0,'proposals':0,'public_closes':0}
    for i in r['items']:
        i['quote']=None
        i['signal'].update(status='stale' if i.get('valuation') else 'pending',status_label='待確認' if i.get('valuation') else '待估值',suggested=None,conservative=None,extreme=None,action='查看研究與待核准提案；公開收盤價不作盤前掛價')
        c=s.fact(i['symbol'],'public_close') or None
        i['public_close']=c
        if c:counts['public_closes']+=1
        if i.get('watched'):counts['watched']+=1
        detail=s.fact(i['symbol'],'research_detail') or None
        i['research_detail']=detail
        if detail:
            i['description']=detail['description']
            if valid_research(detail,now):
                counts['research']+=1
                if detail.get('proposal'):counts['proposals']+=1
    r['coverage'].update(counts)
    r['health']=[h for h in r.get('health',[]) if h.get('name') not in ('試撮','報價','公開頁範圍','公開收盤')]
    r['health'].append({'name':'公開收盤','status':'ok' if counts['public_closes'] else 'blocked','detail':'證交所開放資料；每筆顯示實際交易日，不是即時行情。','as_of':(s.meta('public_close_sync') or {}).get('at')})
    r['health'].append({'name':'研究涵蓋','status':'partial','detail':f"觀察 {counts['watched']} 檔；研究 {counts['research']} 檔；待核准提案 {counts['proposals']} 檔。母表不是已完成分析數量。",'as_of':r['generated_at']})
    return r

def main():
    h=pathlib.Path.home();r=json.loads((h/'kid-agent-os/services/stock-radar/docs/radar.json').read_text());s=Store(h/'kid-agent-data/stock-radar/prod.db')
    try:print(json.dumps(sanitize(r,s,datetime.now(TW)),ensure_ascii=False,allow_nan=False))
    finally:s.close()
if __name__=='__main__':main()
