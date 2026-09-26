"""Intraday re-check: the morning conclusion is not the whole day (STOCK_AGENT.md 8). No LLM.

During the session the trial-price problem disappears -- TWSE's free feed carries real traded prices --
so this runs on mis.twse.com.tw and never spends Fugle quota or tokens. It speaks only when the day's
own facts changed: a name reaching its entry, a recommended name losing its support, or a name newly
entering the buy zone. Nothing new means no message.
"""
import argparse,json
from datetime import datetime
from pathlib import Path
from .domain import TW,positive
from .store import Store

DROP=.02  # below the researched support by this much is a real breakdown, not a tick
SHOW=4

def watched_symbols(s):
    """The morning candidate list plus anything already recommended today; both are bounded."""
    meta=s.meta('morning_candidates') or {}
    return list(dict.fromkeys(meta.get('symbols') or []))

def state(s,now,quotes):
    """Compare live prices with today's plans; return only the changes worth a message."""
    from .buy_plan import store_plan
    events=[]
    for inst in s.instruments():
        sym=inst['symbol'];quote=quotes.get(sym)
        if not quote:continue
        price=positive(quote.get('price'))
        plan=store_plan(s,inst,now)
        if not price or not plan.get('buy_max'):continue
        entry=positive(plan.get('entry'));support=positive(plan.get('support'))
        if support and price<support*(1-DROP):
            events.append({'kind':'support_lost','symbol':sym,'name':inst.get('name'),'price':price,'level':support})
        elif entry and price<=entry:
            events.append({'kind':'reached_entry','symbol':sym,'name':inst.get('name'),'price':price,'level':entry})
        elif price<=plan['buy_max'] and plan['status']=='no_chase':
            events.append({'kind':'entered_zone','symbol':sym,'name':inst.get('name'),'price':price,'level':plan['buy_max']})
    return events

def new_events(s,events,now):
    """One message per event per day: an unchanged situation must not be re-sent every hour."""
    seen=s.meta('intraday_seen') or {}
    today=str(now.date());sent=set(seen.get(today) or [])
    fresh=[e for e in events if f"{e['kind']}:{e['symbol']}" not in sent]
    if fresh:
        s.set_meta('intraday_seen',{today:sorted(sent|{f"{e['kind']}:{e['symbol']}" for e in fresh})})
    return fresh

LABELS={'support_lost':'跌破支撐，原買點失效','reached_entry':'觸及承接參考價','entered_zone':'回落進入買進區'}

def text(events,now):
    lines=[f"台股盤中更新 {now:%m/%d %H:%M}",'']
    for e in sorted(events,key=lambda e:e['kind']):
        lines.append(f"{e['name']} {e['symbol']}｜{LABELS[e['kind']]}（現價 {e['price']:g}，參考 {e['level']:g}）")
    lines.append('盤中價格會變動，這是事實提醒，不是新的掛價建議；是否下單由你決定。')
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
    events=new_events(s,state(s,now,quotes),now)
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
