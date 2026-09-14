"""Weekend reports from recorded evidence; never infer fills or invent valuations."""
import argparse,json
from datetime import datetime,timedelta
from pathlib import Path
from .domain import TW,stamp
from .store import Store,atomic_json
from .cli import _bot_token,_send_with_outbox
from .discord import DISCORD_CHANNEL

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
        for i in list(items.values())[:10]:lines.append(f"{i.get('name',i['symbol'])}({i['symbol']})：曾列候選；觸價及後續表現尚未驗證。")
        lines.append('未接入後續行情回顧前，不計算報酬、不推測你的成交或持倉。')
    else:
        from .buy_plan import format_plans
        return format_plans(store,now).replace('台股買進計畫｜','下週股票觀察清單｜').replace('08:50 再確認當日行情','下個交易日 08:50 再確認行情')
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
        if a.send:print(json.dumps(_send_with_outbox(s,_bot_token(),text,channel=DISCORD_CHANNEL,now=now,max_attempts=1)))
        else:print(text)
    finally:s.close()
if __name__=='__main__':main()
