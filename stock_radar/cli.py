#!/usr/bin/env python3
"""Stock Radar CLI. All commands operate on the SQLite Store, independent
of OpenClaw's own state. Per explicit product decision (2026-09-10), the
radar UI IS the new docs/index.html -- the old tab-based dashboard was
intentionally replaced, not kept as a separate page. engine.py and
docs/dashboard.json stay in place for compatibility with existing cron
scripts (stock_morning_report.sh etc.), but they are no longer what
docs/index.html renders. This CLI never edits docs/index.html, docs/radar.css,
or docs/radar.js (frontend files, owned by Kid/Codex's frontend track);
it only ever writes docs/radar.json.

Usage:
  python3 -m stock_radar.cli sync-universe [--db PATH]
  python3 -m stock_radar.cli sync-quotes [--db PATH]
  python3 -m stock_radar.cli sync-financials [--db PATH]
  python3 -m stock_radar.cli sync-calendar [--db PATH]
  python3 -m stock_radar.cli sync-risk [--db PATH]
  python3 -m stock_radar.cli propose SYMBOL --file valuation.json [--db PATH]
  python3 -m stock_radar.cli apply PROPOSAL_ID --actor ID --channel ID [--db PATH]
  python3 -m stock_radar.cli research SYMBOL --file notes.json [--db PATH]
  python3 -m stock_radar.cli export --out docs/radar.json [--db PATH] [--mode live|simulation]
  python3 -m stock_radar.cli backup --out PATH [--db PATH]
  python3 -m stock_radar.cli lookup QUERY [--db PATH]   # disambiguation search by symbol/name
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .domain import TW, decide
from .store import Store, atomic_json
from .universe import build_universe, equity_universe
from .quotes import fetch_quotes, fetch_daily_close_all
from .fugle import fetch_quotes as fetch_fugle_quotes, FugleClient, FugleError
from .financials import fetch_quarterly_income_general, fetch_monthly_revenue, fetch_pe_yield_pb
from .tpex import fetch_otc_daily_close
from .calendar import fetch_holiday_schedule, is_trading_day
from .risk import build_risk_facts
from .runtime import single_flight_lock, LockBusyError
from .diff import significant_changes_against_snapshot, snapshot_signals, revoked_actionable_symbols
import hashlib
import time as _time
from .export import build_radar_json
from .discord import (send_message, chunk_text, format_daily_summary, format_status_line, lookup_reply,
                      is_test_mode, TEST_MARKER_PREFIX, extract_query, fetch_new_messages,
                      DISCORD_CHANNEL as DISCORD_CHANNEL_DEFAULT)

# DEMO_DB: the default SQLite path used when --db is omitted. This lives
# INSIDE the git worktree (.gitignore'd, never committed) and exists
# purely for local development/manual testing convenience -- it is NOT,
# and must never become, the production runtime database. A git worktree
# can be deleted/recreated/pruned at any time (it is a disposable checkout
# of a branch, not a durable data directory), so storing real production
# valuations/state here would risk silent data loss on routine git
# operations, and there'd be no way to tell a real production run from a
# local dry-run just by looking at the file. cmd_export enforces this: a
# --mode live export REQUIRES an explicit --db pointing OUTSIDE the repo
# tree (see PROD_DB_RECOMMENDED / _reject_demo_db_for_live below), so a
# forgotten --db flag can never let live output silently land in the demo
# file (or, worse, a live export silently READ stale demo-mode state).
DEMO_DB = Path(__file__).parent.parent / 'stock_radar.db'
DEFAULT_DB = DEMO_DB  # kept as the argparse default so existing dev/test invocations are unaffected
# Free-tier Fugle usage has no documented numeric rate/quota limit found
# as of 2026-09-10 (checked developer.fugle.tw docs) -- rather than
# silently assuming "whatever we ask for is fine" and potentially burning
# through an undocumented quota against the FULL market (2,000+ symbols,
# each needing 2 HTTP calls per fugle.py's one-ticker+one-quote-per-symbol
# design), Fugle real-time quotes are scoped to a bounded CANDIDATE list
# (--watchlist-only, i.e. symbols an operator has explicitly flagged
# 'watched' -- typically ones with an approved/proposed valuation) unless
# --allow-full-market-fugle is explicitly passed. Full-market coverage
# uses the free, unthrottled mis.twse.com.tw source (default) on whatever
# cadence the caller's cron schedule runs it at (documented as a DAILY
# update in openclaw/schedule.md, not real-time) -- this project does not
# claim full-market real-time coverage anywhere, and this cap is what
# keeps that claim honest in code, not just in a doc comment someone could
# drift away from.
# 30-symbol cap matches stock_radar.fugle.fetch_quotes()'s own max_symbols
# default (Codex's v2 FugleClient adapter, 2026-09-10): one ticker+quote
# call pair per symbol at a fixed 1.1s-between-requests pace (enforced
# inside FugleClient itself, not by a session wrapper here -- see below),
# so 30 candidates is roughly 66s per sync-quotes tick. Kept in sync with
# fugle.py's own default rather than silently drifting to a different
# number in two places.
FUGLE_CANDIDATE_CAP = 30
FUGLE_LOCK_PATH = Path(__file__).parent.parent / '_state' / 'fugle_sync.lock'
# Recommended production runtime path: outside any git worktree, inside
# the durable OpenClaw workspace root (survives worktree add/remove and is
# covered by the workspace's own backup skill, unlike a path inside a
# disposable branch checkout).
PROD_DB_RECOMMENDED = Path.home() / '.openclaw' / 'workspace' / '_state' / 'stock_radar' / 'prod.db'
DISCORD_CHANNEL = '1493898877970153532'
# Whitelist of Discord user IDs allowed to apply valuations. Populated from
# the same guild-user allowlist already used for the bot's DM/mention scope
# (openclaw.json channels.discord.guilds.*.users) -- kept here explicitly
# rather than re-reading openclaw.json at runtime, so this module has no
# dependency on OpenClaw's own config format and stays independently
# testable/portable. Update this list if the authorized user set changes.
ALLOWED_ACTORS = ['554850002883706901']  # Kid


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
    cfg_path = Path(__file__).parent.parent.parent / 'fugle-dashboard' / 'engine' / 'config.json'
    try:
        cfg = json.loads(cfg_path.read_text())
        return cfg.get('api_key') or None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def cmd_sync_quotes(args):
    store = Store(args.db)
    try:
        instruments = [i for i in store.instruments() if i['kind'] in ('stock', 'etf_equity')]
        if args.watchlist_only:
            instruments = [i for i in instruments if i.get('watched')]
        if not instruments:
            print('no instruments to quote (run sync-universe first, or use --watchlist-only with a populated watchlist)')
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
                # stays scoped to a bounded candidate list (--watchlist-only)
                # unless the operator explicitly overrides. This must NOT be
                # silently downgraded to a partial fetch -- fail loudly so a
                # cron job misconfiguration is visible in its run log, not
                # discovered later as "why did we only get some quotes".
                raise SystemExit(
                    f'--source fugle requested for {len(instruments)} instruments, above the '
                    f'{FUGLE_CANDIDATE_CAP}-symbol candidate cap (Fugle free-tier quota is '
                    f'undocumented; see FUGLE_CANDIDATE_CAP comment in cli.py). Use '
                    f'--watchlist-only to scope to your candidate list, or pass '
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
        for symbol, q in quotes.items():
            store.observe(q)
            n += 1
        # Record the REAL outcome (which source actually served data, and any
        # Fugle failure detail) so cmd_export's health entries can report
        # what actually happened instead of a hardcoded assumption. This is
        # the single source of truth cmd_export reads from -- it must never
        # print a stale/hardcoded '401' message once a sync has actually
        # succeeded via mis.twse or Fugle.
        store.set_meta('last_quote_sync', {
            'at': datetime.now(TW).isoformat(), 'requested': len(instruments), 'received': n,
            'source': source_used, 'fugle_attempted': args.source == 'fugle',
            'fugle_error': fugle_error,
        })
        print(f'quotes synced: {n}/{len(instruments)}')
        if n < len(instruments):
            print(f'WARNING: {len(instruments)-n} instruments did not return a quote (partial coverage, not silently treated as success)')
    finally:
        store.close()


def cmd_sync_calendar(args):
    store = Store(args.db)
    try:
        schedule = fetch_holiday_schedule()
        store.set_meta('trading_calendar', schedule)
        print(f"calendar synced: ROC years {schedule['roc_years']}, "
              f"{len(schedule['closed_dates'])} holiday dates cached")
    finally:
        store.close()


def cmd_sync_risk(args):
    store = Store(args.db)
    try:
        instruments = [i for i in store.instruments() if i['kind'] in ('stock', 'etf_equity')]
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


def cmd_propose(args):
    store = Store(args.db)
    try:
        valuation = json.loads(Path(args.file).read_text())
        proposal_id = store.propose(args.symbol, valuation)
        print(f'proposal created: {proposal_id} for {args.symbol}')
    finally:
        store.close()


def cmd_apply(args):
    store = Store(args.db)
    try:
        store.apply(args.proposal_id, actor=args.actor, allowed_actors=ALLOWED_ACTORS, channel=args.channel)
        print(f'proposal {args.proposal_id} applied by {args.actor}')
    finally:
        store.close()


def cmd_research(args):
    """Sets narrative-only research notes (why_now/chips/catalysts/risks)
    for a symbol, stored as a 'research' fact so cmd_export can read real
    DB content instead of a hardcoded {}. These fields do NOT feed into
    any price math (unlike a valuation's thesis/sources, which
    validate_valuation() already requires) -- they are purely descriptive
    context shown in the frontend detail view."""
    store = Store(args.db)
    try:
        payload = json.loads(Path(args.file).read_text())
        allowed = {'why_now', 'chips', 'catalysts', 'risks', 'thesis', 'sources'}
        unknown = set(payload) - allowed
        if unknown:
            raise SystemExit(f'unknown research fields: {sorted(unknown)} (allowed: {sorted(allowed)})')
        store.set_fact(args.symbol, 'research', payload)
        print(f'research notes saved for {args.symbol}')
    finally:
        store.close()


def _quote_source_health(store, now):
    """Build the '試撮'/quote-source health entry from the ACTUAL outcome of
    the most recent sync-quotes run (stored in Store.meta('last_quote_sync')
    by cmd_sync_quotes), never a hardcoded '401' string. Fixes a real bug:
    the old hardcoded message kept claiming Fugle returns 401 even after a
    successful mis.twse-only sync, and would keep claiming it forever if
    Fugle ever started working -- health must reflect what actually just
    happened, not a snapshot frozen at whenever this file was last edited.
    """
    meta = store.meta('last_quote_sync')
    if not meta:
        return {
            'name': '試撮', 'status': 'pending',
            'detail': '尚未執行過 sync-quotes，目前無任何行情來源資料', 'as_of': None,
        }
    source = meta.get('source')
    fugle_attempted = meta.get('fugle_attempted')
    fugle_error = meta.get('fugle_error')
    at = meta.get('at')
    if source == 'fugle':
        return {
            'name': '試撮', 'status': 'ok',
            'detail': 'Fugle marketdata v1.0 成功回傳，支援資料驅動的試撮判斷（非時鐘推斷）', 'as_of': at,
        }
    if fugle_attempted and fugle_error:
        return {
            'name': '試撮', 'status': 'blocked',
            'detail': f'行情來源(mis.twse.com.tw)未提供可靠的試撮/盤中旗標；Fugle 本次嘗試失敗（{fugle_error}），目前回到 fallback 來源；盤前 08:30-09:00 不會產生可掛價信號',
            'as_of': at,
        }
    return {
        'name': '試撮', 'status': 'blocked',
        'detail': '目前行情來源(mis.twse.com.tw)未提供可靠的試撮/盤中旗標（未嘗試 Fugle）；盤前 08:30-09:00 不會產生可掛價信號',
        'as_of': at,
    }


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
    _reject_demo_db_for_live(args.db, args.mode)
    store = Store(args.db)
    try:
        instruments = store.instruments()
        # Per RADAR_DATA_CONTRACT.md, radar.json items carry kind in
        # (stock, etf_equity, etf_other, etn). etf_other (leveraged/
        # inverse/bond/active/balanced/futures-tracking ETFs -- anything
        # TWSE's own fund-type disclosure doesn't confirm as plain passive
        # equity, see universe.classify_etf_kinds) has no valuation model
        # yet but must still appear and count toward coverage.etfs, shown
        # as '待研究' (decide() returns 'pending' for any kind outside
        # stock/etf_equity), not silently dropped. etn (Exchange Traded
        # Note -- a bank debt instrument, not a fund) also appears and gets
        # 'pending' from decide(), but export.py does NOT count it toward
        # coverage.etfs since it isn't one. preferred/tdr/reit/abs stay in
        # the universe DB for `lookup` but are not part of the radar.json
        # contract's supported kinds.
        target = [i for i in instruments if i['kind'] in ('stock', 'etf_equity', 'etf_other', 'etn')]
        quotes, decisions, valuations = {}, {}, {}
        now = datetime.now(TW)
        health = [_quote_source_health(store, now)]
        if args.assume_market_open and args.mode == 'live':
            # --assume-market-open is a manual/testing override for research
            # runs only. A live export must reflect the REAL trading
            # calendar; allowing this flag to bypass it in live mode would
            # let a forgotten testing flag silently make a holiday look
            # like a trading day in production output.
            raise SystemExit('--assume-market-open is not allowed with --mode live '
                             '(use sync-calendar to populate the real trading calendar instead)')
        if args.assume_market_open:
            # explicit manual/testing override, bypasses the real calendar
            # (only reachable for --mode simulation, enforced above)
            market_open = True
        else:
            schedule = store.meta('trading_calendar')
            try:
                # True/False here are POSITIVE determinations from a
                # populated calendar cache. Do NOT default this to False on
                # any other code path -- an unpopulated/absent cache must
                # produce market_open=None (below), not a bare False, so
                # domain.decide()'s session_phase() can tell "verified
                # closed" apart from "we don't actually know".
                market_open = is_trading_day(now.date(), schedule)
            except ValueError as exc:
                # calendar missing/stale for this year -- market status is
                # UNKNOWN, not "closed". market_open=None flows into
                # domain.decide()'s session_phase(), which has its own
                # explicit is-None branch (never bool(None) coerced to
                # False) and blocks with a distinct reason from a verified
                # non-trading-day.
                market_open = None
                health.append({
                    'name': '交易日曆', 'status': 'blocked',
                    'detail': f'{exc} (run sync-calendar)', 'as_of': None,
                })
        research = {}
        for inst in target:
            symbol = inst['symbol']
            q = store.quote(symbol)
            quotes[symbol] = q
            v = store.active(symbol)
            valuations[symbol] = v
            risk = store.fact(symbol, 'risk')
            history = store.history(symbol, limit=20)
            decisions[symbol] = decide(inst, q, v, now=now, market_open=market_open,
                                       risk=risk or None, history=history)
            # Research notes come from two REAL DB sources, never a
            # hardcoded {}: thesis/sources are read from the active
            # (already evidence-reviewed) valuation itself -- the same
            # data validate_valuation() already required to have an HTTPS
            # source and a thesis string, so this is not new unvetted
            # content. why_now/chips/catalysts/risks are narrative-only
            # supplementary notes (they do not affect any price math) held
            # in the 'research' facts category, settable via the
            # `research` CLI command. valuation_history comes straight from
            # Store.versions() so past applied/superseded/rejected
            # valuations are visible with their own reason text, per the
            # frontend's 估值歷史 section.
            note = store.fact(symbol, 'research') or {}
            research[symbol] = {
                'thesis': (v or {}).get('thesis') or note.get('thesis'),
                'why_now': note.get('why_now'),
                'chips': note.get('chips'),
                'catalysts': note.get('catalysts') or [],
                'risks': note.get('risks') or [],
                'sources': (v or {}).get('sources') or note.get('sources') or [],
                'valuation_history': store.versions(symbol),
            }
        if not any(quotes.values()):
            health.append({'name': '報價', 'status': 'blocked', 'detail': '尚無任何已同步報價', 'as_of': None})
        out = build_radar_json(instruments=target, quotes=quotes, decisions=decisions,
                               valuations=valuations, research=research, health=health,
                               mode=args.mode, now=now)
        # Self-check the payload we're about to publish BEFORE writing it,
        # using the exact same verify_radar.verify() gate a human would run
        # manually -- catches a schema/contract regression at export time
        # instead of only discovering it later when the frontend or a
        # Discord notify job chokes on a malformed docs/radar.json. This
        # check is diagnostic-only here (never blocks the write): the tool
        # itself has no network access and cannot fix a real upstream data
        # problem, and refusing to publish ANY snapshot because one symbol
        # has a bad record would be worse than publishing with a visible
        # warning. --mode live additionally runs the tool's own stricter
        # `production=True` gate (freshness window, no simulation data).
        try:
            from tools.verify_radar import verify as _verify_radar
            _verify_errors = _verify_radar(out, production=(args.mode == 'live'))
        except Exception as exc:
            _verify_errors = [f'verify_radar itself failed to run: {exc!r}']
        if _verify_errors:
            print(f'WARNING: verify_radar found {len(_verify_errors)} issue(s) (export still written, evidence fields preserved):')
            for e in _verify_errors[:20]:
                print(f'  - {e}')
        atomic_json(args.out, out)
        print(f'exported {len(target)} instruments to {args.out} (mode={args.mode}, quotes={out["coverage"]["quotes"]}, valued={out["coverage"]["valued"]})')
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
        return {'status': 'skipped_duplicate', 'delivery_id': existing['id']}
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


def cmd_notify_summary(args):
    store = Store(args.db)
    try:
        radar = json.loads(Path(args.radar_json).read_text())
        # format_daily_summary forces the 🧪 test marker itself whenever
        # radar['mode'] == 'simulation', regardless of --test -- the data's own
        # mode field decides, not this CLI flag, so it can't be bypassed by a
        # future caller forgetting --test.
        text = format_daily_summary(radar, test_marker=args.test)
        chunks = chunk_text(text)
        now = datetime.now(TW)
        if args.dry_run:
            print(f'--- would send {len(chunks)} message(s) to channel {args.channel} ---')
            for c in chunks:
                print(c)
                print('---')
            return
        token = _bot_token()
        results = [_send_with_outbox(store, token, c, channel=args.channel, now=now) for c in chunks]
        sent = [r for r in results if r['status'] == 'sent']
        skipped = [r for r in results if r['status'] == 'skipped_duplicate']
        print(f'sent {len(sent)} message(s), skipped {len(skipped)} duplicate(s): '
             f'{[r.get("message_id") for r in sent]}')
    finally:
        store.close()


def cmd_notify_changes(args):
    """The 08:55 step per STOCK_RADAR_SPEC.md: '只有狀態／掛價重大改變、失效
    才通知'. Compares the current radar.json's signals against the LAST
    NOTIFIED snapshot (Store.meta('last_notified_signals'), a compact
    {symbol: signal-subset} map, not a second full radar.json on disk) via
    stock_radar.diff.significant_changes_against_snapshot(). Only sends a
    message when something actually changed; the snapshot is updated to
    the full current state on EVERY run (whether or not a notification was
    sent) so the next run's diff is always against the truly-latest state,
    not a stale one from whenever the last notification happened to fire.
    """
    store = Store(args.db)
    try:
        radar = json.loads(Path(args.radar_json).read_text())
        now = datetime.now(TW)
        previous_snapshot = store.meta('last_notified_signals')
        changed = significant_changes_against_snapshot(previous_snapshot, radar['items'])
        # Revoked BEFORE the snapshot is overwritten below -- this needs
        # the OLD (previous_snapshot) vs CURRENT items comparison, per
        # Codex review 2026-09-10: a symbol that was previously actionable
        # (sweet/add/buy) but has vanished from current_items entirely
        # (delisted / dropped by a later sync-universe / correction) must
        # get an explicit revocation notice, since a human may still be
        # acting on the stale instruction and silence would be read as
        # "still valid", not "we don't know anymore".
        revoked = revoked_actionable_symbols(previous_snapshot, radar['items'])
        # Always refresh the snapshot to the CURRENT full state, regardless
        # of whether anything changed -- otherwise a quiet run would leave
        # the next comparison pointed at an older, possibly stale baseline.
        store.set_meta('last_notified_signals', snapshot_signals(radar['items']))
        if not changed and not revoked:
            print('no significant signal changes since last notification; nothing sent')
            return
        is_test = is_test_mode(radar, explicit_test=args.test)
        header = TEST_MARKER_PREFIX if is_test else ''
        lines = [f'{header}雷達變化通知 {radar.get("market_date")} {radar.get("generated_at", "")[11:16]}', '']
        if changed:
            lines.append(f'{len(changed)} 檔狀態/掛價有重大變化：')
            for item in changed:
                lines.append(format_status_line(item, now=now))
        if revoked:
            if changed:
                lines.append('')
            status_label = {'sweet': '甜甜價', 'add': '加碼區', 'buy': '買進區'}
            lines.append(f'{len(revoked)} 檔之前的可行動掛價已撤回（標的不再存在於母表，請勿再依旧指令掃單）：')
            for r in revoked:
                lines.append(f"- {r['symbol']}（原狀態：{status_label.get(r['previous_status'], r['previous_status'])}）")
        text = '\n'.join(lines)
        chunks = chunk_text(text)
        if args.dry_run:
            print(f'--- would send {len(chunks)} message(s) to channel {args.channel} ({len(changed)} changed symbols) ---')
            for c in chunks:
                print(c)
                print('---')
            return
        token = _bot_token()
        results = [_send_with_outbox(store, token, c, channel=args.channel, now=now) for c in chunks]
        sent = [r for r in results if r['status'] == 'sent']
        skipped = [r for r in results if r['status'] == 'skipped_duplicate']
        print(f'{len(changed)} symbols changed; sent {len(sent)} message(s), skipped {len(skipped)} duplicate(s)')
    finally:
        store.close()


def _lookup_text(store, radar, query, *, explicit_test):
    """Shared lookup-reply-text logic used by both cmd_discord_lookup
    (manual/scripted --post) and cmd_discord_poll (real inbound message
    routing, item 7) -- one code path for both so they cannot silently
    diverge in behavior (e.g. one honoring is_test_mode and the other
    forgetting it)."""
    q = query.strip()
    matches = [i for i in store.instruments()
              if q == i['symbol'] or q in (i.get('name') or '')]
    items = []
    if radar:
        by_symbol = {i['symbol']: i for i in radar['items']}
        items = [by_symbol[m['symbol']] for m in matches if m['symbol'] in by_symbol]
    reply = lookup_reply(items) if items else ('查無此標的。' if not matches else '找到標的但尚無雷達資料，請先執行 export。')
    # is_test_mode() forces the marker whenever radar_json['mode'] ==
    # 'simulation', regardless of --test -- a lookup against a
    # simulation-mode radar.json must never render as if it were a
    # live query just because a caller forgot --test (mirrors
    # format_daily_summary's own auto-marking contract).
    return (TEST_MARKER_PREFIX + reply) if is_test_mode(radar, explicit_test=explicit_test) else reply


def cmd_discord_lookup(args):
    store = Store(args.db)
    try:
        radar = json.loads(Path(args.radar_json).read_text()) if args.radar_json and Path(args.radar_json).exists() else None
        text = _lookup_text(store, radar, args.query, explicit_test=args.test)
        print(text)
        if args.post:
            token = _bot_token()
            send_message(token, text, channel_id=args.channel)
    finally:
        store.close()


def cmd_discord_poll(args):
    """Item 7: routes REAL inbound Discord messages (not just an operator
    manually running --post) to the CLI's own lookup logic. Polls once per
    invocation (intended to be called repeatedly by a scheduler, e.g. every
    minute) using Discord's `after` message-id cursor (Store.meta
    'last_discord_poll_id') so it never re-processes an already-answered
    message across separate process invocations. Only messages matching
    discord.extract_query() (an explicit trigger like '$3661' / '查 0050')
    are treated as queries; anything else in the channel is ignored so this
    does not spam replies into ordinary conversation. Every reply THREADS
    under the triggering message via reply_to_message_id, so item 7's
    'real message routing' requirement is visibly satisfied in the UI, not
    just internally."""
    store = Store(args.db)
    try:
        token = _bot_token()
        radar = json.loads(Path(args.radar_json).read_text()) if args.radar_json and Path(args.radar_json).exists() else None
        cursor = store.meta('last_discord_poll_id').get('id')
        messages = fetch_new_messages(token, channel_id=args.channel, after_id=cursor, limit=args.limit)
        answered = 0
        for msg in messages:
            # Never answer the bot's own messages -- otherwise a bot reply
            # that happens to look like a query (unlikely given our own
            # format_status_line output, but not impossible) could trigger
            # an infinite reply loop.
            if msg.get('author', {}).get('bot'):
                continue
            query = extract_query(msg.get('content', ''))
            if query is None:
                continue
            text = _lookup_text(store, radar, query, explicit_test=args.test)
            if args.dry_run:
                print(f'--- would reply to message {msg["id"]} ---\n{text}\n---')
            else:
                send_message(token, text, channel_id=args.channel, reply_to_message_id=msg['id'])
            answered += 1
        if messages and not args.dry_run:
            store.set_meta('last_discord_poll_id', {'id': messages[-1]['id']})
        print(f'polled {len(messages)} new message(s), answered {answered} quer{"y" if answered == 1 else "ies"}')
    finally:
        store.close()


def cmd_lookup(args):
    store = Store(args.db)
    try:
        q = args.query.strip()
        matches = [i for i in store.instruments()
                  if q == i['symbol'] or q in (i.get('name') or '')]
        if not matches:
            print(f'no match for "{q}"')
            return
        if len(matches) > 1:
            print(f'ambiguous query "{q}", {len(matches)} candidates:')
        for m in matches:
            print(f"  {m['symbol']}  {m.get('name')}  [{m['kind']}/{m.get('market')}]")
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='stock_radar', description='Stock Radar CLI')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('sync-universe').set_defaults(func=cmd_sync_universe)

    p = sub.add_parser('sync-quotes')
    p.add_argument('--watchlist-only', action='store_true')
    p.add_argument('--source', choices=['mis', 'fugle'], default='mis',
                   help='mis.twse.com.tw (default, no key, no reliable trial flag) or fugle (real lastTrial/lastTrade split, needs a working API key)')
    p.add_argument('--fugle-key-file', type=Path, default=None,
                   help='path to a local file containing the Fugle API key; defaults to fugle-dashboard/engine/config.json api_key')
    p.add_argument('--allow-full-market-fugle', action='store_true',
                   help=f'Override the {FUGLE_CANDIDATE_CAP}-symbol Fugle candidate cap (undocumented free-tier quota; use only with a confirmed plan)')
    p.set_defaults(func=cmd_sync_quotes)

    sub.add_parser('sync-financials').set_defaults(func=cmd_sync_financials)
    sub.add_parser('sync-risk').set_defaults(func=cmd_sync_risk)
    sub.add_parser('sync-calendar', help='Refresh the TWSE trading-day calendar (holidaySchedule OpenAPI)').set_defaults(func=cmd_sync_calendar)

    p = sub.add_parser('propose')
    p.add_argument('symbol')
    p.add_argument('--file', required=True)
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser('apply')
    p.add_argument('proposal_id')
    p.add_argument('--actor', required=True)
    p.add_argument('--channel', required=True)
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('research', help='Set narrative-only research notes (why_now/chips/catalysts/risks) for a symbol')
    p.add_argument('symbol')
    p.add_argument('--file', required=True)
    p.set_defaults(func=cmd_research)

    p = sub.add_parser('export')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--mode', choices=['live', 'simulation'], default='live')
    p.add_argument('--assume-market-open', action='store_true',
                   help='Manual override: skip the real trading-calendar check (sync-calendar). '
                        'Without this flag, export reads the cached calendar (run sync-calendar first) '
                        'and fails closed (blocked) if the calendar has no data for the current ROC year.')
    p.set_defaults(func=cmd_export)

    p = sub.add_parser('backup')
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser('restore', help='Restore a backup .db onto a target path (refuses to overwrite a non-empty target without --force)')
    p.add_argument('--backup', type=Path, required=True)
    p.add_argument('--target', type=Path, required=True)
    p.add_argument('--force', action='store_true', help='Overwrite a non-empty existing target DB')
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser('lookup')
    p.add_argument('query')
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser('notify-summary', help='Post the daily summary to Discord (test-marked unless --mode live export)')
    p.add_argument('--radar-json', type=Path, required=True)
    p.add_argument('--channel', default=DISCORD_CHANNEL_DEFAULT)
    p.add_argument('--test', action='store_true', help='Prefix message with the test marker (required unless the radar.json mode is live)')
    p.add_argument('--dry-run', action='store_true', help='Print instead of sending')
    p.set_defaults(func=cmd_notify_summary)

    p = sub.add_parser('notify-changes', help='08:55 step: only post when status/quote signals significantly changed since last notification (outbox-deduped, limited-retry)')
    p.add_argument('--radar-json', type=Path, required=True)
    p.add_argument('--channel', default=DISCORD_CHANNEL_DEFAULT)
    p.add_argument('--test', action='store_true')
    p.add_argument('--dry-run', action='store_true', help='Print instead of sending')
    p.set_defaults(func=cmd_notify_changes)

    p = sub.add_parser('discord-lookup', help='Answer a symbol/name query, optionally posting the reply to Discord')
    p.add_argument('query')
    p.add_argument('--radar-json', type=Path, default=None)
    p.add_argument('--channel', default=DISCORD_CHANNEL_DEFAULT)
    p.add_argument('--test', action='store_true')
    p.add_argument('--post', action='store_true', help='Actually send to Discord instead of just printing')
    p.set_defaults(func=cmd_discord_lookup)

    p = sub.add_parser('discord-poll', help='Item 7: poll the channel for real inbound single-stock queries and reply threaded (run repeatedly on a schedule)')
    p.add_argument('--radar-json', type=Path, default=None)
    p.add_argument('--channel', default=DISCORD_CHANNEL_DEFAULT)
    p.add_argument('--test', action='store_true')
    p.add_argument('--limit', type=int, default=50)
    p.add_argument('--dry-run', action='store_true', help='Print replies instead of sending them, and do not advance the poll cursor')
    p.set_defaults(func=cmd_discord_poll)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
