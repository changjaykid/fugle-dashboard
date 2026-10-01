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

FAILED=[]
def secondary(args,step):
    # Secondary steps still never block the report or the site. What changed: the failure is carried
    # into the task's own status line. `track evaluate`, `research` and `close_review` all run here, so
    # for twelve days close.log printed a bare {"status":"ok"} that proved only the main line had run.
    try:run(args)
    except (RuntimeError,subprocess.TimeoutExpired) as e:
        FAILED.append(step)
        print(json.dumps({'status':'secondary_failed','step':step,'error_type':type(e).__name__}),file=sys.stderr,flush=True)

def track(args):
    secondary([PY,'-m','stock_radar.track','--db',str(D/'prod.db')]+args,'track '+args[0])

SITE='https://changjaykid.github.io/fugle-dashboard/'

def alert(text):
    # A silent morning looks like "nothing to buy"; say plainly that the system failed instead.
    try:run([PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--alert',text,'--send'])
    except Exception:pass

def announce_anchors(now):
    """A hand-researched price can only be renewed by a person, so its expiry has to reach one.

    Until the grace window ends the symbol has no buy price at all, and after it ends the screen replaces
    the researched price with a different number. Neither is something to discover by noticing an absence."""
    sys.path.insert(0,str(R))
    from stock_radar.store import Store,atomic_json
    s=Store(D/'prod.db')
    try:states=(s.meta('research_anchors') or {}).get('states') or {}
    finally:s.close()
    due={k:v for k,v in states.items() if v in ('grace','lapsed')}
    seen=D/'anchor-alert.json'
    told=json.loads(seen.read_text()) if seen.exists() else {}
    if not due or told.get('states')==due:return
    grace=[k for k,v in due.items() if v=='grace'];lapsed=[k for k,v in due.items() if v=='lapsed']
    lines=[f"台股系統｜{now:%m/%d %H:%M}",'【研究價位到期】']
    if grace:lines.append(f"{'、'.join(sorted(grace))} 的個股研究價位已到期，寬限期內暫時沒有買點；續期研究後才會重新出現。")
    if lapsed:lines.append(f"{'、'.join(sorted(lapsed))} 已超過寬限期，改由規則估值接手。規則價位不等於原研究價位。")
    alert('\n'.join(lines+[SITE]))
    atomic_json(seen,{'at':now.isoformat(),'states':due})

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
            # The 09:05 run is the first thing after the only pre-open send, so it is where a missing
            # morning message is still worth acting on; at 18:00 the trading day is already over.
            if now.hour*60+now.minute<550:
                posted=D/'morning-message.json'
                if not (posted.exists() and json.loads(posted.read_text()).get('date')==str(now.date())):
                    sys.path.insert(0,str(R))
                    from stock_radar.calendar import is_trading_day as _td
                    from stock_radar.store import Store as _S
                    _s=_S(D/'prod.db')
                    try:cal=_s.meta('trading_calendar')
                    finally:_s.close()
                    if cal and _td(now.date(),cal):
                        alert(f"台股系統｜{now:%m/%d %H:%M}\n【系統異常】今天沒有送出盤前建議（08:55 未執行或發送失敗）。這不是「不進場」的判斷，請勿依此下單。\nhttps://changjaykid.github.io/fugle-dashboard/")
                        from stock_radar.store import atomic_json as _aj;_aj(D/'morning-alert.json',{'date':str(now.date())})
            # Free TWSE quotes during the session: no Fugle quota, no tokens, silent unless something changed.
            print(run([PY,'-m','stock_radar.intraday','--db',str(D/'prod.db'),'--send']).strip())
        elif a.task in ('weekly','outlook'):
            run([PY,'-m','stock_radar.weekend',a.task,'--send'])
            # One model call a week, on Saturday, to argue about the rules the account traded. It can
            # only propose: applying a proposal is a human editing strategy.SETTINGS.
            if a.task=='weekly':
                secondary([PY,'-m','stock_radar.strategy_review','--db',str(D/'prod.db'),'--send'],
                          'strategy_review')
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
                # A job that never starts is silent, and silence looks exactly like "no opportunities".
                # The close run is the last one of the day, so it is where a missing morning shows up.
                posted=D/'morning-message.json';told=D/'morning-alert.json'
                ran=posted.exists() and json.loads(posted.read_text()).get('date')==str(now.date())
                ran=ran or (told.exists() and json.loads(told.read_text()).get('date')==str(now.date()))
                if not ran:alert(f"台股系統｜{now:%m/%d %H:%M}\n【系統異常】今天是交易日，但沒有送出盤前建議（08:55 未執行或發送失敗）。請檢查舊 Mac 的排程。\nhttps://changjaykid.github.io/fugle-dashboard/")
                secondary([PY,'-m','stock_radar.research_llm','--db',str(D/'prod.db')],'research')
                # The simulated account advances on closing bars, before the review that reports it.
                # It is secondary: a failure here must not cost the day's review, and the status line
                # now says when it happened.
                secondary([PY,'-m','stock_radar.paper_runner','--db',str(D/'prod.db'),
                           '--out',str(D/'paper-latest.json')],'paper_runner')
                secondary([PY,'-m','stock_radar.close_review','--db',str(D/'prod.db'),'--send'],'close_review')
                # A researched price that lapses needs a person, and nothing else would say so: the
                # screen only takes over after the grace window, and until then the symbol is invisible.
                announce_anchors(now)
                # A secondary step that fails silently makes every number downstream of it stale --
                # `track evaluate` is how the only evidence of whether this agent works gets updated.
                if FAILED:
                    seen=D/'secondary-alert.json'
                    done=json.loads(seen.read_text()) if seen.exists() else {}
                    if done.get('date')!=str(now.date()) or sorted(done.get('steps') or [])!=sorted(FAILED):
                        alert(f"台股系統｜{now:%m/%d %H:%M}\n【流程不完整】主線完成，但這些步驟失敗："
                              f"{'、'.join(FAILED)}。報表與追蹤數字可能停在上一次成功的版本。\n{SITE}")
                        from stock_radar.store import atomic_json as _aj;_aj(seen,{'date':str(now.date()),'steps':FAILED})
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
            today=json.loads(sent.read_text()) if sent.exists() else {}
            today=today if today.get('date')==str(now.date()) else {}
            args=[PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--morning-radar',str(R/'docs/radar.json'),'--out',str(D/'morning-latest.json'),'--send']
            if today.get('message_id'):args+=['--edit-id',today['message_id']]
            # One message a day, sent by the 08:55 run: its trial price is the closest to the open.
            # 08:50 only prepares data (quotes, export, snapshot, ledger) so 08:55 has everything ready.
            if a.task=='recheck' or today.get('message_id'):
                out=run(args)
                for line in out.splitlines():
                    try:r=json.loads(line)
                    except ValueError:continue
                    if r.get('message_id') and r.get('status')!='edit_failed':
                        from stock_radar.store import atomic_json as _aj;_aj(sent,{'date':str(now.date()),'message_id':r['message_id']})
            from stock_radar.store import atomic_json
            atomic_json(prev,state)
        done={'at':now.isoformat(),'task':a.task,'status':'ok' if not FAILED else 'ok_with_failures'}
        if FAILED:done['failed_steps']=FAILED
        print(json.dumps(done))
if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'status':'failed','error_type':type(e).__name__}),flush=True)
        task=sys.argv[1] if len(sys.argv)>1 else '';t=datetime.datetime.now(ZoneInfo('Asia/Taipei'));why=f"{type(e).__name__}: {str(e)[:80]}"
        what={'notify':'今天盤前流程失敗，沒有產生建議。這不是「不進場」的判斷，請勿依此下單。',
              'intraday':'盤中重新判斷失敗，本輪沒有檢查；下一輪會再試。',
              'recheck':'今天 08:55 盤前流程失敗，沒有送出當日建議。這不是「不進場」的判斷，請勿依此下單。',
              'refresh':'早上資料更新失敗，網站與估值停在上一次成功的版本；08:55 可能無法確認。',
              'close':'收盤資料更新失敗，網站停在上一次成功的版本。',
              'weekly':'週六週報產生失敗。','outlook':'週日下週觀察清單產生失敗。'}.get(task)
        if what:alert(f"台股系統｜{t:%m/%d %H:%M}\n【系統異常】{what}（{why}）\nhttps://changjaykid.github.io/fugle-dashboard/")
        sys.exit(1)
