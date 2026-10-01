"""Full-market daily data from TWSE date-indexed reports: one request per trading day covers
every listed security, so the whole market costs the same as one stock. No LLM.

prices: MI_INDEX 每日收盤行情  -> {symbol: [open, high, low, close, lots]}
ratios: BWIBBU_d 本益比/淨值比 -> {symbol: [pe, pb, yield]}  (pe/pb None when not meaningful)
flows:  T86 三大法人買賣超      -> {symbol: [foreign_lots, trust_lots]}
"""
import argparse,json,time
from datetime import date,datetime,timedelta
from pathlib import Path
from .domain import TW
from .store import Store,dumps

URLS={'prices':'https://www.twse.com.tw/exchangeReport/MI_INDEX','ratios':'https://www.twse.com.tw/exchangeReport/BWIBBU_d','flows':'https://www.twse.com.tw/fund/T86'}
PARAMS={'prices':{'type':'ALLBUT0999'},'ratios':{'selectType':'ALL'},'flows':{'selectType':'ALLBUT0999'}}
# How many trading days of each dataset to retain. prices and ratios hold years rather than one year
# because the entry-rule research of 2026-10-01 could not tell a signal from a single market regime:
# 250 days covered 2026-03 to 09, in which 0050 returned 47% and the median liquid stock 13%. Momentum
# wins in a trend and value loses in one, so a factor measured only there has been measured once.
# sync() deletes everything past these counts, so lowering them throws history away.
# What this does NOT change: production still reads only the recent window it needs (SCAN_DAYS), so the
# nightly scan does not get slower as the archive grows.
# flows joins prices and ratios at full depth: Strategy v1 scores institutional flow at 15 of 100
# points, and ten days cannot be backtested against anything.
KEEP={'prices':2200,'ratios':2200,'flows':2200}
# The window the live pipeline reads. A year of ratios is what valuation_engine's median needs, plus
# slack for missed sessions; research loads the whole archive instead by passing no limit.
SCAN_DAYS=270
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; StockRadar/1.0)'}
LIQUIDITY_LOTS=1000;LIQUIDITY_DAYS=20;PAUSE=2.5;LOOKBACK_DAYS=420

def _n(v):
    try:return float(str(v).replace(',',''))
    except (TypeError,ValueError):return None
def _pos(v):
    n=_n(v);return n if n and n>0 else None

def parse(kind,body,d):
    """Returns rows for day d, None when TWSE reports no data for that date, or raises on a mismatched response."""
    if body.get('stat')!='OK':return None
    if body.get('date')!=d.strftime('%Y%m%d'):raise ValueError(f'{kind} {d} 日期不符')
    if kind=='prices':
        t=next((t for t in body.get('tables') or [] if '每日收盤行情' in (t.get('title') or '')),None)
        if not t or not t.get('data'):return None
        out={}
        for r in t['data']:
            close=_pos(r[8])
            if close:out[r[0].strip()]=[_pos(r[5]),_pos(r[6]),_pos(r[7]),close,round((_n(r[2]) or 0)/1000,3)]
        return out
    rows=body.get('data') or []
    if not rows:return None
    if kind=='ratios':return {r[0].strip():[_pos(r[5]),_pos(r[6]),_n(r[3])] for r in rows}
    return {r[0].strip():[round((_n(r[4]) or 0)/1000,3),round((_n(r[10]) or 0)/1000,3)] for r in rows}

def stored(s,kind):
    return {r[0] for r in s.db.execute('SELECT day FROM market_days WHERE kind=?',(kind,))}

def _trading(d,schedule):
    """True/False from the cached calendar; None for years it does not cover (the fetch decides)."""
    from .calendar import is_trading_day
    if d.weekday()>=5:return False
    try:return is_trading_day(d,schedule)
    except ValueError:return None

def sync(s,now,http=None,pause=PAUSE,keep=KEEP):
    """Fetch every missing trading day up to the last completed session, newest first; returns failures."""
    import requests
    from .buy_plan import last_completed_day
    http=http or requests;schedule=s.meta('trading_calendar') or {};end=last_completed_day(now,schedule);failed=[]
    closed=stored(s,'closed')
    for kind,n in keep.items():
        have=stored(s,kind);d=end;count=0
        while count<n and (end-d).days<=LOOKBACK_DAYS:
            ds=str(d);open_=_trading(d,schedule)
            if open_ is False or ds in closed:d-=timedelta(days=1);continue
            if ds in have:count+=1;d-=timedelta(days=1);continue
            try:
                body=http.get(URLS[kind],params={'response':'json','date':d.strftime('%Y%m%d'),**PARAMS[kind]},headers=HEADERS,timeout=30).json()
                rows=parse(kind,body,d);time.sleep(pause)
                if rows:
                    with s.transaction():s.db.execute('INSERT OR REPLACE INTO market_days VALUES(?,?,?)',(ds,kind,dumps(rows)))
                    count+=1
                elif open_ is None and d<now.date():
                    # Only a past date the calendar cannot vouch for is recorded as closed; a missing
                    # report for a known trading day (or today) is retried next run instead.
                    with s.transaction():s.db.execute('INSERT OR REPLACE INTO market_days VALUES(?,?,?)',(ds,'closed','{}'))
                    closed.add(ds)
                else:failed.append(f'{kind}:{ds}')
            except Exception as e:failed.append(f'{kind}:{ds}:{type(e).__name__}');time.sleep(pause)
            d-=timedelta(days=1)
        days=sorted(stored(s,kind),reverse=True)[n:]
        if days:
            with s.transaction():s.db.executemany('DELETE FROM market_days WHERE kind=? AND day=?',[(kind,x) for x in days])
    return failed

def load(s,kind,n=None,since=None):
    """[(day, {symbol: row})] oldest first: the last n stored trading days, or everything from `since`.

    Pass one of them. The archive now holds years rather than one year, and a daily job that loads all
    of it parses tens of megabytes of JSON for the sake of the last twenty days -- so every caller in the
    live pipeline bounds what it asks for, and only research loads the lot."""
    where='SELECT day,payload FROM market_days WHERE kind=?';args=[kind]
    if since:where+=' AND day>=?';args.append(since)
    where+=' ORDER BY day DESC'
    if n:where+=' LIMIT ?';args.append(n)
    rows=s.db.execute(where,tuple(args)).fetchall()
    return [(r[0],json.loads(r[1])) for r in reversed(rows)]

def liquidity(prices,days=LIQUIDITY_DAYS):
    """Average daily lots over the last `days` sessions; a session without trades counts as zero."""
    recent=prices[-days:];out={}
    for _,rows in recent:
        for sym,r in rows.items():out[sym]=out.get(sym,0)+r[4]
    return {sym:round(v/len(recent),1) for sym,v in out.items()} if recent else {}

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);a=p.parse_args()
    s=Store(a.db)
    try:
        failed=sync(s,datetime.now(TW))
        print({k:len(stored(s,k)) for k in KEEP},'failed:',','.join(failed) or 'none')
    finally:s.close()
if __name__=='__main__':main()
