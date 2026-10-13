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
    # The P/B line names its basis: "cheap against its own one-year norm" is a weaker claim on book than
    # on earnings, and the morning message is the only place most of these are ever read.
    for today,norm,name,basis in (('pe_today','fair_pe','本益比',''),('pb_today','fair_pb','股價淨值比','；以淨值為基準，不是獲利')):
        if positive(i.get(today)) and positive(i.get(norm)):
            gap=1-i[today]/i[norm]
            return f"{name} {i[today]:g}，{'比一年常態 '+format(i[norm],'.1f')+' 便宜 '+format(gap,'.0%') if gap>0 else '高於一年常態 '+format(i[norm],'.1f')}{basis}"
    return (v or {}).get('thesis') or '估值依據請見網站'

def researched(s,inst,now):
    """Whether a deeper written analysis is currently on file. 2026-09-26 user decision: this is depth,
    not a gate -- the Python rules decide what may be bought, and research only enriches it. ETFs are
    covered by the ETF rule (NAV premium + one-year price position) and never need company research."""
    if inst.get('kind','').startswith('etf'):return True
    r=s.fact(inst['symbol'],'research_detail') or {}
    return bool(r.get('thesis') and r.get('risks') and valid_research(r,now))

def research_coverage(s,now):
    """How many recommendations currently carry a deeper written analysis. Depth is optional: the rules
    decide what may be bought, so an empty result is a note, not a blocker."""
    valid=[(stamp(r['valid_until']),i['name']) for i in s.instruments() if i['kind']=='stock'
           for r in [s.fact(i['symbol'],'research_detail') or {}] if r.get('thesis') and r.get('risks') and valid_research(r,now)]
    if not valid:return '尚無深度分析；買點仍由規則判斷。'
    first=min(valid);return f"深度分析 {len(valid)} 檔有效，最早 {first[0].astimezone(TW):%m/%d} 到期（{first[1]}）。"

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

def scan_morning(s,now,quotes=None):
    """The pre-open message: tonight's orders from swing.py. Answer first, the levels under it, nothing else.

    The levels come from last night's close. 08:55's trial price says whether the limit is still below the
    market, which is what `quotes` carries when the pre-open check has run."""
    payload=s.meta('daily_scan') or {}
    head=f"台股盤前 {now.month}/{now.day}（{WEEKDAYS[now.weekday()]}）{now:%H:%M}"
    if not payload:
        return '\n'.join([head,'','⚠️ 昨晚沒有產生掃描結果，今天不提供買點。這不是「不進場」的判斷，請勿依此下單。',SITE])
    picks=payload.get('candidates') or []
    t=payload.get('tally') or {}
    lines=[head,'']
    if not payload.get('market_open'):
        lines.append('⛔ 今天不掛新單：0050 在 120 日線之下，大盤走弱時這套規則不進場。')
    elif picks:
        lines.append(f"✅ 今天掛單 {len(picks)} 檔（限價掛在相對低點，跌到才買，最多掛 5 個交易日）")
        for n,c in enumerate(picks[:MAX_STOCKS],1):
            lines.append('')
            lines.append(f"{n}. {c['name']} {c['symbol']} ── 掛 {c['limit']:g}（收盤 {c['close']:g}，低 {c['below_close_pct']:.1f}%）")
            lines.append(f"   先看布林上緣 {c['target']:g}（+{c['target_pct']:.1f}%），碰到後續抱、自高點回落 3% 賣；停損 {c['stop']:g}")
            lines.append(f"   {c['why']}")
            q=(quotes or {}).get(c['symbol'])
            trial=positive((q or {}).get('trial_price') if (q or {}).get('is_trial') else (q or {}).get('price'))
            if trial and trial<=c['stop']:
                lines.append(f"   ⚠️ 試撮 {trial:g} 已在停損之下，今天不買")
        if len(picks)>MAX_STOCKS:lines+=['',f"另有 {len(picks)-MAX_STOCKS} 檔見網站。"]
        lines+=['',f"價位以 {payload['day']} 收盤計算。是否下單由你決定。"]
    else:
        lines.append('⛔ 今天沒有符合條件的標的，不掛新單')
        near=(payload.get('near') or [])[:1]
        if near:
            lines.append(f"最接近：{near[0]['name']} {near[0]['symbol']}－{near[0]['why']}")
    lines.append(f"掃描上市普通股 {t.get('universe',0)} 檔，可判斷 {t.get('judged',0)}，符合 {t.get('buy',0)}")
    lines.append(SITE)
    return fit(lines)

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--import-file',type=Path);p.add_argument('--sync-close',action='store_true');p.add_argument('--send',action='store_true');p.add_argument('--scan-morning',action='store_true');p.add_argument('--quotes',type=Path);p.add_argument('--out',type=Path);p.add_argument('--alert');p.add_argument('--edit-id');a=p.parse_args();now=datetime.now(TW);s=Store(a.db)
    try:
        if a.import_file:install(s,json.loads(a.import_file.read_text()),now)
        if a.sync_close:sync_close(s,now)
        if a.alert:text=a.alert
        elif a.scan_morning:
            quotes={i['symbol']:i.get('quote') for i in json.loads(a.quotes.read_text())['items']} if a.quotes else None
            text=scan_morning(s,now,quotes)
        else:text=build(s,now)
        if a.out:atomic_json(a.out,{'generated_at':now.isoformat(),'text':text})
        if a.send:
            from .cli import _bot_token,_send_with_outbox
            from .discord import channel_for,edit_message
            if a.edit_id:
                # One message per morning: replace it rather than posting a second, contradictory one.
                try:
                    edit_message(_bot_token(),a.edit_id,text,channel_id=channel_for('daily'))
                    print(json.dumps({'status':'edited','message_id':a.edit_id}));return
                except Exception as exc:
                    print(json.dumps({'status':'edit_failed','error_type':type(exc).__name__}))
            # The pre-open message is sent once a day by the 08:55 run and nothing else retries it,
            # so a transient Discord failure must be retried here rather than losing the whole day.
            r=_send_with_outbox(s,_bot_token(),text,channel=channel_for('alert' if a.alert else 'daily'),now=now,
                                max_attempts=1 if a.alert else 3)
            print(json.dumps(r))
        else:print(text)
    finally:s.close()
if __name__=='__main__':main()
