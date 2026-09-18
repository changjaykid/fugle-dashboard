"""Old-Mac launchd worker: public filings only; never invokes OpenClaw or an LLM."""
import argparse,datetime,fcntl,json,os,pathlib,subprocess,sys
from zoneinfo import ZoneInfo
H=pathlib.Path.home(); R=H/'kid-agent-os/services/stock-radar'; W=H/'kid-agent-os/services/stock-web'; D=H/'kid-agent-data/stock-radar'; PY='/usr/local/bin/python3'
os.umask(0o077)
os.environ["PYTHONPATH"]=str(R)
os.environ["STOCK_RADAR_FUGLE_CONFIG"]=str(H/".openclaw/workspace/fugle-dashboard/engine/config.json")

def run(args,cwd=R):
    p=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=300)
    if p.returncode:raise RuntimeError('Command failed: '+str(args[:3]))
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
    p=argparse.ArgumentParser();p.add_argument('task',choices=['refresh','notify','recheck','close','weekly','outlook']);p.add_argument('--manual',action='store_true');a=p.parse_args();now=datetime.datetime.now(ZoneInfo('Asia/Taipei'))
    # launchd can catch up a calendar event after wake: do not send a late morning report.
    if not a.manual:
        target={'refresh':390,'notify':530,'recheck':535,'close':990,'weekly':600,'outlook':1200}[a.task]
        if a.task in ('weekly','outlook') and now.weekday()!={'weekly':5,'outlook':6}[a.task]:return
        if not target<=now.hour*60+now.minute<=target+5:return
    # Kill switch (no LLM, no network): `touch ~/kid-agent-data/stock-radar/PAUSED` stops every stock job; rm resumes.
    if (D/'PAUSED').exists():
        print(json.dumps({'at':now.isoformat(),'task':a.task,'status':'paused'}));return
    with (D/'service.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        cli=[PY,'-m','stock_radar.cli','--db',str(D/'prod.db')]
        if a.task in ('weekly','outlook'):
            run([PY,'-m','stock_radar.weekend',a.task,'--send'])
        elif a.task in ('refresh','close'):
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
            snapshots=D/'snapshots';snapshots.mkdir(exist_ok=True)
            snapshot=snapshots/(now.strftime('%Y%m%d-%H%M%S')+'.json')
            with snapshot.open('xb') as f:f.write((R/'docs/radar.json').read_bytes())
            radar=json.loads((R/'docs/radar.json').read_text())
            track(['record','--radar',str(snapshot)])
            state={'date':str(now.date()),'items':{i['symbol']:{k:i.get('signal',{}).get(k) for k in ('status','suggested','reason')} for i in radar['items'] if i.get('watched') or i.get('signal',{}).get('status') in ('sweet','add','buy')}}
            prev=D/'morning-state.json'
            same=prev.exists() and json.loads(prev.read_text())==state
            if a.task!='recheck' or not same:
                run([PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--morning-radar',str(R/'docs/radar.json'),'--out',str(D/'morning-latest.json'),'--send'])
            from stock_radar.store import atomic_json
            atomic_json(prev,state)
        print(json.dumps({'at':now.isoformat(),'task':a.task,'status':'ok'}))
if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'status':'failed','error_type':type(e).__name__}),flush=True)
        task=sys.argv[1] if len(sys.argv)>1 else '';t=datetime.datetime.now(ZoneInfo('Asia/Taipei'));why=f"{type(e).__name__}: {str(e)[:80]}"
        what={'notify':'今天盤前流程失敗，沒有產生建議。這不是「不進場」的判斷，請勿依此下單。',
              'refresh':'早上資料更新失敗，網站與估值停在上一次成功的版本；08:50 可能無法確認。',
              'close':'收盤資料更新失敗，網站停在上一次成功的版本。',
              'weekly':'週六週報產生失敗。','outlook':'週日下週觀察清單產生失敗。'}.get(task)
        if what:alert(f"台股系統｜{t:%m/%d %H:%M}\n【系統異常】{what}（{why}）\nhttps://changjaykid.github.io/fugle-dashboard/")
        sys.exit(1)
