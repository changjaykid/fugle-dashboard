"""Intraday re-check of the orders and positions the rules hold. No LLM.

During the session TWSE's free feed carries real traded prices, so this runs on mis.twse.com.tw and never
spends Fugle quota or tokens. It watches exactly two things, both set by swing.py and both visible on the
page and in the pre-open message: a resting order's limit being reached, and a held position falling
through its stop. Nothing else -- the valuation band no longer speaks to the user, and a second set of
levels arriving at lunchtime would be a second set of rules. Nothing new means no message.
"""
import argparse,json
from datetime import datetime
from pathlib import Path
from .domain import TW,positive
from .store import Store
from . import paper

LABELS={'stop_hit':'跌破停損','reached_entry':'掛單價到了'}

def watched(s):
    """{symbol: (kind, level, name)}: every resting limit and every held position's stop."""
    out={}
    scan=s.meta('daily_scan') or {}
    for o in list((s.meta('paper_runner') or {}).get('orders') or [])+list(scan.get('candidates') or []):
        out.setdefault(o['symbol'],('reached_entry',o['limit'],o['name']))
    book=paper.Book.load(s)
    for sym,p in (book.positions if book else {}).items():
        if p.get('stop'):out[sym]=('stop_hit',p['stop'],p.get('name') or sym)
    return out

def watched_symbols(s):
    return list(watched(s))

def state(s,quotes):
    events=[]
    for sym,(kind,level,name) in watched(s).items():
        price=positive((quotes.get(sym) or {}).get('price'))
        if price and price<=level:
            events.append({'kind':kind,'symbol':sym,'name':name,'price':price,'level':level})
    return events

def new_events(s,events,now):
    """At most one intraday message a day, carrying everything worth acting on at that moment."""
    seen=s.meta('intraday_seen') or {};today=str(now.date())
    if seen.get(today) or not events:return []
    s.set_meta('intraday_seen',{today:sorted({f"{e['kind']}:{e['symbol']}" for e in events})})
    return events

def text(events,now):
    lines=[f"台股盤中更新 {now:%m/%d %H:%M}",'']
    for e in sorted(events,key=lambda e:e['kind'],reverse=True):
        lines.append(f"{e['name']} {e['symbol']}｜{LABELS[e['kind']]}（現價 {e['price']:g}，{'掛單' if e['kind']=='reached_entry' else '停損'} {e['level']:g}）")
    lines.append('今天只會有這一則盤中提醒。這是事實提醒，不是新的價位；是否下單由你決定。')
    return '\n'.join(lines)

def run(s,now,send=False,fetch=None):
    from .calendar import is_trading_day
    if not is_trading_day(now.date(),s.meta('trading_calendar') or {}):return {'status':'closed'}
    symbols=watched_symbols(s)
    if not symbols:return {'status':'no_candidates'}
    if fetch is None:
        from .quotes import fetch_quotes
        by_symbol={i['symbol']:i for i in s.instruments()}
        fetch=lambda: fetch_quotes([by_symbol[x] for x in symbols if x in by_symbol])
    quotes=fetch()
    events=new_events(s,state(s,quotes),now)
    if not events:return {'status':'quiet','checked':len(quotes)}
    body=text(events,now)
    if send:
        from .cli import _bot_token,_send_with_outbox
        from .discord import channel_for
        result=_send_with_outbox(s,_bot_token(),body,channel=channel_for('alert'),now=now,max_attempts=1)
        return {'status':result.get('status'),'events':len(events)}
    return {'status':'built','events':len(events),'text':body}

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--send',action='store_true')
    a=p.parse_args();s=Store(a.db)
    try:
        out=run(s,datetime.now(TW),a.send)
        print(out.get('text') or json.dumps(out,ensure_ascii=False))
    finally:s.close()
if __name__=='__main__':main()
