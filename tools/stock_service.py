"""Old-Mac launchd worker: public filings only; never invokes OpenClaw or an LLM."""
import argparse,datetime,fcntl,json,os,pathlib,subprocess,sys,time
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
        # Dotted too: requests.exceptions.ConnectionError was read as nothing, so the morning of
        # 2026-10-30 left NonzeroExit three times and no way to say what had failed.
        matches=re.findall(r'^(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z][A-Za-z0-9_]*(?:Error|Exception|Timeout)):',p.stderr or '',re.M)
        category=matches[-1] if matches else 'NonzeroExit'
        print(json.dumps({'at':datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
            'status':'command_failed','step':step,'exit_code':p.returncode,'error_type':category}),file=sys.stderr,flush=True)
        raise RuntimeError('Command failed: '+step+' ('+category+')')
    return p.stdout

FAILED=[]
def resilient(args,step,attempts=3,pause=20):
    """Retry a network step before giving up on the whole evening.

    2026-10-01 18:02: the close job died on its first command, `sync-universe`, with a ConnectionReset
    from the exchange, and so never fetched that day's bars, never advanced the simulated account, never
    sent the close review and never published. The same command succeeded by hand forty minutes later,
    so one reset from the other end had cost a whole trading day. The four opening syncs are the only
    steps that depend on someone else's server being reachable at an exact minute, so they are the ones
    that get another go."""
    for attempt in range(1,attempts+1):
        try:return run(args)
        except (RuntimeError,subprocess.TimeoutExpired) as e:
            if attempt==attempts:raise
            print(json.dumps({'at':datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
                'status':'retrying','step':step,'attempt':attempt,'error_type':type(e).__name__}),
                file=sys.stderr,flush=True)
            time.sleep(pause)

def secondary(args,step,attempts=1):
    # Secondary steps never block the report or the site, but a failure is carried into the task's own
    # status line: for twelve days close.log printed a bare {"status":"ok"} that proved only the main
    # line had run.
    try:resilient(args,step,attempts)
    except (RuntimeError,subprocess.TimeoutExpired) as e:
        FAILED.append(step)
        print(json.dumps({'status':'secondary_failed','step':step,'error_type':type(e).__name__}),file=sys.stderr,flush=True)

SITE='https://changjaykid.github.io/fugle-dashboard/'

def alert(text):
    # A silent morning looks like "nothing to buy"; say plainly that the system failed instead.
    try:run([PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--alert',text,'--send'])
    except Exception as e:
        # This is the one path whose entire purpose is to make a failure visible,
        # and it swallowed its own. Every 系統異常 and 流程不完整 notice goes
        # through here, so when it fails the absence of a warning reads as the
        # absence of a problem -- the exact confusion this function exists to
        # prevent. It still must not raise: a caller is mid-report.
        print(json.dumps({'at':datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
                          'status':'alert_undelivered','error_type':type(e).__name__}),
              file=sys.stderr,flush=True)

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
    # Two jobs can be scheduled on the same minute. intraday gained a 10:00 slot
    # on 2026-09-26; the Saturday weekly review has been at 10:00 since 09-14.
    # They take this lock, and the loser used to return without a word --
    # weekly.log holds exactly two lines, 09-19 and 09-26, for that reason. The
    # only model call this agent makes went twelve days unmade and nothing said
    # so, because a skipped run and a run with nothing to do left the same trace:
    # none.
    #
    # A review of a week that has already ended is not sensitive to the minute,
    # so it waits. Intraday work is about this minute and must not be made late
    # by waiting, so it still gives up at once -- but it says so either way,
    # because "this run was skipped" is not "there was nothing to do".
    patient = a.task in ('weekly','outlook','close','refresh')
    with (D/'service.lock').open('w') as lock:
        deadline = time.monotonic() + (180 if patient else 0)
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print(json.dumps({'at':now.isoformat(),'task':a.task,'status':'skipped',
                                      'reason':'another_stock_job_held_the_lock',
                                      'waited_seconds':180 if patient else 0}),flush=True)
                    return
                time.sleep(5)
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
            # only propose: applying a proposal is a human editing swing.SETTINGS.
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
            for cmd in ['sync-universe','sync-calendar']:resilient(cli+[cmd],cmd)
            # Quarterly income, monthly revenue and yesterday's ratios: one failed fetch at 06:30 on
            # 2026-10-30 stopped the risk list, the scan and the publish behind it, for data that was
            # twelve hours old and still right. The morning's own 30-hour check catches a lasting failure.
            secondary(cli+['sync-financials'],'sync-financials',attempts=3)
            resilient(cli+['sync-risk'],'sync-risk')
            run([PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--sync-close'])
            # Full market: add the latest session (a few requests) for the scan.
            run([PY,'-m','stock_radar.market','--db',str(D/'prod.db')])
            # The scan (swing.py) decides what gets recommended and what the simulated account orders
            # (2026-10-13). It must run before paper_runner, which reads the scan it writes, and before
            # the export, which publishes it. Primary, not secondary: a day with no scan is a day the
            # account must not trade, and that should fail loudly rather than quietly do nothing.
            run([PY,'-m','stock_radar.daily_scan','--db',str(D/'prod.db')])
            # What each company does, for the page and the morning message (MOPS, cached ninety days).
            # Secondary: a missing description costs a line of text, not the day.
            secondary([PY,'-m','stock_radar.profile','--db',str(D/'prod.db')],'profile')
            # Record what it proposed and follow what earlier proposals did. Secondary: a gap in the
            # journal costs analysis, not the day's report.
            secondary([PY,'-m','stock_radar.journal','--db',str(D/'prod.db'),'record'],'journal record')
            secondary([PY,'-m','stock_radar.journal','--db',str(D/'prod.db'),'evaluate'],'journal evaluate')
            # Reviewing a session that already ended would repeat yesterday's report: trading days only.
            if a.task=='close' and trading_today:
                # A job that never starts is silent, and silence looks exactly like "no opportunities".
                # The close run is the last one of the day, so it is where a missing morning shows up.
                posted=D/'morning-message.json';told=D/'morning-alert.json'
                ran=posted.exists() and json.loads(posted.read_text()).get('date')==str(now.date())
                ran=ran or (told.exists() and json.loads(told.read_text()).get('date')==str(now.date()))
                if not ran:alert(f"台股系統｜{now:%m/%d %H:%M}\n【系統異常】今天是交易日，但沒有送出盤前建議（08:55 未執行或發送失敗）。請檢查舊 Mac 的排程。\nhttps://changjaykid.github.io/fugle-dashboard/")
                # The simulated account advances on closing bars, before the review that reports it.
                # It is secondary: a failure here must not cost the day's review, and the status line
                # now says when it happened.
                secondary([PY,'-m','stock_radar.paper_runner','--db',str(D/'prod.db'),
                           '--out',str(D/'paper-latest.json')],'paper_runner')
                secondary([PY,'-m','stock_radar.close_review','--db',str(D/'prod.db'),'--send'],'close_review')
                # The research-anchor expiry notice is gone. It reported on the valuation
                # engine, which decides nothing the account trades or the morning list
                # shows -- both are the scan -- so a lapsed research price reached the
                # channel sounding like a trading change when no trading rule had moved.
                # One set of entry and exit rules speaks to the reader; this was a second.
                # A secondary step that fails silently makes every number downstream of it stale.
                if FAILED:
                    seen=D/'secondary-alert.json'
                    done=json.loads(seen.read_text()) if seen.exists() else {}
                    if done.get('date')!=str(now.date()) or sorted(done.get('steps') or [])!=sorted(FAILED):
                        alert(f"台股系統｜{now:%m/%d %H:%M}\n【流程不完整】主線完成，但這些步驟失敗："
                              f"{'、'.join(FAILED)}。報表與追蹤數字可能停在上一次成功的版本。\n{SITE}")
                        from stock_radar.store import atomic_json as _aj;_aj(seen,{'date':str(now.date()),'steps':FAILED})
            run(cli+['backup','--out',str(D/('backup-'+now.strftime('%Y%m%d-%H%M%S')+'.db'))])
            # Retention: newest 14 database backups.
            for old in sorted(D.glob('backup-*.db'))[:-14]:old.unlink()
            # The site is GitHub Pages: anything published answers 200 to anyone with the URL, so the
            # payload is encrypted before it is committed (tools/encrypt_payload.py). The password lives in
            # a 600 file outside both repositories.
            staging=D/'radar-public.json'
            run(cli+['export','--out',str(staging),'--mode','live'])
            run([PY,'tools/encrypt_payload.py','--in',str(staging),
                 '--out',str(W/'docs/radar.enc.json'),
                 '--password-file',str(D/'site-password')])
            staging.unlink(missing_ok=True)
            run(['git','add','docs/radar.enc.json','docs/index.html',
                 'docs/radar.js','docs/radar.css'],W)
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
            sent=D/'morning-message.json'
            today=json.loads(sent.read_text()) if sent.exists() else {}
            today=today if today.get('date')==str(now.date()) else {}
            # The morning list is the scan's orders (swing.py, 2026-10-13): limits at the lower band.
            args=[PY,'-m','stock_radar.research_brief','--db',str(D/'prod.db'),'--scan-morning',
                  '--out',str(D/'morning-latest.json'),'--send']
            if today.get('message_id'):args+=['--edit-id',today['message_id']]
            # One message a day, sent by the 08:55 run: its trial price is the closest to the open.
            # 08:50 only fetches the quotes so 08:55 has everything ready.
            if a.task=='recheck' or today.get('message_id'):
                out=run(args)
                for line in out.splitlines():
                    try:r=json.loads(line)
                    except ValueError:continue
                    if r.get('message_id') and r.get('status')!='edit_failed':
                        from stock_radar.store import atomic_json as _aj;_aj(sent,{'date':str(now.date()),'message_id':r['message_id']})
        done={'at':now.isoformat(),'task':a.task,'status':'ok' if not FAILED else 'ok_with_failures'}
        if FAILED:done['failed_steps']=FAILED
        print(json.dumps(done))
if __name__=='__main__':
    try:main()
    except Exception as e:
        task=sys.argv[1] if len(sys.argv)>1 else '';t=datetime.datetime.now(ZoneInfo('Asia/Taipei'));why=f"{type(e).__name__}: {str(e)[:80]}"
        # Same fields as a success line, so the last line of the log says which run failed and when.
        print(json.dumps({'at':t.isoformat(),'task':task,'status':'failed','error_type':type(e).__name__}),flush=True)
        what={'notify':'今天盤前流程失敗，沒有產生建議。這不是「不進場」的判斷，請勿依此下單。',
              'intraday':'盤中重新判斷失敗，本輪沒有檢查；下一輪會再試。',
              'recheck':'今天 08:55 盤前流程失敗，沒有送出當日建議。這不是「不進場」的判斷，請勿依此下單。',
              'refresh':'早上資料更新失敗，網站與估值停在上一次成功的版本；08:55 可能無法確認。',
              'close':'收盤資料更新失敗，網站停在上一次成功的版本。',
              'weekly':'週六週報產生失敗。','outlook':'週日下週觀察清單產生失敗。'}.get(task)
        if what:alert(f"台股系統｜{t:%m/%d %H:%M}\n【系統異常】{what}（{why}）\nhttps://changjaykid.github.io/fugle-dashboard/")
        sys.exit(1)
