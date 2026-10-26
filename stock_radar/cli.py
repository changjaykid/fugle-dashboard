#!/usr/bin/env python3
"""Stock Radar CLI: the data syncs the scan runs on, the page export, and backups.

All commands operate on the SQLite Store. The scan (daily_scan / swing.py) decides what is ordered;
this module only fetches what it reads and publishes what it decided.

Usage:
  python3 -m stock_radar.cli sync-universe [--db PATH]
  python3 -m stock_radar.cli sync-quotes [--db PATH] [--source mis|fugle] [--candidates|--symbols A,B]
  python3 -m stock_radar.cli sync-financials [--db PATH]
  python3 -m stock_radar.cli sync-calendar [--db PATH]
  python3 -m stock_radar.cli sync-risk [--db PATH]
  python3 -m stock_radar.cli export --out PATH [--db PATH] [--mode live|simulation]
  python3 -m stock_radar.cli backup --out PATH [--db PATH]
  python3 -m stock_radar.cli restore --backup PATH --target PATH [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time as _time
from datetime import datetime
from pathlib import Path

from .domain import TW
from . import strategy, swing
from .store import Store, atomic_json, normalize_iso, dumps
from .universe import build_universe, equity_universe
from .quotes import fetch_quotes
from .fugle import fetch_quotes as fetch_fugle_quotes, FugleClient
from .financials import fetch_quarterly_income_general, fetch_monthly_revenue, fetch_pe_yield_pb
from .calendar import fetch_holiday_schedule
from .risk import build_risk_facts
from .runtime import single_flight_lock, LockBusyError
from .discord import send_message
from .export import build

# DEMO_DB: the default SQLite path used when --db is omitted. This lives
# INSIDE the git worktree (.gitignore'd, never committed) and exists
# purely for local development/manual testing convenience -- it is NOT,
# and must never become, the production runtime database. A git worktree
# can be deleted/recreated/pruned at any time (it is a disposable checkout
# of a branch, not a durable data directory), so storing real production
# state here would risk silent data loss on routine git
# operations, and there'd be no way to tell a real production run from a
# local dry-run just by looking at the file. cmd_export enforces this: a
# --mode live export REQUIRES an explicit --db pointing OUTSIDE the repo
# tree (see PROD_DB_RECOMMENDED / _reject_demo_db_for_live below), so a
# forgotten --db flag can never let live output silently land in the demo
# file (or, worse, a live export silently READ stale demo-mode state).
DEMO_DB = Path(__file__).parent.parent / 'stock_radar.db'
DEFAULT_DB = DEMO_DB  # kept as the argparse default so existing dev/test invocations are unaffected
# Fugle publishes no free-tier quota, so real-time quotes are never fetched for the full market (2,000+
# symbols, two calls each): only for what the rule holds (--candidates), unless
# --allow-full-market-fugle is passed. The full market's closes come from public exchange data.
# The cap is what the rule can hold at once: every position plus every resting order (swing places up
# to `picks` orders a session, each resting `order_days` sessions). The 30 it replaced was a guess at an
# undocumented free-tier quota, and on 2026-10-20 the 08:50 sync refused 32 candidates -- 26 orders and
# 6 holdings -- so no pre-open message went out. The quota is protected by pace rather than count:
# FugleClient spaces requests 1.1s apart (a ticker and a quote call per symbol, about 54 a minute), so
# the full 70 is about two and a half minutes per sync.
FUGLE_CANDIDATE_CAP = (strategy.SETTINGS['max_positions']
                       + swing.SETTINGS['picks'] * swing.SETTINGS['order_days'])
FUGLE_LOCK_PATH = Path(__file__).parent.parent / '_state' / 'fugle_sync.lock'
# Recommended production runtime path: outside any git worktree, inside
# the durable OpenClaw workspace root (survives worktree add/remove and is
# covered by the workspace's own backup skill, unlike a path inside a
# disposable branch checkout).
PROD_DB_RECOMMENDED = Path.home() / '.openclaw' / 'workspace' / '_state' / 'stock_radar' / 'prod.db'


def cmd_sync_universe(args):
    store = Store(args.db)
    try:
        items = build_universe()
        store.upsert_instruments(items)
        store.set_meta('last_universe_sync', {'at': datetime.now(TW).isoformat(), 'count': len(items)})
        eq = equity_universe(items)
        print(f'universe synced: {len(items)} total, {len(eq)} equity-eligible (stock+etf_equity)')
    finally:
        store.close()


def _fugle_api_key(explicit_path):
    """Read a Fugle API key from an explicit local file (never from
    Discord, never printed) if --fugle-key-file is passed, else fall back
    to fugle-dashboard/engine/config.json's existing api_key field (the
    same file engine.py already trusts). Returns None if neither is
    present/parseable -- caller must then use the mis.twse.com.tw fallback,
    not crash."""
    if explicit_path:
        text = Path(explicit_path).read_text().strip()
        return text or None
    import os
    cfg_path = Path(os.environ.get('STOCK_RADAR_FUGLE_CONFIG', str(Path(__file__).parent.parent.parent / 'fugle-dashboard' / 'engine' / 'config.json')))
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get('api_key') or None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def cmd_sync_quotes(args):
    store = Store(args.db)
    try:
        instruments = [i for i in store.instruments() if i['kind'] in ('stock', 'etf_equity', 'etf_other')]
        if args.symbols:
            wanted={x.strip() for x in args.symbols.split(',') if x.strip()}
            instruments=[i for i in instruments if i['symbol'] in wanted]
        if args.candidates:
            # What the rules are acting on: tonight's orders and what the account holds (intraday.watched),
            # so the pre-open message can check each limit and stop against the trial price.
            from .intraday import watched_symbols
            wanted = watched_symbols(store)
            by_symbol = {i['symbol']: i for i in instruments}
            instruments = [by_symbol[s] for s in wanted if s in by_symbol]
        if not instruments:
            print('no instruments to quote (run sync-universe first)')
            return
        quotes = None
        source_used = None
        fugle_error = None
        if args.source == 'fugle':
            key = _fugle_api_key(args.fugle_key_file)
            if not key:
                raise SystemExit('--source fugle requested but no API key found (pass --fugle-key-file or set fugle-dashboard/engine/config.json api_key)')
            if len(instruments) > FUGLE_CANDIDATE_CAP and not args.allow_full_market_fugle:
                # See FUGLE_CANDIDATE_CAP's module-level comment: Fugle's
                # free-tier quota is undocumented, so real-time Fugle usage
                # stays scoped to what the rule holds (--candidates)
                # unless the operator explicitly overrides. This must NOT be
                # silently downgraded to a partial fetch -- fail loudly so a
                # cron job misconfiguration is visible in its run log, not
                # discovered later as "why did we only get some quotes".
                raise SystemExit(
                    f'--source fugle requested for {len(instruments)} instruments, above the '
                    f'{FUGLE_CANDIDATE_CAP}-symbol candidate cap (the most positions plus resting '
                    f'orders the rule can hold; see FUGLE_CANDIDATE_CAP comment in cli.py). Use '
                    f'--candidates to scope to what the rule holds, or pass '
                    f'--allow-full-market-fugle if you have confirmed your plan can handle this.'
                )
            try:
                # single_flight_lock: non-blocking -- if another sync-quotes
                # --source fugle process is already running (e.g. an
                # overlapping cron tick), this run skips cleanly rather than
                # queuing up a second concurrent burst against the same
                # rate-limited quota. Pacing itself (1.1s between requests)
                # is now enforced INSIDE FugleClient (see fugle.py v2's
                # FugleClient._get), not by a session wrapper here -- the
                # lock still exists at this layer because it is a
                # cross-process guard (two separate `python3 -m
                # stock_radar.cli` invocations), which a per-client-instance
                # pacing lock cannot provide on its own.
                with single_flight_lock(FUGLE_LOCK_PATH):
                    client = FugleClient(key)
                    # fetch_fugle_quotes' own max_symbols enforces the cap
                    # BEFORE any network call (fail loud, not a silent
                    # partial truncation). --allow-full-market-fugle is a
                    # deliberate, explicit per-call override of that limit
                    # (not a way to make Fugle usage look unbounded by
                    # default) -- it raises the ceiling only for this one
                    # invocation, to exactly len(instruments), never higher.
                    cap = len(instruments) if args.allow_full_market_fugle else FUGLE_CANDIDATE_CAP
                    try:
                        quotes = fetch_fugle_quotes(instruments, key, client=client, max_symbols=cap)
                    finally:
                        client.close()
                source_used = 'fugle'
                print(f'quotes source: Fugle marketdata v1.0 ({len(quotes)} symbols)')
            except LockBusyError as exc:
                fugle_error = repr(exc)
                print(f'Fugle sync already in progress elsewhere, skipping this tick ({exc}); falling back to mis.twse.com.tw')
            except Exception as exc:
                # Includes FugleError (network/HTTP/schema failures) and
                # any other unexpected error. Never silently swallowed as
                # success -- fugle_error is recorded into last_quote_sync
                # below, and the fallback to mis.twse is itself printed
                # loudly, not disguised as a Fugle success.
                fugle_error = repr(exc)
                print(f'Fugle source failed ({fugle_error}); falling back to mis.twse.com.tw')
        if quotes is None:
            quotes = fetch_quotes(instruments)
            source_used = 'mis'
            print(f'quotes source: mis.twse.com.tw ({len(quotes)} symbols)')
        n = 0
        rejected = {}
        for inst in instruments:
            symbol = inst['symbol']
            q = quotes.get(symbol)
            reason = None
            if not isinstance(q, dict) or q.get('symbol') != symbol:
                reason = 'QUOTE_MISSING_OR_MISMATCH'
            else:
                try:
                    normalize_iso(q.get('as_of'))
                    if q.get('book_as_of'):
                        normalize_iso(q['book_as_of'])
                except (TypeError, ValueError, OverflowError):
                    reason = 'SOURCE_TIME_INVALID'
                if reason is None:
                    try:dumps(q)
                    except (TypeError, ValueError, OverflowError):reason = 'QUOTE_PAYLOAD_INVALID'
            if reason:
                rejected[symbol] = reason
                store.set_fact(symbol, 'quote_health', {'status':'blocked','reason':reason,
                    'checked_at':datetime.now(TW).isoformat()})
                print(json.dumps({'status':'quote_rejected','symbol':symbol,'reason':reason},ensure_ascii=False))
                continue
            # Storage errors are not swallowed as bad quotes; they must fail the job.
            store.observe(q)
            n += 1
        # The real outcome -- which source served, and any Fugle failure -- for the run log and the audit.
        store.set_meta('last_quote_sync', {
            'at': datetime.now(TW).isoformat(), 'requested': len(instruments), 'received': n,
            'source': source_used, 'fugle_attempted': args.source == 'fugle',
            'fugle_error': fugle_error, 'rejected': rejected,
        })
        print(f'quotes synced: {n}/{len(instruments)}')
        if n < len(instruments):
            print(f'WARNING: {len(instruments)-n} instruments did not return a quote (partial coverage, not silently treated as success)')
    finally:
        store.close()


def cmd_sync_calendar(args):
    store = Store(args.db)
    try:
        # TWSE's API answers for one year only. Replacing the cache with it dropped the year still being
        # traded the day the API moved on, and every remaining day of that year then failed closed as
        # 'no data for this ROC year'. The fetched years replace their own dates; the others are kept.
        fetched = fetch_holiday_schedule()
        kept = store.meta('trading_calendar') or {'closed_dates': {}, 'roc_years': []}
        years = set(fetched['roc_years'])
        closed = {day: name for day, name in kept['closed_dates'].items() if int(day[:4]) - 1911 not in years}
        closed.update(fetched['closed_dates'])
        schedule = {'closed_dates': dict(sorted(closed.items())),
                    'roc_years': sorted(years | set(kept['roc_years']))}
        store.set_meta('trading_calendar', schedule)
        print(f"calendar synced: ROC years {schedule['roc_years']}, "
              f"{len(schedule['closed_dates'])} holiday dates cached")
    finally:
        store.close()


def cmd_sync_risk(args):
    store = Store(args.db)
    try:
        instruments = [i for i in store.instruments() if i['kind'] in ('stock', 'etf_equity', 'etf_other')]
        if not instruments:
            print('no instruments (run sync-universe first)')
            return
        facts = build_risk_facts(instruments)
        n_cleared = n_events = n_not_cleared = 0
        for symbol, fact in facts.items():
            store.set_fact(symbol, 'risk', fact)
            if fact['cleared']:
                n_cleared += 1
                if fact['events']:
                    n_events += 1
            else:
                n_not_cleared += 1
        store.set_meta('last_risk_sync', {'at': datetime.now(TW).isoformat(), 'count': len(facts)})
        print(f'risk checked: {len(facts)} instruments, {n_cleared} cleared ({n_events} with events), {n_not_cleared} not checked (OTC gap)')
    finally:
        store.close()


def cmd_sync_financials(args):
    store = Store(args.db)
    try:
        income = fetch_quarterly_income_general()
        revenue = fetch_monthly_revenue()
        pe = fetch_pe_yield_pb()
        n = 0
        for symbol in set(income) | set(revenue) | set(pe):
            payload = {
                'income': income.get(symbol), 'revenue': revenue.get(symbol),
                'valuation_ratios': pe.get(symbol), 'fetched_at': datetime.now(TW).isoformat(),
            }
            store.set_fact(symbol, 'financials', payload)
            n += 1
        store.set_meta('last_financials_sync', {'at': datetime.now(TW).isoformat(), 'count': n})
        print(f'financials synced for {n} TSE symbols (income={len(income)}, revenue={len(revenue)}, pe/pb/yield={len(pe)})')
        print('NOTE: OTC financials not covered by these endpoints (documented gap in financials.py)')
    finally:
        store.close()


def _reject_demo_db_for_live(db_path: Path, mode: str) -> None:
    """A --mode live export must never run against DEMO_DB (the git-
    worktree-local dev/test SQLite file, .gitignore'd and disposable).
    This is a hard SystemExit, not a warning: production data must live
    somewhere durable and independent of the worktree's lifecycle (see
    DEMO_DB's docstring comment above), and a forgotten/default --db in a
    live cron invocation is exactly the kind of silent-defaults bug this
    project has repeatedly had to fix after the fact (Fugle-401 health,
    ETF misclassification, --assume-market-open in live mode). Comparing
    resolved absolute paths so a relative alias or symlink to the same
    file cannot slip through."""
    if mode != 'live':
        return
    if Path(db_path).resolve() == DEMO_DB.resolve():
        raise SystemExit(
            f'--mode live must not use the demo/dev database ({DEMO_DB}). '
            f'Pass an explicit --db pointing to a durable path outside this git worktree '
            f'(recommended: {PROD_DB_RECOMMENDED}).'
        )


def cmd_export(args):
    """What the page shows: the scan, the account and the health of the data they were built from."""
    _reject_demo_db_for_live(args.db, args.mode)
    store = Store(args.db)
    try:
        out = build(store, datetime.now(TW), mode=args.mode)
        atomic_json(args.out, out)
        print(f"exported to {args.out} (mode={args.mode}, scan={'yes' if out['scan'] else 'none'}, "
              f"account={'yes' if out['paper'] else 'none'})")
    finally:
        store.close()


def cmd_backup(args):
    store = Store(args.db)
    try:
        path = store.backup(args.out)
        print(f'backup written: {path}')
    finally:
        store.close()


def cmd_restore(args):
    """Deliberately does NOT open a Store on args.db first -- Store.restore
    is a staticmethod precisely so a corrupt/unreadable target DB can never
    block a restore attempt (that would defeat the whole point of having a
    restore path). --force is required to overwrite a non-empty existing
    target DB; this mirrors the destructive-operation caution used
    elsewhere in this CLI (e.g. --allow-full-market-fugle)."""
    path = Store.restore(args.backup, args.target, force=args.force)
    print(f'restored: {path}')


def _bot_token():
    """Read the Discord bot token from OpenClaw's own config at call time --
    never hardcoded, never logged. Kept as a thin local helper (not a
    module-level import) so stock_radar stays importable/testable without
    OpenClaw installed; only Discord-posting commands need this."""
    import json as _json
    cfg = _json.loads(Path.home().joinpath('.openclaw', 'openclaw.json').read_text())
    return cfg['channels']['discord']['token']


def _content_hash(text: str) -> str:
    """Dedup key for the outbox: same exact text sent to the same channel
    on the same day counts as "already delivered", so a duplicate cron
    tick (overlapping schedules, a restart-recovery re-run, a manual
    re-trigger) cannot double-post the identical message."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _send_with_outbox(store, token, text, *, channel, now, max_attempts=3,
                      backoff_seconds=2, dry_run=False, sleep=_time.sleep):
    """Single chunk send with outbox dedup + limited retry. Every attempt
    (success or failure) is recorded via Store.record_delivery so a retry
    budget is enforceable across SEPARATE process invocations too (see
    Store.recent_delivery_failures), not just within this one call.

    Dedup window is "since local midnight of `now`" -- the same daily
    summary text legitimately recurs on different days (e.g. "目前無符合且
    資料有效的可行動標的。" on two quiet days), so dedup must not span
    days or it would permanently suppress a legitimately-repeating message.
    """
    chash = _content_hash(f'{channel}:{text}')
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    existing = store.find_sent_delivery(chash, since=since)
    if existing:
        # The message is already in the channel, so callers that track "did today's message go out"
        # (tools/stock_service.py's morning heartbeat) need its id, not just the fact that we skipped.
        return {'status': 'skipped_duplicate', 'delivery_id': existing['id'],
                'message_id': existing['payload'].get('message_id')}
    if dry_run:
        return {'status': 'dry_run'}
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = send_message(token, text, channel_id=channel)
            store.record_delivery(chash, 'sent',
                                  {'text_len': len(text), 'channel': channel,
                                   'attempt': attempt, 'message_id': result.get('id')}, now=now)
            return {'status': 'sent', 'message_id': result.get('id'), 'attempts': attempt}
        except Exception as exc:
            last_exc = exc
            store.record_delivery(chash, 'failed',
                                  {'text_len': len(text), 'channel': channel,
                                   'attempt': attempt, 'error': repr(exc)}, now=now)
            if attempt < max_attempts:
                sleep(backoff_seconds)
    raise last_exc


def main(argv=None):
    parser = argparse.ArgumentParser(prog='stock_radar', description='Stock Radar CLI')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('sync-universe').set_defaults(func=cmd_sync_universe)

    p = sub.add_parser('sync-quotes')
    p.add_argument('--candidates', action='store_true', help="quote what the rule holds: tonight's orders and the account's positions")
    p.add_argument('--symbols', help='comma-separated symbols to quote')
    p.add_argument('--source', choices=['mis', 'fugle'], default='mis',
                   help='mis.twse.com.tw (default, no key, no reliable trial flag) or fugle (real lastTrial/lastTrade split, needs a working API key)')
    p.add_argument('--fugle-key-file', type=Path, default=None,
                   help='path to a local file containing the Fugle API key; defaults to fugle-dashboard/engine/config.json api_key')
    p.add_argument('--allow-full-market-fugle', action='store_true',
                   help=f'Override the {FUGLE_CANDIDATE_CAP}-symbol Fugle candidate cap (what the rule can hold at once; use only with a confirmed plan)')
    p.set_defaults(func=cmd_sync_quotes)

    sub.add_parser('sync-financials').set_defaults(func=cmd_sync_financials)
    sub.add_parser('sync-risk').set_defaults(func=cmd_sync_risk)
    sub.add_parser('sync-calendar', help='Refresh the TWSE trading-day calendar (holidaySchedule OpenAPI)').set_defaults(func=cmd_sync_calendar)

    p = sub.add_parser('export')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--mode', choices=['live', 'simulation'], default='live')
    p.set_defaults(func=cmd_export)

    p = sub.add_parser('backup')
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser('restore', help='Restore a backup .db onto a target path (refuses to overwrite a non-empty target without --force)')
    p.add_argument('--backup', type=Path, required=True)
    p.add_argument('--target', type=Path, required=True)
    p.add_argument('--force', action='store_true', help='Overwrite a non-empty existing target DB')
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
