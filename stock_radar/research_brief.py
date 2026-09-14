"""Sourced research + licensed closing prices; no LLM and no automatic approval."""
import argparse,csv,io,json,math
from datetime import datetime,date,timedelta
from pathlib import Path
from .domain import TW,stamp
from .store import Store,atomic_json
URL='https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=open_data'
SITE='https://changjaykid.github.io/fugle-dashboard/'

def parse_closes(text,now):
    out={}
    for row in csv.DictReader(io.StringIO(text.lstrip('\ufeff'))):
        try:
            raw=row['日期'];d=date(int(raw[:3])+1911,int(raw[3:5]),int(raw[5:7]))
            n=float(row['收盤價'].replace(',',''))
            if d>now.date() or (d==now.date() and now.hour<14) or not math.isfinite(n) or n<=0:continue
            out[row['證券代號']]={'close':n,'trade_date':str(d),'source_url':URL,'dataset_url':'https://data.gov.tw/dataset/11549','fetched_at':now.isoformat()}
        except (KeyError,ValueError,TypeError):continue
    if not out:raise ValueError('No valid dated public closing prices')
    return out

def sync_close(s,now):
    import requests
    r=requests.get(URL,timeout=25);r.raise_for_status();rows=parse_closes(r.text,now)
    for i in s.instruments():
        if i['symbol'] in rows:s.set_fact(i['symbol'],'public_close',rows[i['symbol']])
    s.set_meta('public_close_sync',{'at':now.isoformat(),'count':len(rows)})

def install(s,doc,now):
    for r in doc['items']:
        sym=r['symbol'];p=r.get('proposal')
        if p:
            existing=next((v for v in s.versions(sym) if v['payload'].get('id')==p['id']),None)
            r['proposal_db_id']=existing['id'] if existing else s.propose(sym,p,now=now)
        # Narrative facts do not alter active valuations or the trading decision.
        s.set_fact(sym,'research',{k:r[k] for k in ('thesis','why_now','chips','catalysts','risks','sources')})
        s.set_fact(sym,'research_detail',r)

def valid_research(r,now):
    a=stamp(r.get('as_of'));b=stamp(r.get('valid_until'))
    return bool(a and b and a<=now<b)

def build(s,now):
    watched=[i for i in s.instruments() if i.get('watched')]
    rows=[]
    for i in watched:
        r=s.fact(i['symbol'],'research_detail') or {}
        if valid_research(r,now):rows.append((i,r,s.fact(i['symbol'],'public_close') or {}))
    lines=['台股觀察重點｜'+now.strftime('%Y-%m-%d'),'買進條件尚未完整，以下只供觀察，今天先不依這份清單掛單。']
    for i,r,c in rows:
        lines.append(f"{i['name']} {i['symbol']}｜{r.get('stance','先觀察')}\n{r['thesis']}\n風險：{r['risks'][0]}。")
    if not rows:lines.append('分析資料不足，暫時無法提供觀察名單。')
    lines.append(SITE)
    text='\n'.join(lines)
    if len(text)>1900:raise ValueError('Research brief exceeds Discord limit')
    return text

def morning(s,radar,now):
    from .discord import format_daily_summary,_revalidate_signal
    watched=[i for i in radar['items'] if i.get('watched')]
    eligible=[i for i in watched if _revalidate_signal(i,now)['status'] in ('sweet','add','buy')]
    if eligible:
        scoped={**radar,'items':watched}
        return format_daily_summary(scoped,now=now)+'\n'+SITE
    pending=sum(i.get('signal',{}).get('status')=='pending' for i in watched)
    stale=sum(i.get('signal',{}).get('status') in ('stale','blocked') for i in watched)
    lines=['台股盤前｜'+now.strftime('%Y-%m-%d %H:%M')]
    if radar.get('mode')!='live':lines.insert(0,'【測試資料，非即時交易建議】')
    if pending or stale:
        lines+=['今天先等：目前無法確認可買名單。',f'本次追蹤 {len(watched)} 檔；買進分析未完成 {pending} 檔，行情或風險尚待確認 {stale} 檔。']
    else:lines+=['今天先等：目前價格沒有符合既定買進條件。']
    notes=[]
    for i in watched:
        r=s.fact(i['symbol'],'research_detail') or {}
        if valid_research(r,now):notes.append(f"{i['name']} {i['symbol']}｜觀察 {r.get('catalysts',['待確認'])[0]}；風險：{r.get('risks',['待確認'])[0]}。")
    if notes:lines+=['觀察重點（不是買進推薦）：']+notes[:5]
    lines.append(SITE)
    return '\n'.join(lines)

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--import-file',type=Path);p.add_argument('--sync-close',action='store_true');p.add_argument('--send',action='store_true');p.add_argument('--morning-radar',type=Path);p.add_argument('--out',type=Path);a=p.parse_args();now=datetime.now(TW);s=Store(a.db)
    try:
        if a.import_file:install(s,json.loads(a.import_file.read_text()),now)
        if a.sync_close:sync_close(s,now)
        text=morning(s,json.loads(a.morning_radar.read_text()),now) if a.morning_radar else build(s,now)
        if a.out:atomic_json(a.out,{'generated_at':now.isoformat(),'text':text})
        if a.send:
            from .cli import _bot_token,_send_with_outbox
            from .discord import DISCORD_CHANNEL
            r=_send_with_outbox(s,_bot_token(),text,channel=DISCORD_CHANNEL,now=now,max_attempts=1)
            print(json.dumps(r))
        else:print(text)
    finally:s.close()
if __name__=='__main__':main()
