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
from .fugle import fetch_quotes as fetch_fugle_quotes
from .financials import fetch_quarterly_income_general, fetch_monthly_revenue, fetch_pe_yield_pb
from .tpex import fetch_otc_daily_close
from .calendar import fetch_holiday_schedule, is_trading_day
from .risk import build_risk_facts
from .export import build_radar_json
from .discord import send_message, chunk_text, format_daily_summary, lookup_reply, DISCORD_CHANNEL as DISCORD_CHANNEL_DEFAULT

DEFAULT_DB = Path(__file__).parent.parent / 'stock_radar.db'
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
        if args.source == 'fugle':
            key = _fugle_api_key(args.fugle_key_file)
            if not key:
                raise SystemExit('--source fugle requested but no API key found (pass --fugle-key-file or set fugle-dashboard/engine/config.json api_key)')
            try:
                quotes = fetch_fugle_quotes(instruments, key)
                print(f'quotes source: Fugle marketdata v1.0 ({len(quotes)} symbols)')
            except Exception as exc:
                print(f'Fugle source failed ({exc!r}); falling back to mis.twse.com.tw')
        if quotes is None:
            quotes = fetch_quotes(instruments)
            print(f'quotes source: mis.twse.com.tw ({len(quotes)} symbols)')
        n = 0
        for symbol, q in quotes.items():
            store.observe(q)
            n += 1
        store.set_meta('last_quote_sync', {'at': datetime.now(TW).isoformat(), 'requested': len(instruments), 'received': n})
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


def cmd_export(args):
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
        health = [{
            'name': '試撮', 'status': 'blocked',
            'detail': '目前報價來源(mis.twse.com.tw)未提供可靠的試撮/盤中旗標，Fugle API 回報 401；盤前 08:30-09:00 不會產生可掛價信號',
            'as_of': None,
        }]
        now = datetime.now(TW)
        if args.assume_market_open:
            # explicit manual/testing override, bypasses the real calendar
            market_open = True
        else:
            schedule = store.meta('trading_calendar')
            try:
                market_open = is_trading_day(now.date(), schedule)
            except ValueError as exc:
                # calendar missing/stale for this year -- do NOT silently
                # guess open; fail closed (blocked) and tell the operator
                # to run sync-calendar, same posture as other missing-data
                # gates in this CLI (Fugle 401, OTC risk gap, etc.)
                market_open = False
                health.append({
                    'name': '交易日曆', 'status': 'blocked',
                    'detail': f'{exc} (run sync-calendar)', 'as_of': None,
                })
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
        if not any(quotes.values()):
            health.append({'name': '報價', 'status': 'blocked', 'detail': '尚無任何已同步報價', 'as_of': None})
        out = build_radar_json(instruments=target, quotes=quotes, decisions=decisions,
                               valuations=valuations, research={}, health=health,
                               mode=args.mode, now=now)
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


def _bot_token():
    """Read the Discord bot token from OpenClaw's own config at call time --
    never hardcoded, never logged. Kept as a thin local helper (not a
    module-level import) so stock_radar stays importable/testable without
    OpenClaw installed; only Discord-posting commands need this."""
    import json as _json
    cfg = _json.loads(Path.home().joinpath('.openclaw', 'openclaw.json').read_text())
    return cfg['channels']['discord']['token']


def cmd_notify_summary(args):
    radar = json.loads(Path(args.radar_json).read_text())
    # format_daily_summary forces the 🧪 test marker itself whenever
    # radar['mode'] == 'simulation', regardless of --test -- the data's own
    # mode field decides, not this CLI flag, so it can't be bypassed by a
    # future caller forgetting --test.
    text = format_daily_summary(radar, test_marker=args.test)
    chunks = chunk_text(text)
    if args.dry_run:
        print(f'--- would send {len(chunks)} message(s) to channel {args.channel} ---')
        for c in chunks:
            print(c)
            print('---')
        return
    token = _bot_token()
    ids = []
    for c in chunks:
        result = send_message(token, c, channel_id=args.channel)
        ids.append(result.get('id'))
    print(f'sent {len(ids)} message(s): {ids}')


def cmd_discord_lookup(args):
    store = Store(args.db)
    try:
        q = args.query.strip()
        matches = [i for i in store.instruments()
                  if q == i['symbol'] or q in (i.get('name') or '')]
        radar = json.loads(Path(args.radar_json).read_text()) if args.radar_json and Path(args.radar_json).exists() else None
        items = []
        if radar:
            by_symbol = {i['symbol']: i for i in radar['items']}
            items = [by_symbol[m['symbol']] for m in matches if m['symbol'] in by_symbol]
        reply = lookup_reply(items) if items else ('查無此標的。' if not matches else '找到標的但尚無雷達資料，請先執行 export。')
        text = ('🧪【測試查詢】' + reply) if args.test else reply
        print(text)
        if args.post:
            token = _bot_token()
            send_message(token, text, channel_id=args.channel)
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

    p = sub.add_parser('lookup')
    p.add_argument('query')
    p.set_defaults(func=cmd_lookup)

    p = sub.add_parser('notify-summary', help='Post the daily summary to Discord (test-marked unless --mode live export)')
    p.add_argument('--radar-json', type=Path, required=True)
    p.add_argument('--channel', default=DISCORD_CHANNEL_DEFAULT)
    p.add_argument('--test', action='store_true', help='Prefix message with the test marker (required unless the radar.json mode is live)')
    p.add_argument('--dry-run', action='store_true', help='Print instead of sending')
    p.set_defaults(func=cmd_notify_summary)

    p = sub.add_parser('discord-lookup', help='Answer a symbol/name query, optionally posting the reply to Discord')
    p.add_argument('query')
    p.add_argument('--radar-json', type=Path, default=None)
    p.add_argument('--channel', default=DISCORD_CHANNEL_DEFAULT)
    p.add_argument('--test', action='store_true')
    p.add_argument('--post', action='store_true', help='Actually send to Discord instead of just printing')
    p.set_defaults(func=cmd_discord_lookup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
