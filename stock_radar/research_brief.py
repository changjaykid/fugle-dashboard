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
    active=sum(bool(s.active(i['symbol'])) for i in watched)
    lines=['台股研究更新｜'+now.strftime('%Y-%m-%d'),'研究觀察 '+str(len(rows))+'/'+str(len(watched))+' 檔；已套用估值 '+str(active)+' 檔。','以下為研究與待核准價位，不是今日掛單指令。']
    for i,r,c in rows:
        p=r.get('proposal');v=stamp((p or {}).get('valid_until'))
        close=f"收盤 {c['close']:g}（{c['trade_date']}）" if c.get('close') else '收盤未取得'
        if p and v and v>now:
            action=f"提案買進上限 {p['buy']:g}；"+('收盤低於／等於提案門檻，優先複核' if c.get('close') and c['close']<=p['buy'] else '等待價格或獲利條件改善')
        else:action=(r.get('valuation_gap') or '研究需更新').rstrip('。')
        lines.append(f"{i['name']} {i['symbol']}｜{close}\n{action}。風險：{r['risks'][0]}。")
    if not rows:lines.append('研究缺失或已過期，不能提供候選理由；需要更新研究。')
    lines+=['估值假設：H1 EPS×2×四個月歷史PE中位數，再保留15%安全邊際；非公司全年預測。','新版本未核准前不提供可掛價；現金流、籌碼與量價仍待補。',SITE+'#research-title']
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
    prefix='【盤前檢查 '+now.strftime('%Y-%m-%d %H:%M')+'】\n今天沒有通過全部檢查的掛價；不是全市場沒有機會。\n'
    if radar.get('mode')!='live':prefix='【測試資料，非即時交易建議】\n'+prefix
    text=prefix+build(s,now)
    if len(text)>1900:raise ValueError('Morning brief exceeds limit')
    return text

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
