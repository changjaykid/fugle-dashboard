"""Weekend reports for the one rule set the agent trades (swing-v1): what the account did, what is next.

Saturday: the simulated account's week -- its value against the market, what was bought and sold and
why, what is held and how each holding will leave, and the rule's record so far beside the backtest it
was chosen on. Sunday: the plan for the week ahead -- the market filter, every resting order with its
stop and target, and the exit each holding is waiting for.

Both used to be built from the valuation system the swing rule replaced on 2026-10-13: "承接價",
"甜甜價", how far a P/E sat under its norm -- figures for a method nothing traded any more, sent every
weekend beside a daily message about the method that did.
"""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from .domain import TW
from .store import Store, atomic_json
from .cli import _bot_token, _send_with_outbox
from .discord import channel_for
from . import paper, swing
from .strategy_review import MEASURED

SITE = 'https://changjaykid.github.io/fugle-dashboard/'
# 'trail' is how the rule before swing-v1 named its trailing exit; its trades are still in the book.
EXITS = {'stop': '停損', 'target': '移動停利', 'trail': '移動停利', 'time': '時間到'}


def _market(store):
    scan = store.meta('daily_scan') or {}
    state = scan.get('market_open')
    if state is None:
        return '大盤濾網：未取得'
    return ('大盤濾網：0050 在 120 日均線之上，可開新單' if state else
            '大盤濾網：0050 在 120 日均線之下，暫停開新單（既有持倉照常管理）')


def _holding(p):
    move = (p['mark'] / p['entry'] - 1) * 100
    if p.get('armed'):
        exit_rule = f"已碰布林上緣，收盤跌破 {p['peak'] * (1 - swing.SETTINGS['give_back']):.2f} 賣出"
    else:
        exit_rule = (f"停損 {p.get('stop_now') or p.get('stop'):g}，"
                     f"碰到布林上緣 {p.get('target') or '—'} 後改為移動停利")
    return (f"・{p['name']} {p['symbol']}｜{p['shares']} 股 成本 {p['entry']:g} → {p['mark']:g}"
            f"（{move:+.1f}%）｜{exit_rule}")


def weekly(store, now):
    book = paper.Book.load(store)
    if not book:
        return '【台股週報】模擬帳戶尚未開始，沒有可檢討的紀錄。'
    monday = (now.date() - timedelta(days=now.weekday())).isoformat()
    perf = paper.performance(book)
    bench = (store.meta('paper_performance') or {}).get('benchmark')
    lines = [f'【台股週報】{monday} ～ {now.date()}｜規則 {swing.VERSION}',
             f"模擬帳戶淨值 {perf['net_equity']:,.0f} 元（起始 {perf['starting_cash']:,.0f}，"
             f"{perf['total_return_pct']:+.2f}%，已扣手續費與證交稅）"]
    if bench:
        lines.append(f"同期全市場等權 {bench['equal_weight_pct']:+.2f}% → 超額 "
                     f"{perf['total_return_pct'] - bench['equal_weight_pct']:+.2f} 個百分點")
    lines.append(_market(store))
    week = [t for t in book.trades if t['day'] >= monday]
    bought = [t for t in week if t['side'] == 'buy']
    sold = [c for c in book.closed if c['exit_day'] >= monday]
    lines.append('')
    lines.append(f'本週買進 {len(bought)} 筆、賣出 {len(sold)} 筆')
    for t in bought:
        lines.append(f"・買 {t['name']} {t['symbol']} {t['shares']} 股 @ {t['price']:g}（{t['day']}）")
    for c in sold:
        lines.append(f"・賣 {c['name']} {c['symbol']} @ {c['exit']:g}｜{c['profit']:+,.0f} 元"
                     f"（{c['return_pct']:+.2f}%）｜{EXITS.get(c['exit_reason'], c['exit_reason'])}"
                     f"｜持有 {c.get('held_days') or '—'} 日")
    if book.positions:
        lines += ['', f'持倉 {len(book.positions)} 檔']
        lines += [_holding(p) for p in book.positions.values()]
    orders = (store.meta('paper_runner') or {}).get('orders') or []
    lines.append(f'掛單中 {len(orders)} 筆（明細見週日的下週計畫與網站）')
    lines += ['', f"規則實績：已結 {perf['closed_trades']} 筆"]
    if perf['closed_trades']:
        lines[-1] += (f"，勝率 {perf['win_rate_pct']:.0f}%"
                      + (f"，平均獲利 {perf['average_win_pct']:+.2f}%" if 'average_win_pct' in perf else '')
                      + (f"，平均虧損 {perf['average_loss_pct']:+.2f}%" if 'average_loss_pct' in perf else ''))
    lines.append(f"選定時的回測：{MEASURED['回測筆數']} 筆，勝率 {MEASURED['回測勝率百分比']}%，"
                 f"每筆平均 {MEASURED['回測每筆平均淨報酬百分比']:+.1f}%"
                 + ('（實績樣本還太少，尚不能比較）' if perf['closed_trades'] < 20 else ''))
    lines.append(SITE)
    return '\n'.join(lines)


def outlook(store, now):
    book = paper.Book.load(store)
    orders = (store.meta('paper_runner') or {}).get('orders') or []
    lines = [f'【下週台股計畫】{now.date()}｜規則 {swing.VERSION}', _market(store), '']
    if orders:
        lines.append(f'掛單 {len(orders)} 筆（布林下緣限價，成交後停損與目標如下）：')
        for o in sorted(orders, key=lambda o: o['placed']):
            lines.append(f"・{o['name']} {o['symbol']}｜掛 {o['limit']:g}｜停損 {o['stop']:g}"
                         f"｜目標先看 {o['target']:g}｜剩 {o['left']} 個交易日")
    else:
        lines.append('目前沒有掛單。')
    if book and book.positions:
        lines += ['', '持倉的出場條件：']
        lines += [_holding(p) for p in book.positions.values()]
    lines += ['', '下單依據與每檔的產業、法人、營收、本益比見網站。', SITE]
    text = '\n'.join(lines)
    if len(text) > 1900:
        # Discord takes one message; the orders are listed on the site in full.
        keep = lines[:3 + 1 + 15] + [f'…其餘 {max(0, len(orders) - 15)} 筆見網站。', SITE]
        text = '\n'.join(keep)
    return text


def build(kind, now, store):
    return weekly(store, now) if kind == 'weekly' else outlook(store, now)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('kind', choices=['weekly', 'outlook'])
    p.add_argument('--send', action='store_true')
    a = p.parse_args()
    d = Path.home() / 'kid-agent-data/stock-radar'
    s = Store(d / 'prod.db')
    now = datetime.now(TW)
    try:
        text = build(a.kind, now, s)
        atomic_json(d / (a.kind + '-latest.json'), {'generated_at': now.isoformat(), 'text': text})
        if a.send:
            print(json.dumps(_send_with_outbox(s, _bot_token(), text, channel=channel_for('review'), now=now,
                                               max_attempts=1)))
        else:
            print(text)
    finally:
        s.close()


if __name__ == '__main__':
    main()
