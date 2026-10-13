"""Enter at a reasonable level, leave at a reasonable level.

Exits are price levels that mean something: back to where it fell from, back to the 20-day average,
back to the 60-day high, the upper Bollinger band, or the P/E back at its own one-year median. Each
keeps a stop and a time limit so a trade cannot stay open forever. Entries are either the next open or
a limit resting at a support (20-day average or the lower Bollinger band) for five days, which only
fills if the price comes down to it. Random entries run through every exit as the control.
"""
import numpy as np, warnings; warnings.filterwarnings('ignore')
exec(open(__file__.replace('fair.py', 'study.py')).read().split("dates = list(range(205")[0])
sd20 = roll(cf, 20, np.nanstd)
bb_up = ma20 + 2 * sd20; bb_lo = ma20 - 2 * sd20
fns = {n: f for n, _, f in M}
ENTRIES = ['隨機', '長多短空超跌', '超跌+法人買', '趨勢回檔', '趨勢回檔+法人買', 'RSI2超賣', '低股價淨值比', '動能+法人買']
EXITS = {
    '抱20天(對照)':          dict(maxd=20),
    '漲回下跌前價位':          dict(level='pre', stop=.10, maxd=40),
    '回到月線':              dict(level='ma20', stop=.10, maxd=40),
    '回到60日高點':           dict(level='hi60', stop=.12, maxd=60),
    '碰布林上緣':             dict(level='bbup', stop=.10, maxd=40),
    '本益比回到自身中位':       dict(level='pe', stop=.15, maxd=60),
}
ENTRY_MODES = {'隔天開盤買': None, '掛月線等它跌到才買': 'ma20', '掛布林下緣等它跌到才買': 'bblo'}

def fill(t, j, mode):
    """(index, price) of the entry, or None if a resting limit never fills within five days."""
    if mode is None:
        return (t + 1, o[t + 1, j]) if o[t + 1, j] > 0 else None
    lim = ma20[t, j] if mode == 'ma20' else bb_lo[t, j]
    if not lim > 0: return None
    lim = min(lim, c[t, j])
    for k in range(t + 1, min(t + 6, T)):
        if l[k, j] <= lim: return (k, min(lim, o[k, j]) if o[k, j] > 0 else lim)
    return None

def level(e, t, k, j):
    lv = e.get('level')
    if lv == 'pre': return cf[max(t - 5, 0), j]          # the close before the five-day drop
    if lv == 'ma20': return ma20[k - 1, j]                 # known at the previous close
    if lv == 'hi60': return hi60[t, j]
    if lv == 'bbup': return bb_up[k - 1, j]
    return None

def trade(t, j, e, mode):
    f = fill(t, j, mode)
    if not f: return None
    k0, entry = f
    stop = entry * (1 - e['stop']) if 'stop' in e else None
    for k in range(k0, min(k0 + e['maxd'], T)):
        if np.isnan(c[k, j]): continue
        if stop and l[k, j] <= stop and k > k0:
            return min(stop, o[k, j] if o[k, j] > 0 else stop) / entry - 1 - COST, k - k0 + 1, 'stop'
        if e.get('level') == 'pe':
            if k > k0 and pe[k, j] > 0 and pe_med[k, j] > 0 and pe[k, j] >= pe_med[k, j]:
                return c[k, j] / entry - 1 - COST, k - k0 + 1, 'target'
            continue
        tg = level(e, t, k, j) if e.get('level') else None
        if tg and tg > entry * 1.006 and h[k, j] >= tg and k > k0:
            return max(tg, o[k, j] if o[k, j] > 0 else tg) / entry - 1 - COST, k - k0 + 1, 'target'
    k = min(k0 + e['maxd'] - 1, T - 1)
    return cf[k, j] / entry - 1 - COST, k - k0 + 1, 'time'

etf_ma = roll(etf[:, None], 120, np.nanmean)[:, 0]
span = list(range(205, T - 66))
picks = {}
for name in ENTRIES:
    picks[name] = []
    for t in span:
        if not (etf[t] > etf_ma[t]): continue
        u = universe(t); sc = np.where(u, fns[name](t), np.nan); ok = np.where(~np.isnan(sc))[0]
        picks[name] += [(t, j) for j in ok[np.argsort(-sc[ok])][:10]]
print(f"訊號 {days[span[0]]}~{days[span[-1]]}，每天前 10 名，大盤(0050)在 120 日線下不進場，每筆扣 0.585%")
print("格式：每筆平均% ｜勝率% ｜到達目標% ｜平均持有天 ｜每天%")
for mname, mode in ENTRY_MODES.items():
    print(f"\n######## 進場：{mname} ########")
    for ename, e in EXITS.items():
        print(f"-- 出場：{ename} --")
        for name in ENTRIES:
            rows = [trade(t, j, e, mode) for t, j in picks[name]]
            rows = [r for r in rows if r]
            if len(rows) < 50: continue
            r = np.array([x[0] for x in rows]); d = np.array([x[1] for x in rows])
            hit = np.mean([x[2] == 'target' for x in rows])
            print(f"   {name:<12}{r.mean()*100:6.2f} ｜{(r>0).mean()*100:5.1f} ｜{hit*100:5.1f} ｜{d.mean():5.1f} ｜{r.mean()/d.mean()*100:6.3f} （{len(r)}筆）")
