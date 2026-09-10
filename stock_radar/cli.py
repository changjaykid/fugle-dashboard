#!/usr/bin/env python3
"""Stock Radar CLI. All commands operate on the SQLite Store, independent
of OpenClaw's own state. Never touches docs/dashboard.json or docs/index.html
(the existing production dashboard). The radar frontend lives at its own
path docs/radar/{index.html,radar.css,radar.js} to avoid colliding with the
live site; this CLI never edits those frontend files either.

Usage:
  python3 -m stock_radar.cli sync-universe [--db PATH]
  python3 -m stock_radar.cli sync-quotes [--db PATH]
  python3 -m stock_radar.cli sync-financials [--db PATH]
  python3 -m stock_radar.cli propose SYMBOL --file valuation.json [--db PATH]
  python3 -m stock_radar.cli apply PROPOSAL_ID --actor ID --channel ID [--db PATH]
  python3 -m stock_radar.cli export --out docs/radar/radar.json [--db PATH] [--mode live|simulation]
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
from .financials import fetch_quarterly_income_general, fetch_monthly_revenue, fetch_pe_yield_pb
from .tpex import fetch_otc_daily_close
from .export import build_radar_json

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


def cmd_sync_quotes(args):
    store = Store(args.db)
    try:
        instruments = [i for i in store.instruments() if i['kind'] in ('stock', 'etf_equity')]
        if args.watchlist_only:
            instruments = [i for i in instruments if i.get('watched')]
        if not instruments:
            print('no instruments to quote (run sync-universe first, or use --watchlist-only with a populated watchlist)')
            return
        quotes = fetch_quotes(instruments)
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
        target = [i for i in instruments if i['kind'] in ('stock', 'etf_equity')]
        quotes, decisions, valuations = {}, {}, {}
        health = [{
            'name': '試撮', 'status': 'blocked',
            'detail': '目前報價來源(mis.twse.com.tw)未提供可靠的試撮/盤中旗標，Fugle API 回報 401；盤前 08:30-09:00 不會產生可掛價信號',
            'as_of': None,
        }]
        now = datetime.now(TW)
        market_open = args.assume_market_open
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
    p.set_defaults(func=cmd_sync_quotes)

    sub.add_parser('sync-financials').set_defaults(func=cmd_sync_financials)

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
                   help='For manual/testing runs outside a real trading-calendar check')
    p.set_defaults(func=cmd_export)

    p = sub.add_parser('backup')
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser('lookup')
    p.add_argument('query')
    p.set_defaults(func=cmd_lookup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
