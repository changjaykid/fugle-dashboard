"""Recommendation ledger: what each trading morning advised, and what the market did next. No LLM."""
import argparse,json,statistics
from datetime import date,datetime
from pathlib import Path
from .domain import TW,positive
from .store import Store,dumps

HORIZONS=(5,20,60)
ACTIONABLE=('sweet','add','buy')

def _num(v):return float(str(v).replace(',',''))
def _roc(v):
    y,m,d=map(int,v.split('/'));return date(y+1911,m,d)

def rows(s,symbol=None):
    q='SELECT payload FROM recommendations'+(' WHERE symbol=?' if symbol else '')+' ORDER BY day,symbol'
    return [json.loads(r[0]) for r in s.db.execute(q,(symbol,) if symbol else ())]

def record(s,radar,day,at):
    """Keep the final pre-open advice per watched symbol; a later snapshot of the same morning replaces an earlier one."""
    n=0
    with s.transaction():
        for i in radar.get('items',[]):
            if not (i.get('watched') or i.get('valuation')):continue
            sig=i.get('signal') or {};key=f"{day}:{i['symbol']}"
            old=s.db.execute('SELECT payload FROM recommendations WHERE id=?',(key,)).fetchone()
            if old and json.loads(old[0])['recorded_at']>at:continue
            status=sig.get('status') or 'stale';suggested=positive(sig.get('suggested')) if status in ACTIONABLE else None
            rec={'day':day,'symbol':i['symbol'],'name':i.get('name') or i['symbol'],'status':status,'suggested':suggested,'reason':sig.get('reason') or sig.get('action'),'recorded_at':at}
            s.db.execute('INSERT OR REPLACE INTO recommendations VALUES(?,?,?,?)',(key,day,i['symbol'],dumps(rec)));n+=1
    return n

def evaluate(s):
    """Fill in outcomes from stored daily bars. Results are kept once known, so the rolling 60-bar cache is enough."""
    from .market import load,SCAN_DAYS
    market={}
    for d,day in load(s,'prices',SCAN_DAYS):
        for sym,r in day.items():market.setdefault(sym,[]).append([f"{int(d[:4])-1911}/{d[5:7]}/{d[8:]}",'','','','',str(r[2]),str(r[3])])
    changed=0
    with s.transaction():
        for rec in rows(s):
            bars=market.get(rec['symbol']) or sorted(s.fact(rec['symbol'],'price_bars').get('rows',[]),key=lambda x:_roc(x[0]))
            day=date.fromisoformat(rec['day']);base=next((b for b in bars if _roc(b[0])==day),None);before=dumps(rec)
            if 'close' not in rec and base:
                rec['close']=_num(base[6]);rec['low']=_num(base[5])
                if rec['suggested']:rec['filled']=rec['low']<=rec['suggested']
            if 'close' in rec:
                entry=rec['suggested'] if rec.get('filled') else rec['close']
                later=[b for b in bars if _roc(b[0])>day]
                out=rec.setdefault('returns',{})
                for h in HORIZONS:
                    if str(h) not in out and len(later)>=h:out[str(h)]=round((_num(later[h-1][6])/entry-1)*100,2)
            if dumps(rec)!=before:
                s.db.execute('UPDATE recommendations SET payload=? WHERE id=?',(dumps(rec),f"{rec['day']}:{rec['symbol']}"));changed+=1
    return changed

def _avg(xs):return (round(statistics.mean(xs),2),len(xs)) if xs else (None,0)

def stats(s):
    recs=rows(s);act=[r for r in recs if r['status'] in ACTIONABLE];filled=[r for r in act if r.get('filled')]
    out={'days':len({r['day'] for r in recs}),'actionable':len(act),'filled':len(filled),'unfilled':len([r for r in act if r.get('filled') is False]),'horizons':{}}
    for h in map(str,HORIZONS):
        out['horizons'][h]={'filled':_avg([r['returns'][h] for r in filled if h in r.get('returns',{})]),
                            'avoid':_avg([r['returns'][h] for r in recs if r['status']=='avoid' and h in r.get('returns',{})]),
                            'baseline':_avg([r['returns'][h] for r in recs if h in r.get('returns',{})])}
    return out

def summary(s):
    st=stats(s)
    if not st['days']:return '建議追蹤：尚無紀錄。'
    lines=[f"建議追蹤（{st['days']} 個交易日）：可掛價建議 {st['actionable']} 筆，當日觸及掛價 {st['filled']} 筆、未觸及 {st['unfilled']} 筆。"]
    fmt=lambda a:'—' if a[0] is None else f"{a[0]:+.2f}%（{a[1]} 筆）"
    for h,v in st['horizons'].items():
        if v['baseline'][1]:lines.append(f"{h} 日後：成交建議 {fmt(v['filled'])}；不追 {fmt(v['avoid'])}；觀察股整體 {fmt(v['baseline'])}")
    if not any(v['baseline'][1] for v in st['horizons'].values()):lines.append('尚未滿 5 個交易日，還不能評估成效。')
    lines.append('樣本很少時結果不具統計意義；報酬未含手續費、稅與股息。')
    return '\n'.join(lines)

def backfill(s,snapshots):
    n=0
    for f in sorted(Path(snapshots).glob('*.json')):
        radar=json.loads(f.read_text());at=radar.get('generated_at')
        if not at:continue
        t=datetime.fromisoformat(at).astimezone(TW)
        if t.hour*60+t.minute>=9*60:continue
        n+=record(s,radar,str(t.date()),t.isoformat())
    return n

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);sub=p.add_subparsers(dest='cmd',required=True)
    r=sub.add_parser('record');r.add_argument('--radar',type=Path,required=True)
    sub.add_parser('evaluate');sub.add_parser('summary');sub.add_parser('stats')
    b=sub.add_parser('backfill');b.add_argument('--snapshots',type=Path,required=True)
    a=p.parse_args(argv);s=Store(a.db)
    try:
        if a.cmd=='record':
            now=datetime.now(TW);print(record(s,json.loads(a.radar.read_text()),str(now.date()),now.isoformat()),'recorded')
        elif a.cmd=='backfill':print(backfill(s,a.snapshots),'recorded;',evaluate(s),'evaluated')
        elif a.cmd=='evaluate':print(evaluate(s),'evaluated')
        elif a.cmd=='stats':print(json.dumps(stats(s),ensure_ascii=False,indent=1))
        else:print(summary(s))
    finally:s.close()

if __name__=='__main__':main()
