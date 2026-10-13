"""Load five years of cached TWSE data into aligned numpy arrays once, and save them as .npz."""
import sys, json, numpy as np
sys.path.insert(0, '/Users/kid/kid-agent-os/services/stock-radar')
from stock_radar import market
from stock_radar.store import Store
s = Store('/Users/kid/kid-agent-data/stock-radar/prod.db')
prices = market.split_adjust(market.load(s, 'prices'))
flows = dict(market.load(s, 'flows'))
ratios = dict(market.load(s, 'ratios'))
insts = {i['symbol']: i for i in s.instruments() if i['kind'] == 'stock'}
days = [d for d, _ in prices]
syms = sorted(insts)
ix = {x: j for j, x in enumerate(syms)}
T, N = len(days), len(syms)
A = {k: np.full((T, N), np.nan) for k in ('o', 'h', 'l', 'c', 'v', 'fi', 'tr', 'pe', 'pb', 'dy')}
for t, (d, table) in enumerate(prices):
    for x, b in table.items():
        j = ix.get(x)
        if j is None or not b or not b[3]: continue
        A['o'][t, j] = b[0] or b[3]; A['h'][t, j] = b[1] or b[3]; A['l'][t, j] = b[2] or b[3]
        A['c'][t, j] = b[3]; A['v'][t, j] = b[4] or 0
    for x, f in (flows.get(d) or {}).items():
        j = ix.get(x)
        if j is not None: A['fi'][t, j] = f[0] or 0; A['tr'][t, j] = f[1] or 0
    for x, r in (ratios.get(d) or {}).items():
        j = ix.get(x)
        if j is not None:
            A['pe'][t, j] = r[0] if r[0] else np.nan; A['pb'][t, j] = r[1] if r[1] else np.nan
            A['dy'][t, j] = r[2] if r[2] is not None else np.nan
# 0050 for the benchmark
etf = np.array([(tb.get('0050') or [np.nan]*4)[3] for _, tb in prices], dtype=float)
etfo = np.array([(tb.get('0050') or [np.nan]*4)[0] or np.nan for _, tb in prices], dtype=float)
ind = np.array([insts[x].get('industry_code') or insts[x].get('industry') or '' for x in syms])
np.savez_compressed('/tmp/claude-study/data.npz', days=np.array(days), syms=np.array(syms), ind=ind,
                    etf=etf, etfo=etfo, **A)
print(T, N, days[0], days[-1], 'flows days', sum(1 for d in days if d in flows))
