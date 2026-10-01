"""Deterministic close review: what this morning advised, and what the session actually did. No LLM.

STOCK_AGENT.md section 9 asks the agent to grade its own day: which calls were right, which WAIT was
right, and what it missed. Everything here is arithmetic on the day's own bars, so it costs nothing and
can run on every close. A missed opportunity is as important as a bad buy, so both are counted.
"""
import argparse,json
from datetime import datetime
from pathlib import Path
from .domain import TW
from .store import Store

MISSED_MOVE=.03  # a no-chase name that still ran this much is a miss worth seeing, not noise
SHOW=3

def session(s,day):
    """{symbol: [open,high,low,close,lots]} for one trading day, from the full-market cache."""
    from .market import load
    for stamp,rows in reversed(load(s,'prices',40)):
        if stamp==day:return rows
    return {}

def review(s,day):
    from .track import rows as ledger
    bars=session(s,day)
    if not bars:return None
    picks=[r for r in ledger(s) if r['day']==day and r.get('suggested')]
    waits=[r for r in ledger(s) if r['day']==day and not r.get('suggested')]
    filled=[];unfilled=[];missed=[]
    for r in picks:
        bar=bars.get(r['symbol'])
        if not bar:continue
        entry=r['suggested'];close=bar[3]
        (filled if bar[2]<=entry else unfilled).append({**r,'close':close,'low':bar[2],'high':bar[1],
                                                        'move_pct':round((close/entry-1)*100,2)})
    for r in waits:
        bar=bars.get(r['symbol'])
        if not bar or not bar[0]:continue
        move=(bar[3]/bar[0]-1)
        if move>=MISSED_MOVE:missed.append({**r,'move_pct':round(move*100,2)})
    return {'day':day,'picks':len(picks),'filled':len(filled),'unfilled':len(unfilled),
            'filled_avg_pct':round(sum(x['move_pct'] for x in filled)/len(filled),2) if filled else None,
            'not_recommended':len(waits),'movers':len(missed),'missed':sorted(missed,key=lambda x:-x['move_pct'])[:SHOW],
            'filled_detail':sorted(filled,key=lambda x:x['move_pct'])[:SHOW],
            'unfilled_detail':sorted(unfilled,key=lambda x:-x['move_pct'])[:SHOW]}

def text(summary):
    if not summary:return None
    lines=[f"台股收盤檢討 {summary['day']}",'']
    if summary['picks']:
        avg='—' if summary['filled_avg_pct'] is None else f"{summary['filled_avg_pct']:+.2f}%"
        lines.append(f"今天建議 {summary['picks']} 檔：觸及掛價 {summary['filled']} 檔，收盤相對掛價平均 {avg}；未觸及 {summary['unfilled']} 檔。")
        for x in summary['filled_detail']:
            lines.append(f"　{x['name']} {x['symbol']}｜掛 {x['suggested']:g} → 收 {x['close']:g}（{x['move_pct']:+.2f}%）")
        for x in summary['unfilled_detail']:
            lines.append(f"　{x['name']} {x['symbol']}｜掛 {x['suggested']:g} 未成交，當日最低 {x['low']:g}")
    else:
        lines.append('今天沒有發出可掛價建議。')
    # Not a claim that each of these was judged: most simply never entered the buy zone.
    lines.append(f"未列入建議 {summary['not_recommended']} 檔，其中 {summary['movers']} 檔當天漲逾 3%。")
    if summary['missed']:
        lines.append('漲最多的：'+'、'.join(f"{x['name']} {x['move_pct']:+.1f}%" for x in summary['missed'])+'（未建議，不代表當時應該買）')
    lines.append('當日漲跌不代表這些判斷長期正確；5／20／60 日成效仍以追蹤紀錄為準。')
    return '\n'.join(lines)


def paper_section(day, folder=None):
    """The simulated account's own day, appended to the close review rather than sent separately.

    One message a day was the whole point of the 08:55 rework; the account's result is part of the same
    answer, not a second notification. Stale or missing output is skipped silently -- paper_runner runs
    as a secondary step and its failure is already reported by the service's own status line."""
    folder = folder or (Path.home() / 'kid-agent-data/stock-radar')
    path = folder / 'paper-latest.json'
    if not path.is_file():
        return []
    try:
        body = json.loads(path.read_text())
    except (ValueError, OSError):
        return []
    if body.get('day') != day or body.get('status') != 'ok' or not body.get('text'):
        return []
    return ['', body['text']]

def run(s,now,send=False):
    from .buy_plan import last_completed_day
    day=str(last_completed_day(now,s.meta('trading_calendar')))
    summary=review(s,day)
    if not summary:return {'status':'no_data','day':day}
    s.set_meta('close_review',{'at':now.isoformat(),**{k:v for k,v in summary.items() if k!='missed'},
                               'missed':[x['symbol'] for x in summary['missed']]})
    body=text(summary)
    extra=paper_section(day)
    if extra:
        candidate='\n'.join([body]+extra)
        # Discord takes one message; the review is the answer and the account is the detail, so the
        # account is what gets dropped if the two together do not fit.
        if len(candidate)<=1900:body=candidate
    if send:
        from .cli import _bot_token,_send_with_outbox
        from .discord import channel_for
        result=_send_with_outbox(s,_bot_token(),body,channel=channel_for('review'),now=now,max_attempts=1)
        return {'status':result.get('status'),'day':day}
    return {'status':'built','day':day,'text':body}

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--send',action='store_true')
    a=p.parse_args();s=Store(a.db)
    try:
        out=run(s,datetime.now(TW),a.send)
        print(out.get('text') or json.dumps(out,ensure_ascii=False))
    finally:s.close()
if __name__=='__main__':main()
