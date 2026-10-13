"""Buy a relative low, sell into the bounce: does the exit rule make the difference?

Same data and costs as study.py. Signals every trading day, top 10 per day, entry at the next open.
Each entry is followed day by day until its exit rule fires. Stops and targets are checked against the
day's low and high, stop first when both are touched (a daily bar cannot say which came first). Random
entries go through the same exits as a control: an exit that makes random entries look good is the exit,
not the stock picking.
"""
import numpy as np, warnings; warnings.filterwarnings('ignore')
exec(open(__file__.replace("swing.py", "study.py")).read().split("dates = list(range(205")[0])   # reuse arrays + methods
ma5 = roll(cf, 5, np.nanmean)
rsi2_ = rsi2
COST = 0.00585
ENTRIES = ['隨機', '趨勢回檔', '趨勢回檔+法人買', '長多短空超跌', '超跌+法人買', 'RSI2超賣', '純超跌',
           '低股價淨值比', '動能+法人買', '突破整理區(v1型)']
fns = {n: f for n, _, f in M}
EXITS = {
    '抱20天': dict(maxd=20),
    '站上5日線就賣(最多10天)': dict(maxd=10, ma5=True),
    'RSI2>70就賣(最多10天)': dict(maxd=10, rsi=70),
    '到前20日高點賣/停損8%(最多20天)': dict(maxd=20, tgt='hi20', stop=.08),
    '+6%賣/−6%停損(最多10天)': dict(maxd=10, tp=.06, stop=.06),
    '+10%賣/−7%停損(最多20天)': dict(maxd=20, tp=.10, stop=.07),
}
def trade(t, j, e):
    entry = o[t + 1, j]
    if not entry > 0: return None
    stop = entry * (1 - e['stop']) if 'stop' in e else None
    tgt = entry * (1 + e['tp']) if 'tp' in e else (hi20[t, j] if e.get('tgt') == 'hi20' else None)
    if tgt is not None and tgt <= entry * 1.01: tgt = None if e.get('tgt') else tgt
    if e.get('tgt') == 'hi20' and tgt is None: return None      # no room to the prior high: not a trade
    for k in range(t + 1, min(t + 1 + e['maxd'], T)):
        lo_, hi_, cl = l[k, j], h[k, j], c[k, j]
        if np.isnan(cl): continue
        if stop and lo_ <= stop: return min(stop, o[k, j] if o[k, j] > 0 else stop) / entry - 1 - COST, k - t
        if tgt and hi_ >= tgt: return max(tgt, o[k, j] if k > t + 1 and o[k, j] > 0 else tgt) / entry - 1 - COST, k - t
        if e.get('ma5') and cl > ma5[k, j] and k > t + 1: return cl / entry - 1 - COST, k - t
        if e.get('rsi') and rsi2_[k, j] > e['rsi'] and k > t + 1: return cl / entry - 1 - COST, k - t
    k = min(t + e['maxd'], T - 1)
    return (cf[k, j] / entry - 1 - COST, k - t)
etf_ma = roll(etf[:, None], 120, np.nanmean)[:, 0]
days_ = list(range(205, T - 22))
picks = {}
for name in ENTRIES:
    picks[name] = []
    for t in days_:
        if not (etf[t] > etf_ma[t]): continue                  # bear filter on for all
        u = universe(t); sc = np.where(u, fns[name](t), np.nan); ok = np.where(~np.isnan(sc))[0]
        for j in ok[np.argsort(-sc[ok])][:10]: picks[name].append((t, j))
print(f"訊號 {days[days_[0]]}~{days[days_[-1]]}，每天前 10 名，0050 在 120 日線下不進場，每筆扣 0.585%")
print("每格：平均每筆% ｜勝率% ｜平均持有天數 ｜每持有一天賺%（越高代表資金效率越好）")
res = {}
for ename, e in EXITS.items():
    print(f"\n== 出場：{ename} ==")
    for name in ENTRIES:
        rows = [trade(t, j, e) for t, j in picks[name]]
        rows = [r for r in rows if r]
        if not rows: continue
        r = np.array([x[0] for x in rows]); d = np.array([x[1] for x in rows])
        yrs = {}
        for (t, _), x in zip([p for p in picks[name]][:len(rows)], r): pass
        res[(ename, name)] = (r.mean(), (r > 0).mean(), d.mean())
        print(f"  {name:<12} {r.mean()*100:6.2f} ｜{(r>0).mean()*100:5.1f} ｜{d.mean():5.1f}天 ｜{r.mean()/d.mean()*100:6.3f}  （{len(r)} 筆）")
