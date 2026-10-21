"""The pre-open message, system alerts, and the public closing prices the page shows. No LLM."""
import argparse,csv,io,json,math
from datetime import datetime,date
from pathlib import Path
from .domain import TW,positive,stamp
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

WEEKDAYS='一二三四五六日'
MAX_STOCKS=5
def fit(lines,limit=1900):
    """Discord takes one message: shed risk lines first, then the ETF section, never the answer."""
    text='\n'.join(lines)
    if len(text)>limit:lines=[l for l in lines if not l.strip().startswith('風險：')];text='\n'.join(lines)
    if len(text)>limit:lines=[l for l in lines if not l.strip().startswith('另有 ')];text='\n'.join(lines)
    return text[:limit]

def todays_quote(s,symbol,now):
    """This morning's quote for symbol, or None: yesterday's price must not read as today's trial."""
    q=s.quote(symbol) or {}
    at=stamp(q.get('as_of'))
    return q if at and at.astimezone(TW).date()==now.date() else None

def scan_morning(s,now):
    """The pre-open message: tonight's orders from swing.py. Answer first, the levels under it, nothing else.

    The levels come from last night's close. 08:55's trial price, synced by the 08:50 run, says whether the
    limit is still below the market."""
    from .daily_scan import annotate
    payload=annotate(s,s.meta('daily_scan')) or {}
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
            lines.append(f"{n}. {c['name']} {c['symbol']}（{c.get('industry') or '產業未知'}）── 掛 {c['limit']:g}（收盤 {c['close']:g}，低 {c['below_close_pct']:.1f}%）")
            if c.get('business'):lines.append(f"   {c['business'][:40]}")
            size=f"｜約 {c['suggested_shares']} 股 {c['suggested_amount']:,} 元" if c.get('suggested_shares') else ''
            # No reward/risk here: the target is where the trail starts, not where the trade ends, so a
            # ratio against it reads 0.8 for a rule that has averaged +2.0% a trade. The rule's own record
            # is stated once, under the list.
            lines.append(f"   先看 {c['target']:g}（+{c['target_pct']:.1f}%）／停損 {c['stop']:g}"
                         +(f"（−{c['stop_pct']:.1f}%）" if c.get('stop_pct') else '')+size)
            facts=[f"外資 {c['foreign_lots']:+,} 張",f"投信 {c['trust_lots']:+,} 張"] if 'foreign_lots' in c else []
            if c.get('revenue_yoy_pct') is not None:facts.append(f"營收年增 {c['revenue_yoy_pct']:+.0f}%")
            if c.get('pe'):facts.append(f"本益比 {c['pe']:g}")
            if facts:lines.append('   '+'｜'.join(facts))
            q=todays_quote(s,c['symbol'],now)
            trial=positive((q or {}).get('trial_price') if (q or {}).get('is_trial') else (q or {}).get('price'))
            if trial and trial<=c['stop']:
                lines.append(f"   ⚠️ 試撮 {trial:g} 已在停損之下，今天不買")
        if len(picks)>MAX_STOCKS:lines+=['',f"另有 {len(picks)-MAX_STOCKS} 檔見網站。"]
        r=payload.get('rule_record') or {}
        lines+=['',f"價位以 {payload['day']} 收盤計算；碰到目標後續抱、自高點回落 3% 賣。"
                   +(f"這套規則回測 {r['trades']} 筆：勝率 {r['win_pct']:.0f}%、平均每筆 {r['mean_pct']:+.1f}%、持有約 {r['held_days']:.0f} 天。" if r else '')
                   +"是否下單由你決定。"]
    else:
        lines.append('⛔ 今天沒有符合條件的標的，不掛新單')
        near=(payload.get('near') or [])[:1]
        if near:
            lines.append(f"最接近：{near[0]['name']} {near[0]['symbol']}－{near[0]['why']}")
    lines.append(f"掃描上市普通股 {t.get('universe',0)} 檔，可判斷 {t.get('judged',0)}，符合 {t.get('buy',0)}")
    lines.append(SITE)
    return fit(lines)

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--sync-close',action='store_true');p.add_argument('--send',action='store_true');p.add_argument('--scan-morning',action='store_true');p.add_argument('--out',type=Path);p.add_argument('--alert');p.add_argument('--edit-id');a=p.parse_args();now=datetime.now(TW);s=Store(a.db)
    try:
        if a.sync_close:sync_close(s,now)
        if a.alert:text=a.alert
        elif a.scan_morning:
            text=scan_morning(s,now)
        else:return
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
