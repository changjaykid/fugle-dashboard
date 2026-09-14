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
    from .buy_plan import format_plans
    return format_plans(s,now)

def morning(s,radar,now):
    from .discord import format_daily_summary,_revalidate_signal
    watched=[i for i in radar['items'] if i.get('watched')]
    eligible=[i for i in watched if _revalidate_signal(i,now)['status'] in ('sweet','add','buy')]
    if eligible:
        lines=['台股盤前｜'+now.strftime('%Y-%m-%d %H:%M'),'通過行情與分析檢查，可考慮以下限價：']
        for i in eligible[:6]:
            sig=_revalidate_signal(i,now);v=i['valuation'];r=s.fact(i['symbol'],'research_detail') or {}
            lines.append(f"{i['name']} {i['symbol']}｜嘗試掛 {sig['suggested']:g} 元，上限 {v['buy']:g} 元\n{r.get('thesis','')}\n風險：{(r.get('risks') or ['待確認'])[0]}\n有效至 {sig['valid_until']}，過期即停用。")
        lines.append(SITE)
        text='\n'.join(lines)
        return ('【測試資料，非即時交易建議】\n' if radar.get('mode')!='live' else '')+text
    text=build(s,now)
    prefix='台股盤前｜'+now.strftime('%Y-%m-%d %H:%M')+'\n本次沒有通過全部即時掛價條件的標的。\n'
    if radar.get('mode')!='live':prefix='【測試資料，非即時交易建議】\n'+prefix
    return prefix+text

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
