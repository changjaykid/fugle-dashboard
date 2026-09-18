"""Sourced research + licensed closing prices; no LLM and no automatic approval."""
import argparse,csv,io,json,math
from datetime import datetime,date,timedelta
from pathlib import Path
from .domain import TW,stamp,positive
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

WEEKDAYS='一二三四五六日'
def _why(v):
    """One line a person can check: how far today's multiple is below the stock's own one-year norm."""
    i=(v or {}).get('inputs') or {}
    for today,norm,name in (('pe_today','fair_pe','本益比'),('pb_today','fair_pb','股價淨值比')):
        if positive(i.get(today)) and positive(i.get(norm)):
            gap=1-i[today]/i[norm]
            return f"{name} {i[today]:g}，{'比一年常態 '+format(i[norm],'.1f')+' 便宜 '+format(gap,'.0%') if gap>0 else '高於一年常態 '+format(i[norm],'.1f')}"
    return (v or {}).get('thesis') or '估值依據請見網站'

def morning(s,radar,now):
    """08:50 message: the answer first, then only what is needed to act on it."""
    from .discord import _revalidate_signal
    from .buy_plan import store_plan
    head=f"台股盤前｜{now.month}/{now.day}（{WEEKDAYS[now.weekday()]}）{now:%H:%M}"
    valued=[i for i in radar['items'] if i.get('valuation') and (i.get('watched') or i.get('kind')=='stock')]
    go=[(i,_revalidate_signal(i,now)) for i in valued];go=[(i,g) for i,g in go if g['status'] in ('sweet','add','buy')][:5]
    plans={i['symbol']:store_plan(s,i,now) for i in valued}
    names=lambda st:[i['name'] for i in valued if plans[i['symbol']]['status']==st]
    lines=[head]
    if go:
        lines.append('【今天可考慮進場】限價掛單，買不到不追；是否下單由你決定。')
        for n,(i,g) in enumerate(go,1):
            r=s.fact(i['symbol'],'research_detail') or {};until=stamp(g.get('valid_until'))
            lines+=['',f"{'①②③④⑤'[n-1]} {i['name']} {i['symbol']}",f"掛 {g['suggested']:g} 元｜最高 {i['valuation']['buy']:g} 元，超過不追",f"理由：{_why(i['valuation'])}"]
            if r.get('risks'):lines.append(f"風險：{r['risks'][0]}")
            if until:lines.append(f"{until.astimezone(TW):%H:%M} 前有效，過期停用")
    else:
        lines.append('【今天不進場】沒有股票通過全部條件。')
        for st,label in (('wait_stabilize','已進買進區，先等止穩'),('conditional','估值符合，但今天盤前行情未通過'),('waiting','估值符合，等承接位置')):
            if names(st):lines.append(f"接近的：{'、'.join(names(st))}（{label}）")
    if names('overvalued'):lines.append(f"評價過高：{'、'.join(names('overvalued'))}")
    rest=sum(plans[i['symbol']]['status']=='no_chase' for i in valued)
    if rest:lines.append(f"其餘 {rest} 檔尚未進入買進區。")
    lines.append(SITE)
    text='\n'.join(lines)
    return ('【測試資料，非即時交易建議】\n' if radar.get('mode')!='live' else '')+text

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
