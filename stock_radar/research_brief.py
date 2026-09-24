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
MAX_STOCKS,MAX_ETFS,MAX_NAMES=5,3,5
def _why(v,price=None):
    """One line a person can check: how far below its own one-year norm the symbol trades."""
    i=(v or {}).get('inputs') or {}
    if (v or {}).get('method')=='forward_pe' and i.get('forecast_year') and positive(i.get('forward_eps')) and positive(i.get('fair_pe')):
        return f"{i['forecast_year']} 情境 EPS {i['forward_eps']:.2f} 元 × 參考 PE {i['fair_pe']:.2f} 倍；屬分析推估，非公司財測"
    if (v or {}).get('method')=='etf_band' and positive(i.get('median')):
        p=price or i.get('price_today');tier='最便宜 10%' if p<=i['p10'] else '最便宜 25%' if p<=i['p25'] else '低於一年中位數'
        return f"價格 {p:g} 在過去一年{tier}（中位數 {i['median']:.2f}）；價格位置，不是估值"
    for today,norm,name in (('pe_today','fair_pe','本益比'),('pb_today','fair_pb','股價淨值比')):
        if positive(i.get(today)) and positive(i.get(norm)):
            gap=1-i[today]/i[norm]
            return f"{name} {i[today]:g}，{'比一年常態 '+format(i[norm],'.1f')+' 便宜 '+format(gap,'.0%') if gap>0 else '高於一年常態 '+format(i[norm],'.1f')}"
    return (v or {}).get('thesis') or '估值依據請見網站'

def researched(s,inst,now):
    """Per the stock skill, only a symbol with valid research is recommended. ETFs follow the user-approved
    ETF rule (NAV premium + one-year price position) instead of company research."""
    if inst.get('kind','').startswith('etf'):return True
    r=s.fact(inst['symbol'],'research_detail') or {}
    return bool(r.get('thesis') and r.get('risks') and valid_research(r,now))

def research_coverage(s,now):
    """How many stocks can be recommended at all, and when the first research lapses."""
    valid=[(stamp(r['valid_until']),i['name']) for i in s.instruments() if i['kind']=='stock'
           for r in [s.fact(i['symbol'],'research_detail') or {}] if r.get('thesis') and r.get('risks') and valid_research(r,now)]
    if not valid:return '目前沒有有效的個股研究，個股不會被推薦（ETF 不受影響）。'
    first=min(valid);return f"有效個股研究 {len(valid)} 檔，最早 {first[0].astimezone(TW):%m/%d} 到期（{first[1]}）。"

def _names(xs):
    return '、'.join(xs[:MAX_NAMES])+(f" 等 {len(xs)} 檔" if len(xs)>MAX_NAMES else '')

def quotes_checked(s,now):
    """None when this morning's live trial check really ran, else the reason it cannot be trusted."""
    q=s.meta('last_quote_sync') or {};c=s.meta('morning_candidates') or {};at=stamp(q.get('at'));built=stamp(c.get('as_of'))
    if not built or now-built>timedelta(hours=30):return '昨晚的全市場掃描沒有完成，沒有候選清單'
    if not at or at.astimezone(TW).date()!=now.date():return '今天盤前報價沒有執行'
    if q.get('source')!='fugle':return f"試撮報價來源失敗（改用 {q.get('source') or '無'}，無法分辨試撮價）"
    if q.get('received',0)<max(1,int(q.get('requested',0)*.8)):return f"試撮報價只取得 {q.get('received',0)}/{q.get('requested',0)} 檔"
    return None

def fit(lines,limit=1900):
    """Discord takes one message: shed risk lines first, then the ETF section, never the answer."""
    text='\n'.join(lines)
    if len(text)>limit:lines=[l for l in lines if not l.strip().startswith('風險：')];text='\n'.join(lines)
    if len(text)>limit:lines=[l for l in lines if not l.strip().startswith('另有 ')];text='\n'.join(lines)
    return text[:limit]

def morning(s,radar,now):
    """08:50 message: one message a trading day, the answer in the first two lines, detail on the site."""
    from .discord import _revalidate_signal
    from .buy_plan import store_plan
    from .market_scan import discount
    head=f"台股盤前 {now.month}/{now.day}（{WEEKDAYS[now.weekday()]}）{now:%H:%M}"
    valued=[i for i in radar['items'] if i.get('valuation')]
    checked=[(i,_revalidate_signal(i,now)) for i in valued]
    go=sorted([(i,g) for i,g in checked if g['status'] in ('sweet','add','buy')],key=lambda x:-(discount(x[0]['valuation'],x[1].get('suggested')) or 0))
    pool=[x for x in go if not researched(s,x[0],now)];go=[x for x in go if researched(s,x[0],now)]
    picks=[x for x in go if x[0].get('kind')=='stock'][:MAX_STOCKS]+[x for x in go if x[0].get('kind','').startswith('etf')][:MAX_ETFS]
    plans={i['symbol']:store_plan(s,i,now) for i in valued}
    names=lambda st:[i['name'] for i in sorted(valued,key=lambda i:-(discount(i['valuation'],(i.get('public_close') or {}).get('close')) or 0)) if plans[i['symbol']]['status']==st and researched(s,i,now)]
    def block(n,i,g):
        r=s.fact(i['symbol'],'research_detail') or {};etf='（ETF）' if i.get('kind','').startswith('etf') else ''
        out=['',f"{n}. {i['name']} {i['symbol']}{etf} ── 掛 {g['suggested']:g}，最高 {i['valuation']['buy']:g}",f"   {_why(i['valuation'],g.get('suggested'))}"]
        if r.get('risks'):out.append(f"   風險：{r['risks'][0]}")
        return out
    lines=[head,'']
    if picks:
        lines.append(f"✅ 今天可以買 {len(picks)} 檔（限價掛單，買不到就不追）")
        for n,(i,g) in enumerate(picks,1):lines+=block(n,i,g)
        lines+=['',f"價格以 {now:%H:%M} 試撮價計算，09:00 開盤前參考；開盤後請重新確認。超過最高價不追，是否下單由你決定。"]
    elif (problem:=quotes_checked(s,now)) and radar.get('mode')=='live':
        lines+=[f"⚠️ 無法確認：{problem}","今天不提供買點。這不是「不進場」的判斷，請勿依此下單。"]
    else:
        lines.append('⛔ 今天不進場')
        near=[f"{_names(names(st))}（{label}）" for st,label in (('wait_stabilize','已進買進區，等止穩'),('conditional','估值符合，盤前行情未通過'),('waiting','估值符合，等承接位置')) if names(st)]
        lines.append('有研究支持的標的都沒有通過條件。'+('最接近：'+'；'.join(near) if near else ''))
    if pool:lines.append(f"另有 {len(pool)} 檔通過規則篩選但尚未研究，不是推薦 → 網站")
    watched=[i['name'] for i in valued if i.get('watched') and plans[i['symbol']]['status']=='overvalued']
    if watched:lines.append(f"你的關注中評價過高：{_names(watched)}")
    scan=s.meta('market_scan') or {}
    tail=[f"掃描上市 {scan['listed']} 檔，成交量達標 {scan['liquid']} 檔" if scan.get('liquid') else '',research_coverage(s,now).rstrip('。')]
    lines.append('｜'.join(x for x in tail if x))
    lines.append(SITE)
    return ('【測試資料，非即時交易建議】\n' if radar.get('mode')!='live' else '')+fit(lines)

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--import-file',type=Path);p.add_argument('--sync-close',action='store_true');p.add_argument('--send',action='store_true');p.add_argument('--morning-radar',type=Path);p.add_argument('--out',type=Path);p.add_argument('--alert');p.add_argument('--edit-id');a=p.parse_args();now=datetime.now(TW);s=Store(a.db)
    try:
        if a.import_file:install(s,json.loads(a.import_file.read_text()),now)
        if a.sync_close:sync_close(s,now)
        text=a.alert if a.alert else morning(s,json.loads(a.morning_radar.read_text()),now) if a.morning_radar else build(s,now)
        if a.out:atomic_json(a.out,{'generated_at':now.isoformat(),'text':text})
        if a.send:
            from .cli import _bot_token,_send_with_outbox
            from .discord import DISCORD_CHANNEL,edit_message
            if a.edit_id:
                # One message per morning: replace it rather than posting a second, contradictory one.
                try:
                    edit_message(_bot_token(),a.edit_id,text,channel_id=DISCORD_CHANNEL)
                    print(json.dumps({'status':'edited','message_id':a.edit_id}));return
                except Exception as exc:
                    print(json.dumps({'status':'edit_failed','error_type':type(exc).__name__}))
            r=_send_with_outbox(s,_bot_token(),text,channel=DISCORD_CHANNEL,now=now,max_attempts=1)
            print(json.dumps(r))
        else:print(text)
    finally:s.close()
if __name__=='__main__':main()
