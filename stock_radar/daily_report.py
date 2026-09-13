"""Bounded public-filing summary; no LLM, sample valuations or price targets."""
import argparse, json, math
from datetime import datetime, timedelta
from pathlib import Path
from .domain import TW
from .store import Store, atomic_json
from .cli import _bot_token, _send_with_outbox
from .discord import DISCORD_CHANNEL

def report(store, now):
    lines=['台股觀察清單｜公開財務摘要',now.strftime('%Y-%m-%d %H:%M 台北時間'),'營收成長不等於股價便宜；尚無核准估值，不提供買賣價。']
    rows=[]
    for i in store.instruments():
        if not i.get('watched'):continue
        f=store.fact(i['symbol'],'financials') or {}
        r=f.get('revenue') or {}
        y=r.get('yoy_pct'); period=r.get('period_roc_ym')
        try:
            at=datetime.fromisoformat(f.get('fetched_at',''))
            period_text=str(period)
            yr=int(period_text[:3])+1911; month=int(period_text[3:])
            age_months=(now.year-yr)*12+now.month-month
            fresh=at<=now and now-at<=timedelta(hours=30) and 1<=month<=12 and 0<=age_months<=2
        except (ValueError,TypeError):fresh=False
        valid=isinstance(y,(int,float)) and math.isfinite(y) and bool(period) and fresh
        text=f"{i['name']}({i['symbol']})｜"
        if valid:
            text+=f"營收 {period} 年增 {y:+.1f}%｜"+('營收較去年同期增加，待估值' if y>0 else '營收未較去年同期增加，待研究原因')
        else:text+='營收資料未取得，待研究'
        lines.append(text)
        rows.append({'symbol':i['symbol'],'name':i['name'],'text':text,'source_url':r.get('source_url'),'fetched_at':f.get('fetched_at')})
    lines+=['來源：https://openapi.twse.com.tw/v1/opendata/t187ap05_L','網頁：https://changjaykid.github.io/fugle-dashboard/']
    text='\n'.join(lines)
    if len(text)>1900:raise ValueError('Report exceeds one-message limit')
    return {'generated_at':now.isoformat(),'items':rows,'text':text}

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--send',action='store_true');a=p.parse_args()
    s=Store(a.db)
    try:
        now=datetime.now(TW);r=report(s,now);atomic_json(a.out,r)
        if a.send:
            # Use a date-stable message for same-day retries.
            text=r['text'].replace(now.strftime('%Y-%m-%d %H:%M 台北時間'),now.strftime('%Y-%m-%d 台北時間'))
            result=_send_with_outbox(s,_bot_token(),text,channel=DISCORD_CHANNEL,now=now,max_attempts=1)
            print(json.dumps({'delivery':result}))
        else:print(r['text'])
    finally:s.close()
if __name__=='__main__':main()
