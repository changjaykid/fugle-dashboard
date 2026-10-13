"""Many ways of picking Taiwan stocks, measured the same way.

Signal on day t's close. Buy at t+1's open. Sell at t+H's close. Every return is net of a full round trip
(0.1425% fee each side, no discount, + 0.3% tax) = 0.585%. Benchmarks: the equal-weight liquid universe
bought and sold the same way, and 0050. Signal dates every 5 trading days. Top 10 per method per date.
"""
import json, sys, numpy as np
import warnings; warnings.filterwarnings('ignore')
D = np.load('/tmp/claude-study/data.npz')
days = D['days']; o, h, l, c, v = D['o'], D['h'], D['l'], D['c'], D['v']
fi, tr, pe, pb, dy = D['fi'], D['tr'], D['pe'], D['pb'], D['dy']
etf, etfo = D['etf'], D['etfo']
T, N = c.shape
COST = 0.00585
TOP = 10
HS = (5, 10, 20)

def roll(a, n, fn):
    out = np.full_like(a, np.nan)
    for t in range(n - 1, len(a)):
        out[t] = fn(a[t - n + 1:t + 1], axis=0)
    return out

def shift(a, n):
    out = np.full_like(a, np.nan); out[n:] = a[:-n]; return out

cf = c.copy()
# forward-fill closes for rolling stats on suspended days
for t in range(1, T):
    m = np.isnan(cf[t]); cf[t, m] = cf[t - 1, m]
dv = c * v * 1000                      # NT$ turnover
vz = np.where(np.isnan(v), 0, v)
ma20 = roll(cf, 20, np.nanmean); ma60 = roll(cf, 60, np.nanmean)
ma120 = roll(cf, 120, np.nanmean); ma200 = roll(cf, 200, np.nanmean)
hi20 = roll(h, 20, np.nanmax); hi60 = roll(h, 60, np.nanmax); lo60 = roll(l, 60, np.nanmin)
lo20 = roll(l, 20, np.nanmin)
hi_base = shift(roll(h, 55, np.nanmax), 5); lo_base = shift(roll(l, 55, np.nanmin), 5)
dv20 = roll(dv, 20, np.nanmean)
vmed20 = shift(roll(vz, 20, np.nanmedian), 1)
v10 = roll(vz, 10, np.nanmean); vmed60 = roll(vz, 60, np.nanmedian)
r5 = cf / shift(cf, 5) - 1; r20 = cf / shift(cf, 20) - 1; r60 = cf / shift(cf, 60) - 1
r1 = cf / shift(cf, 1) - 1
tr_ = np.maximum(h - l, np.maximum(abs(h - shift(cf, 1)), abs(l - shift(cf, 1))))
atr = roll(tr_, 14, np.nanmean)
fi0 = np.nan_to_num(fi); tr0 = np.nan_to_num(tr)
fi5 = roll(fi0, 5, np.sum); tr5 = roll(tr0, 5, np.sum); vol5 = roll(vz, 5, np.sum)
fi20 = roll(fi0, 20, np.sum); tr20 = roll(tr0, 20, np.sum); vol20 = roll(vz, 20, np.sum)
fi_days5 = roll((fi0 > 0).astype(float), 5, np.sum); tr_days5 = roll((tr0 > 0).astype(float), 5, np.sum)
fi3 = roll(fi0 + tr0, 3, np.sum)
pe_med = roll(pe, 250, np.nanmedian)
# RSI(2)
d1 = cf - shift(cf, 1)
up = roll(np.clip(np.nan_to_num(d1), 0, None), 2, np.mean); dn = roll(np.clip(-np.nan_to_num(d1), 0, None), 2, np.mean)
rsi2 = 100 - 100 / (1 + up / np.where(dn == 0, 1e-9, dn))
mkt = np.nanmean(np.where(dv20 >= 2e7, r60, np.nan), axis=1)[:, None]   # market 60d return (liquid EW)
mkt20 = np.nanmean(np.where(dv20 >= 2e7, r20, np.nan), axis=1)[:, None]
rng_pos = (cf - lo60) / (hi60 - lo60)
relvol = vz / vmed20

def universe(t):
    return (dv20[t] >= 2e7) & ~np.isnan(c[t]) & ~np.isnan(ma120[t]) & (c[t] >= 10)

# Each method: (name, idea, function t -> score array with NaN where not eligible; higher is better)
M = []
def method(name, idea):
    def wrap(fn): M.append((name, idea, fn)); return fn
    return wrap

@method('隨機', '基準：每次隨機選 10 檔')
def _(t): return np.random.default_rng(t).random(N)
@method('20日動能', '近 20 日漲最多')
def _(t): return r20[t]
@method('60日區間高位', '收盤在 60 日區間 80% 以上、離高點 10% 內，越接近高點越前面')
def _(t): return np.where((rng_pos[t] >= .8) & ((hi60[t] - c[t]) / hi60[t] <= .1), rng_pos[t], np.nan)
@method('突破整理區(v1型)', '突破前 55 日整理區上緣、區間寬 ≤30%、量 ≥1.4 倍、離突破點 ≤3 ATR')
def _(t):
    w = (hi_base[t] - lo_base[t]) / hi_base[t]
    ok = (c[t] > hi_base[t]) & (w <= .30) & (relvol[t] >= 1.4) & ((c[t] - hi_base[t]) / atr[t] <= 3)
    return np.where(ok, relvol[t], np.nan)
@method('爆量長紅', '量 ≥2.5 倍、當天漲 ≥3%、收在當日高檔')
def _(t):
    ok = (relvol[t] >= 2.5) & (r1[t] >= .03) & ((c[t] - l[t]) / (h[t] - l[t] + 1e-9) >= .75)
    return np.where(ok, relvol[t], np.nan)
@method('趨勢回檔', '季線上、月線>季線、60日強於大盤；自20日高點回檔3–10%、貼近月線(±3%)、量縮')
def _(t):
    dd = (hi20[t] - c[t]) / hi20[t]
    ok = (c[t] > ma60[t]) & (ma20[t] > ma60[t]) & (r60[t] > mkt[t]) & (dd >= .03) & (dd <= .10) \
         & (abs(c[t] / ma20[t] - 1) <= .03) & (relvol[t] <= 1.0)
    return np.where(ok, r60[t] - mkt[t, 0], np.nan)
@method('趨勢回檔+法人買', '趨勢回檔，且回檔期間（近5日）外資+投信合計買超')
def _(t):
    base = M[5][2](t)
    return np.where(~np.isnan(base) & (fi5[t] + tr5[t] > 0), (fi5[t] + tr5[t]) / vol5[t], np.nan)
@method('外資連買', '近5日外資買超 ≥4 天，按買超佔成交量排序')
def _(t): return np.where(fi_days5[t] >= 4, fi5[t] / vol5[t], np.nan)
@method('投信連買', '近5日投信買超 ≥3 天，按買超佔成交量排序')
def _(t): return np.where(tr_days5[t] >= 3, tr5[t] / vol5[t], np.nan)
@method('外資+投信同買', '近5日外資、投信都是淨買超，按合計佔成交量排序')
def _(t): return np.where((fi5[t] > 0) & (tr5[t] > 0), (fi5[t] + tr5[t]) / vol5[t], np.nan)
@method('法人由賣轉買', '近20日法人淨賣，但最近3日轉為淨買')
def _(t): return np.where((fi20[t] + tr20[t] < 0) & (fi3[t] > 0), fi3[t] / vol5[t], np.nan)
@method('量縮整理', '季線上，近10日量 ≤60日中位的 60%、10日振幅 ≤8%（蓄勢）')
def _(t):
    rg = (roll_h10[t] - roll_l10[t]) / c[t]
    ok = (c[t] > ma60[t]) & (v10[t] <= .6 * vmed60[t]) & (rg <= .08)
    return np.where(ok, -rg, np.nan)
@method('長多短空超跌', '站上120日線（長期多頭）但近5日跌 ≥8%，跌越多越前面')
def _(t): return np.where((c[t] > ma120[t]) & (r5[t] <= -.08), -r5[t], np.nan)
@method('RSI2超賣', '站上200日線、RSI(2) < 10（短線急跌）')
def _(t): return np.where((c[t] > ma200[t]) & (rsi2[t] < 10), -rsi2[t], np.nan)
@method('純超跌', '近5日跌最多（不看趨勢）')
def _(t): return -r5[t]
@method('低本益比(相對自身)', '本益比低於自己一年中位數最多（舊估值法）')
def _(t): return np.where(pe[t] > 0, 1 - pe[t] / pe_med[t], np.nan)
@method('低股價淨值比', '股價淨值比最低（有獲利）')
def _(t): return np.where((pe[t] > 0) & (pb[t] > 0), -pb[t], np.nan)
@method('高殖利率', '殖利率最高')
def _(t): return dy[t]
@method('低波動', 'ATR/股價最低')
def _(t): return -atr[t] / c[t]
@method('強勢+低波動', '60日強於大盤的股票中，波動最低')
def _(t): return np.where(r60[t] > mkt[t], -atr[t] / c[t], np.nan)

@method('動能+法人買', '20日漲幅前段且季線上，近5日外資+投信淨買超，按20日漲幅排序')
def _(t): return np.where((c[t] > ma60[t]) & (fi5[t] + tr5[t] > 0), r20[t], np.nan)
@method('超跌+法人買', '長期多頭(120日線上)近5日跌≥8%，且法人近5日淨買超')
def _(t): return np.where((c[t] > ma120[t]) & (r5[t] <= -.08) & (fi5[t] + tr5[t] > 0), -r5[t], np.nan)
@method('低淨值比+季線上', '股價淨值比低、有獲利、且站上季線（便宜但不在跌）')
def _(t): return np.where((pe[t] > 0) & (pb[t] > 0) & (c[t] > ma60[t]), -pb[t], np.nan)
@method('低淨值比+法人買', '股價淨值比低、有獲利，近5日法人淨買超')
def _(t): return np.where((pe[t] > 0) & (pb[t] > 0) & (fi5[t] + tr5[t] > 0), -pb[t], np.nan)

roll_h10 = roll(h, 10, np.nanmax); roll_l10 = roll(l, 10, np.nanmin)

def fwd(t, H, mask):
    """Net return buying t+1 open, selling t+H close, for the masked symbols."""
    if t + H >= T: return None
    e = o[t + 1]; x = cf[t + H]
    r = x / e - 1 - COST
    return np.where(mask & ~np.isnan(e) & (e > 0), r, np.nan)

dates = list(range(205, T - max(HS) - 1, 5))
res = {name: {H: [] for H in HS} for name, _, _ in M}
bench = {H: [] for H in HS}; etfb = {H: [] for H in HS}
for t in dates:
    u = universe(t)
    for H in HS:
        ru = fwd(t, H, u); bench[H].append(np.nanmean(ru))
        etfb[H].append(etf[t + H] / (etfo[t + 1] if etfo[t + 1] > 0 else etf[t]) - 1 - 0.00385)   # ETF tax 0.1%
    for name, _, fn in M:
        sc = np.where(u, fn(t), np.nan)
        ok = np.where(~np.isnan(sc))[0]
        pick = ok[np.argsort(-sc[ok])][:TOP] if len(ok) else []
        for H in HS:
            if len(pick) == 0:
                res[name][H].append(None); continue
            m = np.zeros(N, bool); m[pick] = True
            rr = fwd(t, H, m)[pick]
            rr = rr[~np.isnan(rr)]
            res[name][H].append((float(np.mean(rr)), [float(x) for x in rr], int(len(pick))) if len(rr) else None)

years = np.array([days[t][:4] for t in dates])
etf_ma = roll(etf[:, None], 120, np.nanmean)[:, 0]
bull = np.array([etf[t] > etf_ma[t] for t in dates])
out = {'dates': len(dates), 'from': str(days[dates[0]]), 'to': str(days[dates[-1]]), 'methods': []}
for name, idea, _ in M:
    row = {'name': name, 'idea': idea}
    for H in HS:
        pairs = [(i, r) for i, r in enumerate(res[name][H]) if r]
        if not pairs:
            continue
        port = np.array([r[0] for _, r in pairs]); bm = np.array([bench[H][i] for i, _ in pairs])
        allr = np.concatenate([r[1] for _, r in pairs])
        ex = port - bm
        yr = {}
        for y in sorted(set(years)):
            sel = [k for k, (i, _) in enumerate(pairs) if years[i] == y]
            if sel: yr[y] = round(float(np.mean(ex[sel])) * 100, 2)
        b = np.array([bull[i] for i, _ in pairs]); eb = np.array([etfb[H][i] for i, _ in pairs])
        neff = max(len(ex) / max(H / 5, 1), 2)
        tstat = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(neff))) if ex.std() > 0 else 0.0
        row[H] = {'t': round(tstat, 2), 'vs0050': round(float(np.nanmean(port - eb)) * 100, 2),
                  'bull': round(float(port[b].mean()) * 100, 2) if b.any() else None,
                  'bear': round(float(port[~b].mean()) * 100, 2) if (~b).any() else None,
                  'dates_traded': len(pairs), 'avg_picks': round(float(np.mean([r[2] for _, r in pairs])), 1),
                  'mean': round(float(port.mean()) * 100, 2), 'median_trade': round(float(np.median(allr)) * 100, 2),
                  'win': round(float((allr > 0).mean()) * 100, 1), 'excess': round(float(ex.mean()) * 100, 2),
                  'beat_pct': round(float((ex > 0).mean()) * 100, 1), 'excess_by_year': {str(k): v for k, v in yr.items()}}
    out['methods'].append(row)
out['bench'] = {H: round(float(np.nanmean(bench[H])) * 100, 2) for H in HS}
out['etf0050'] = {H: round(float(np.nanmean(etfb[H])) * 100, 2) for H in HS}
json.dump(out, open('/tmp/claude-study/result.json', 'w'), ensure_ascii=False, indent=1)

nb = int(bull.sum())
print(f"{out['dates']} 訊號日 {out['from']}~{out['to']}｜多頭(0050在120日線上) {nb} 日、空頭 {len(dates)-nb} 日")
print(f"等權大盤 {out['bench']}｜0050 {out['etf0050']}")
for H in HS:
    e = np.array(etfb[H]); print(f"  0050 持有{H}日 多頭時 {np.nanmean(e[bull])*100:.2f} 空頭時 {np.nanmean(e[~bull])*100:.2f}")
for H in (10, 20):
    print(f"\n== 持有 {H} 日（每筆已扣成本）==")
    print(f"{'方法':<12}{'平均':>6}{'勝率':>6}{'超額':>6}{'t值':>6}{'贏大盤%':>7}{'對0050':>7}{'多頭時':>7}{'空頭時':>7}  各年超額")
    for r in sorted(out['methods'], key=lambda r: -(r.get(H) or {}).get('excess', -99)):
        x = r.get(H)
        if not x: continue
        yr = ' '.join(f"{k[2:]}:{v:+.1f}" for k, v in x['excess_by_year'].items())
        print(f"{r['name']:<12}{x['mean']:>6}{x['win']:>6}{x['excess']:>6}{x['t']:>6}{x['beat_pct']:>7}{x['vs0050']:>7}{x['bull']:>7}{x['bear']:>7}  {yr}")

# ---- Account simulation: rebalance every 20 trading days into the top 10, compounding --------------
print("\n== 帳戶模擬：每 20 個交易日換一次股、前 10 名等權、複利、已扣成本 ==")
reb = list(range(205, T - 21, 20))
yrs = (len(reb) * 20) / 245
def sim(fn, gate):
    global reb, yrs
    eq = [1.0]
    for t in reb:
        if gate and not (etf[t] > etf_ma[t]):
            eq.append(eq[-1]); continue
        if fn is None:
            r = etf[t + 20] / (etfo[t + 1] if etfo[t + 1] > 0 else etf[t]) - 1 - 0.00385
        else:
            u = universe(t); sc = np.where(u, fn(t), np.nan); ok = np.where(~np.isnan(sc))[0]
            pick = ok[np.argsort(-sc[ok])][:TOP]
            if len(pick) == 0: eq.append(eq[-1]); continue
            m = np.zeros(N, bool); m[pick] = True
            rr = fwd(t, 20, m)[pick]; r = float(np.nanmean(rr)) if np.isfinite(rr).any() else 0.0
        eq.append(eq[-1] * (1 + r))
    eq = np.array(eq); peak = np.maximum.accumulate(eq); dd = float((eq / peak - 1).min())
    return eq[-1] - 1, (eq[-1] ** (1 / yrs) - 1), dd

print("（4 種不同起始日：第 0、5、10、15 天開始，取年化報酬的 最差 / 中位 / 最好，最大回撤取最差）")
print(f"{'方法':<12}{'年化 最差/中位/最好':>22}{'最大回撤':>8}   {'空頭不進場 年化 最差/中位/最好':>24}{'最大回撤':>8}")
rows = [('0050 買進持有', None)] + [(n, f) for n, _, f in M]
for name, fn in rows:
    A, B = [], []
    for off in (0, 5, 10, 15):
        reb = list(range(205 + off, T - 21, 20)); yrs = len(reb) * 20 / 245
        A.append(sim(fn, False)); B.append(sim(fn, True))
    fa = sorted(x[1] * 100 for x in A); fb = sorted(x[1] * 100 for x in B)
    print(f"{name:<12}{fa[0]:>8.1f}{(fa[1]+fa[2])/2:>7.1f}{fa[-1]:>7.1f}{min(x[2] for x in A)*100:>8.1f}%   "
          f"{fb[0]:>12.1f}{(fb[1]+fb[2])/2:>7.1f}{fb[-1]:>7.1f}{min(x[2] for x in B)*100:>8.1f}%")
