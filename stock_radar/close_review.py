"""The 18:00 message: the simulated account's day under the one rule set it trades (swing-v1).

It used to lead with "今天建議 N 檔／今天沒有發出可掛價建議", graded from the valuation system's
recommendation ledger -- the method the swing rule replaced on 2026-10-13. On days the swing rule had
twenty resting orders it told the user nothing had been suggested. The account's day is the answer now,
in the words paper_runner already writes for it.
"""
import argparse,json
from datetime import datetime
from pathlib import Path
from .domain import TW
from .store import Store
from .calendar import last_completed_day


def paper_text(day, folder=None):
    """The account's own summary for `day`, or None when it has not been written for that day."""
    folder = folder or (Path.home() / 'kid-agent-data/stock-radar')
    path = folder / 'paper-latest.json'
    try:
        body = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    if body.get('day') != day or body.get('status') != 'ok' or not body.get('text'):
        return None
    return body['text']


def text(day, account):
    lines = [f'台股收盤 {day}', '']
    lines.append(account or '模擬帳戶今天的結算尚未完成；服務的狀態訊息會說明原因。')
    return '\n'.join(lines)


def run(s, now, send=False, folder=None):
    day = str(last_completed_day(now, s.meta('trading_calendar')))
    account = paper_text(day, folder)
    body = text(day, account)
    s.set_meta('close_review', {'at': now.isoformat(), 'day': day, 'account': account is not None})
    if send:
        from .cli import _bot_token, _send_with_outbox
        from .discord import channel_for
        result = _send_with_outbox(s, _bot_token(), body, channel=channel_for('review'), now=now, max_attempts=1)
        return {'status': result.get('status'), 'day': day}
    return {'status': 'built', 'day': day, 'text': body}


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);p.add_argument('--send',action='store_true')
    a=p.parse_args();s=Store(a.db)
    try:
        out=run(s,datetime.now(TW),a.send)
        print(out.get('text') or json.dumps(out,ensure_ascii=False))
    finally:s.close()
if __name__=='__main__':main()
