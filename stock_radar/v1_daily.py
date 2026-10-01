"""The nightly Strategy v1 scan that production actually runs. No LLM, no network.

This is what replaced the valuation band as the thing that decides what gets recommended and what the
simulated account buys (user decision, 2026-10-02). The band still gets computed and still shows on the
site as context, because knowing what a stock is worth is useful even when it is not what selects it.

Why the switch. The band was measured over five years and eight disjoint windows against random draws
from the same universe and came out indistinguishable from chance -- so the forward record it was
accumulating could not teach the weekly review anything, which is the record's whole purpose.

Unlike the replay, this runs with fundamentals and the risk fact switched ON. Both are snapshots of
today, which is exactly what a decision about today should use; it is only backtesting a past day with
them that would be reading the future.

The output is one meta row, `v1_candidates`, holding the day it was built for, every candidate with its
score, its setup, its entry, its structural stop, its target and its R:R, and the tally of what happened
to everything else. Three things read it: the pre-open message, the simulated account and the export.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from .domain import TW
from .store import Store
from . import buy_plan, market, screen, selection

VERSION = 'v1-daily'

# The score the document calls B. Its A/B/C cuts (85/75/65) are an intent -- trade only the best -- and
# the measurement says the scale does not line up with them: over 9,261 candidates across five years the
# median is 59.2, the 90th percentile 74.9, and the highest ever 88.6. Trading only A would mean 13
# trades in five years and a day with one at all 1% of the time, which teaches the weekly review nothing.
# B is the top tenth of candidates and appears on 46% of days, which is selective and still a record.
# Section 8 says these are v1 values for paper simulation to calibrate; this is that calibration, and it
# is the floor the weekly review should argue with first.
MIN_SCORE = 75


def run(store, now, settings=None):
    """Scan the last completed trading day and store the result. Idempotent per day."""
    schedule = store.meta('trading_calendar')
    day = str(buy_plan.last_completed_day(now, schedule))
    prices = market.split_adjust(market.load(store, 'prices', market.SCAN_DAYS))
    if not prices or prices[-1][0] != day:
        return {'status': 'no_bars', 'day': day,
                'have': prices[-1][0] if prices else None}
    flows = market.load(store, 'flows', 20)
    rows, tally = screen.run(store, prices, flows, len(prices) - 1, settings)

    floor = (settings or {}).get('min_score', MIN_SCORE)
    candidates, waits = [], []
    for r in rows:
        plan, why = selection.trade_plan(r['features'], r['setup'], settings)
        if plan and r['total'] < floor:
            plan, why = None, f"分數 {r['total']:.0f} 低於下單門檻 {floor}（{r['grade']} 級）"
        entry = {'symbol': r['symbol'], 'name': r['name'], 'industry': r['industry'],
                 'setup': r['setup'], 'setup_why': r['why'], 'total': r['total'],
                 'grade': r['grade'], 'scored_out_of': r['scored_out_of'],
                 'missing': r['missing'],
                 'components': {k: v['points'] for k, v in r['components'].items()},
                 'close': r['features']['close'],
                 'range_position': r['features']['range_position'],
                 'relative_volume': r['features']['relative_volume']}
        if plan:
            candidates.append({**entry, **plan})
        else:
            waits.append({**entry, 'wait_reason': why})
    candidates.sort(key=lambda c: -c['total'])
    waits.sort(key=lambda c: -c['total'])

    payload = {'version': VERSION, 'at': now.isoformat(), 'day': day,
               'min_score': floor,
               'settings': {k: (selection.SETTINGS | selection.TRADE)[k]
                            for k in sorted(selection.SETTINGS | selection.TRADE)},
               'tally': tally, 'candidates': candidates, 'waits': waits[:40]}
    store.set_meta('v1_candidates', payload)
    return {'status': 'ok', 'day': day, 'candidates': len(candidates), 'waits': len(waits),
            'tally': tally, 'best': candidates[0]['total'] if candidates else None}


def text(store, now):
    """What the pre-open message shows. Answer first: what to buy, or that there is nothing."""
    payload = store.meta('v1_candidates') or {}
    if not payload:
        return 'Strategy v1 尚未產生任何掃描結果。'
    picks = payload['candidates'][:5]
    t = payload['tally']
    lines = [f"台股 Strategy v1｜{payload['day']} 收盤掃描"]
    if picks:
        lines.append(f"可下單 {len(payload['candidates'])} 檔，以下為分數最高的 {len(picks)} 檔：")
        for n, c in enumerate(picks, 1):
            kind = '突破' if c['setup'] == 'breakout' else '回檔'
            lines.append('')
            lines.append(f"{n}. {c['name']} {c['symbol']}（{kind}，{c['total']:.0f} 分 {c['grade']}）")
            lines.append(f"   進場 {c['entry']:g}／停損 {c['stop']:g}／目標 {c['target']:g}"
                         f"｜風報比 {c['rr']:.1f}")
            lines.append(f"   {c['stop_reason']}")
    else:
        lines.append('今天沒有任何標的同時通過選股、進場與風報比，不交易。')
    lines.append('')
    lines.append(f"掃描 {t['universe']} 檔上市普通股：硬性排除 {t['filtered']}、"
                 f"無型態 {t['no_setup']}、突破 {t['breakout']}、回檔 {t['pullback']}")
    if payload['waits']:
        near = payload['waits'][0]
        lines.append(f"分數最高但不下單：{near['name']} {near['symbol']}（{near['total']:.0f} 分）"
                     f"－{near['wait_reason']}")
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--text', action='store_true')
    a = p.parse_args(argv)
    s = Store(a.db)
    try:
        now = datetime.now(TW)
        result = run(s, now)
        print(text(s, now) if a.text and result['status'] == 'ok'
              else json.dumps(result, ensure_ascii=False))
    finally:
        s.close()


if __name__ == '__main__':
    main()
