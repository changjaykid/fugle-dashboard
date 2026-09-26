"""Weekend reports from recorded evidence; never infer fills or invent valuations."""
import argparse,json
from datetime import datetime,timedelta
from pathlib import Path
from .domain import TW,stamp
from .store import Store,atomic_json
from .cli import _bot_token,_send_with_outbox
from .discord import channel_for

def build(kind,now,store,folder):
    lines=[]
    if kind=='weekly':
        monday=now.date()-timedelta(days=now.weekday())
        records=[]
        for p in sorted(folder.glob('*.json')) if folder.exists() else []:
            try:
                x=json.loads(p.read_text());at=stamp(x.get('generated_at'))
                if at and monday<=at.astimezone(TW).date()<=now.date() and x.get('mode')=='live':records.append(x)
            except (ValueError,OSError):continue
        lines=['本週股票候選回顧',f'{monday} ～ {now.date()}']
        days={stamp(x['generated_at']).astimezone(TW).date() for x in records}
        items={i['symbol']:i for r in records for i in r.get('items',[]) if i.get('signal',{}).get('status') in ('sweet','add','buy')}
        lines.append(f'已保留 {len(days)} 天、{len(records)} 次盤前快照；其餘日期未驗證，不視為沒有候選。')
        if not items:lines.append('快照中沒有可確認的合格候選；可能是資料／估值不足，不能推論市場沒有機會。')
        from .price_structure import roc,num
        from .buy_plan import last_completed_day,store_plan
        try:latest=last_completed_day(now,store.meta('trading_calendar'))
        except ValueError:latest=None
        changes=[]
        for i in store.instruments():
            if not i.get('watched'):continue
            bars=store.fact(i['symbol'],'price_bars').get('rows',[])
            before=sorted([x for x in bars if roc(x[0])<monday],key=lambda x:roc(x[0]))
            during=sorted([x for x in bars if monday<=roc(x[0])<=now.date()],key=lambda x:roc(x[0]))
            if not before or not during or roc(during[-1][0])!=latest:continue
            first,last=num(before[-1][6]),num(during[-1][6])
            if first<=0:continue
            pct=(last/first-1)*100;plan=store_plan(store,i,now)
            changes.append((pct,f"{i['name']} {i['symbol']}｜{first:g} → {last:g} 元（{pct:+.2f}%）；{plan['label']}"))
        if changes:
            lines.append('觀察股一週收盤變化（前週末收盤至本週最後交易日；不含股息）：')
            lines += [text for _,text in sorted(changes,reverse=True)[:10]]
        else:lines.append('本週收盤資料尚未完整，暫不計算一週價格變化。')
        if items:lines.append('本週曾通過即時條件：'+'、'.join(i.get('name',i['symbol']) for i in items.values()))
        lines.append('以上為市場價格變化，不代表你的成交或投資報酬；除權息與分割可能影響可比性。')
        from .track import summary
        tracked=summary(store)
        if len('\n'.join(lines))+len(tracked)<1700:lines.append(tracked)
    else:
        from .buy_plan import format_plans
        return format_plans(store,now).replace('台股買進計畫｜','下週股票觀察清單｜').replace('08:55 再確認當日行情','下個交易日 08:55 再確認行情')
    lines.append('完整資料：https://changjaykid.github.io/fugle-dashboard/')
    text='\n'.join(lines)
    if len(text)>1900:raise ValueError('Report too long')
    return text

def main():
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['weekly','outlook']);p.add_argument('--send',action='store_true');a=p.parse_args()
    d=Path.home()/'kid-agent-data/stock-radar';s=Store(d/'prod.db');now=datetime.now(TW)
    try:
        text=build(a.kind,now,s,d/'snapshots')
        atomic_json(d/(a.kind+'-latest.json'),{'generated_at':now.isoformat(),'text':text})
        if a.send:print(json.dumps(_send_with_outbox(s,_bot_token(),text,channel=channel_for('review'),now=now,max_attempts=1)))
        else:print(text)
    finally:s.close()
if __name__=='__main__':main()
