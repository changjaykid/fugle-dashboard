"""Old-Mac launchd worker: public filings only; never invokes OpenClaw or an LLM."""
import argparse,datetime,fcntl,json,os,pathlib,subprocess,sys
from zoneinfo import ZoneInfo
H=pathlib.Path.home(); R=H/'kid-agent-os/services/stock-radar'; W=H/'kid-agent-os/services/stock-web'; D=H/'kid-agent-data/stock-radar'; PY='/usr/local/bin/python3'
os.umask(0o077)
os.environ["PYTHONPATH"]=str(R)
os.environ["STOCK_RADAR_FUGLE_CONFIG"]=str(H/".openclaw/workspace/fugle-dashboard/engine/config.json")

def run(args,cwd=R):
    p=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=300)
    if p.returncode:
        import re
        operations={'sync-quotes','export','sync-universe','sync-calendar','sync-financials','sync-risk','backup'}
        step=next((a for a in args if a in operations),'other')
        # Preserve diagnostic categories, never stderr bodies, args, tokens or URLs.
        matches=re.findall(r'^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception)):',p.stderr or '',re.M)
        category=matches[-1] if matches else 'NonzeroExit'
        print(json.dumps({'at':datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
            'status':'command_failed','step':step,'exit_code':p.returncode,'error_type':category}),file=sys.stderr,flush=True)
        raise RuntimeError('Command failed: '+step+' ('+category+')')
    return p.stdout

def secondary(args,step):
    # Secondary steps log a failure but never block the report or the site.
    try:run(args)
    except (RuntimeError,subprocess.TimeoutExpired) as e:print(json.dumps({'status':'secondary_failed','step':step,'error_type':type(e).__name__}),file=sys.stderr,flush=True)

def track(args):
    secondary([PY,'-m','stock_radar.track','--db',str(D/'prod.db')]+args,'track '+args[0])

def alert(text):
    # A silent morning looks like "nothing to buy"; say plainly that the system failed instead.
    try:run([PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--alert',text,'--send'])
    except Exception:pass

def main():
    p=argparse.ArgumentParser();p.add_argument('task',choices=['refresh','notify','recheck','close','weekly','outlook','intraday']);p.add_argument('--manual',action='store_true');a=p.parse_args();now=datetime.datetime.now(ZoneInfo('Asia/Taipei'))
    # launchd can catch up a calendar event after wake: do not send a late morning report.
    if not a.manual:
        # close at 18:00: TWSE publishes the daily P/E file (BWIBBU_d) after 16:30, so an earlier run
        # skips most symbols. Keep this in sync with local.kid.stock.close.plist.
        # intraday runs at several points; every other task has one scheduled minute.
        windows={'refresh':[390],'notify':[530],'recheck':[535],'close':[1080],'weekly':[600],'outlook':[1200],
                 'intraday':[545,600,660,720,800]}[a.task]
        if a.task in ('weekly','outlook') and now.weekday()!={'weekly':5,'outlook':6}[a.task]:return
        if not any(t<=now.hour*60+now.minute<=t+5 for t in windows):return
    # Kill switch (no LLM, no network): `touch ~/kid-agent-data/stock-radar/PAUSED` stops every stock job; rm resumes.
    if (D/'PAUSED').exists():
        print(json.dumps({'at':now.isoformat(),'task':a.task,'status':'paused'}));return
    with (D/'service.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        cli=[PY,'-m','stock_radar.cli','--db',str(D/'prod.db')]
        if a.task=='intraday':
            # Free TWSE quotes during the session: no Fugle quota, no tokens, silent unless something changed.
            print(run([PY,'-m','stock_radar.intraday','--db',str(D/'prod.db'),'--send']).strip())
        elif a.task in ('weekly','outlook'):
            run([PY,'-m','stock_radar.weekend',a.task,'--send'])
        elif a.task in ('refresh','close'):
            sys.path.insert(0,str(R))
            from stock_radar.store import Store as _Store
            from stock_radar.calendar import is_trading_day as _trading
            _s=_Store(D/'prod.db')
            try:trading_today=bool(_s.meta('trading_calendar')) and _trading(now.date(),_s.meta('trading_calendar'))
            except Exception:trading_today=False
            finally:_s.close()
            for cmd in ['sync-universe','sync-calendar','sync-financials','sync-risk']:run(cli+[cmd])
            run([PY,str(R/'tools/initialize_watchlist.py')])
            run([PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--sync-close'])
            # Watched-only legacy structure (keeps hand-research anchors); the full-market scan covers the rest.
            secondary([PY,'-m','stock_radar.price_structure','--db',str(D/'prod.db')],'price_structure')
            # Full market: add the latest session (a few requests), then liquidity gate, structure, valuation
            # and the ranked 08:50 candidates -- all before anything is exported.
            run([PY,'-m','stock_radar.market','--db',str(D/'prod.db')])
            run([PY,'-m','stock_radar.market_scan','--db',str(D/'prod.db')])
            track(['evaluate'])
            # Once a day, after the close scan: research the symbols the scan says are worth researching,
            # so tomorrow's recommendations have a reason attached. Model access goes through the shared
            # runtime's budgeted CLI; a failure here must never block the site or the report.
            # Data refreshes every day, but reviewing and researching a session that already ended would
            # repeat yesterday's report and spend tokens for nothing: both are trading-day only.
            if a.task=='close' and trading_today:
                secondary([PY,'-m','stock_radar.research_llm','--db',str(D/'prod.db')],'research')
                secondary([PY,'-m','stock_radar.close_review','--db',str(D/'prod.db'),'--send'],'close_review')
            run(cli+['export','--out','docs/radar.json','--mode','live'])
            run([PY,'-m','stock_radar.daily_report','--db',str(D/'prod.db'),'--out','docs/research-summary.json'])
            run(cli+['backup','--out',str(D/('backup-'+now.strftime('%Y%m%d-%H%M%S')+'.db'))])
            # Retention: newest 14 database backups, 120 days of morning snapshots (weekly review and tracking read far less).
            for old in sorted(D.glob('backup-*.db'))[:-14]:old.unlink()
            for old in (D/'snapshots').glob('*.json'):
                if now.timestamp()-old.stat().st_mtime>120*86400:old.unlink()
            (W/'docs/radar.json').write_text(run([PY,'tools/public_export.py']))
            (W/'docs/research-summary.json').write_bytes((R/'docs/research-summary.json').read_bytes())
            run(['git','add','docs/radar.json','docs/research-summary.json'],W)
            dirty=run(['git','diff','--cached','--name-only'],W)
            if dirty:run(['git','commit','-m','Refresh public stock research '+now.strftime('%Y-%m-%d')],W)
            run(['git','push','origin','HEAD:main'],W)
        else:
            sys.path.insert(0,str(R))
            from stock_radar.store import Store
            from stock_radar.calendar import is_trading_day
            s=Store(D/'prod.db')
            try:
                cal=s.meta('trading_calendar')
                if not cal or not is_trading_day(now.date(),cal):return
                meta=s.meta('last_financials_sync') or {}
                at=datetime.datetime.fromisoformat(meta.get('at',''))
                if at>now or now-at>datetime.timedelta(hours=30):raise RuntimeError('Financial refresh is stale')
            finally:s.close()
            run(cli+['sync-quotes','--source','fugle','--candidates'])
            run(cli+['export','--out','docs/radar.json','--mode','live'])
            radar=json.loads((R/'docs/radar.json').read_text())
            # The first pass quotes 30 candidates over ~a minute, so the picks carry the oldest prices.
            # Re-quote just the picks so the price in the message is seconds old, then re-export.
            picks=[i['symbol'] for i in radar['items'] if (i.get('signal') or {}).get('status') in ('sweet','add','buy')][:8]
            if picks:
                secondary(cli+['sync-quotes','--source','fugle','--symbols',','.join(picks)],'requote picks')
                run(cli+['export','--out','docs/radar.json','--mode','live'])
                radar=json.loads((R/'docs/radar.json').read_text())
            snapshots=D/'snapshots';snapshots.mkdir(exist_ok=True)
            snapshot=snapshots/(now.strftime('%Y%m%d-%H%M%S')+'.json')
            with snapshot.open('xb') as f:f.write((R/'docs/radar.json').read_bytes())
            track(['record','--radar',str(snapshot)])
            state={'date':str(now.date()),'items':{i['symbol']:{k:i.get('signal',{}).get(k) for k in ('status','suggested','reason')} for i in radar['items'] if i.get('watched') or i.get('signal',{}).get('status') in ('sweet','add','buy')}}
            prev=D/'morning-state.json';sent=D/'morning-message.json'
            same=prev.exists() and json.loads(prev.read_text())==state
            # One message per trading day: 08:50 posts it (that is the phone notification), 08:55 edits
            # the same message when anything changed, so the channel never shows two versions.
            today=json.loads(sent.read_text()) if sent.exists() else {}
            today=today if today.get('date')==str(now.date()) else {}
            args=[PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--morning-radar',str(R/'docs/radar.json'),'--out',str(D/'morning-latest.json'),'--send']
            if today.get('message_id'):args+=['--edit-id',today['message_id']]
            if not (a.task=='recheck' and same and today.get('message_id')):
                out=run(args)
                for line in out.splitlines():
                    try:r=json.loads(line)
                    except ValueError:continue
                    if r.get('message_id') and r.get('status')!='edit_failed':
                        from stock_radar.store import atomic_json as _aj;_aj(sent,{'date':str(now.date()),'message_id':r['message_id']})
            from stock_radar.store import atomic_json
            atomic_json(prev,state)
        print(json.dumps({'at':now.isoformat(),'task':a.task,'status':'ok'}))
if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'status':'failed','error_type':type(e).__name__}),flush=True)
        task=sys.argv[1] if len(sys.argv)>1 else '';t=datetime.datetime.now(ZoneInfo('Asia/Taipei'));why=f"{type(e).__name__}: {str(e)[:80]}"
        what={'notify':'今天盤前流程失敗，沒有產生建議。這不是「不進場」的判斷，請勿依此下單。',
              'intraday':'盤中重新判斷失敗，本輪沒有檢查；下一輪會再試。',
              'refresh':'早上資料更新失敗，網站與估值停在上一次成功的版本；08:50 可能無法確認。',
              'close':'收盤資料更新失敗，網站停在上一次成功的版本。',
              'weekly':'週六週報產生失敗。','outlook':'週日下週觀察清單產生失敗。'}.get(task)
        if what:alert(f"台股系統｜{t:%m/%d %H:%M}\n【系統異常】{what}（{why}）\nhttps://changjaykid.github.io/fugle-dashboard/")
        sys.exit(1)
