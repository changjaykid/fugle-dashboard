"""Nightly full-market scan for listed securities. Liquidity is the first gate: only symbols averaging
LIQUIDITY_LOTS a day go on to price structure and valuation. Stocks use the one-year P/E band
(valuation_engine.compute); ETFs have no earnings, so they use their own one-year price position. No LLM.
"""
import argparse,statistics
from datetime import date,datetime,timedelta
from pathlib import Path
from .domain import TW,floor_tick
from .store import Store
from . import market,valuation_engine

ETF_KINDS=('etf_equity','etf_other')
# Leveraged (L), inverse (R) and futures-based (U) ETFs decay over time, so a low price versus
# their own past is not cheap; they are never valued for buying.
DECAYING_SUFFIX=('L','R','U')
from .domain import ETF_PREMIUM_MAX
CANDIDATE_CAP=30  # Fugle trial-price checks at 08:50 (cli.FUGLE_CANDIDATE_CAP)

def _roc(ds):
    d=date.fromisoformat(ds);return f"{d.year-1911}年{d.month:02d}月{d.day:02d}日"

def technical(sym,prices,flows):
    """Same structure the buy plan reads for watched stocks, built from full-market history."""
    bars=[(d,rows[sym]) for d,rows in prices[-60:] if sym in rows]
    if len(bars)<20 or bars[-1][0]!=prices[-1][0]:return None
    lo=[b[2] for _,b in bars];hi=[b[1] for _,b in bars];cl=[b[3] for _,b in bars];ma=statistics.mean(cl[-20:])
    supports=sorted(set([min(lo[-20:])]+[lo[k] for k in range(max(1,len(bars)-20),len(bars)-1) if lo[k]<=lo[k-1] and lo[k]<lo[k+1]]))
    days=[(d,rows.get(sym,[0,0])) for d,rows in flows[-5:]]
    if len(days)<5 or days[-1][0]!=bars[-1][0]:return None
    foreign=sum(f[0] for _,f in days);trust=sum(f[1] for _,f in days)
    return {'as_of':bars[-1][0],'flow_as_of':days[-1][0],'flow_start':days[0][0],'ma20':round(ma,2),'support':min(lo[-20:]),'resistance':max(hi[-20:]),
            'low_60d':min(lo),'high_60d':max(hi),'supports':supports,'requires_stabilization':cl[-1]<ma and foreign+trust<0,
            'foreign_5d_lots':round(foreign,3),'trust_5d_lots':round(trust,3)}

def stock_valuation(inst,close,fin,ratios):
    """valuation_engine.compute on full-market daily ratios, reshaped into its per-stock row format."""
    rows=[[_roc(d),str(r[sym][2] or ''),'',str(r[sym][0] or '-'),str(r[sym][1] or '-'),''] for d,r in ratios if (sym:=inst['symbol']) in r]
    return valuation_engine.compute(inst,close,fin,{'months':{'all':rows}})

def etf_valuation(inst,close,prices):
    """Price position, not valuation: buy below the one-year median, add in the cheapest quarter, sweet in the cheapest tenth."""
    if not close:return None,'缺少收盤價'
    closes=[rows[inst['symbol']][3] for d,rows in prices if inst['symbol'] in rows and d<=close['trade_date']]
    if len(closes)<valuation_engine.MIN_SAMPLES:return None,f'上市交易不足 {valuation_engine.MIN_SAMPLES} 天，無法判斷一年價格位置'
    q=statistics.quantiles(closes,n=20);p10,p25,med=q[1],q[4],statistics.median(closes)
    v={'method':'etf_band','fair':round(med,2),'sweet':floor_tick(p10,'etf_equity'),'add':floor_tick(p25,'etf_equity'),'buy':floor_tick(med,'etf_equity'),
       'inputs':{'median':round(med,4),'p25':round(p25,4),'p10':round(p10,4),'samples':len(closes),'price_today':close['close']},
       'reason':(f"ETF 沒有獲利，不用本益比；改看自己過去一年 {len(closes)} 個交易日的價格位置：中位數 {med:.2f}、最便宜 25% 在 {p25:.2f} 以下、最便宜 10% 在 {p10:.2f} 以下。"
                 f"買進區＝低於中位數，加碼區＝最便宜 25%，甜甜價＝最便宜 10%。這是價格位置，不是估值；盤前另檢查市價不得高於淨值 {ETF_PREMIUM_MAX:g}% 以上。")}
    return v,None

def discount(v,close):
    """How far below its own one-year norm a symbol trades; the ranking key for 'cheapest first'."""
    i=(v or {}).get('inputs') or {}
    for today,norm in (('pe_today','fair_pe'),('pb_today','fair_pb')):
        if i.get(today) and i.get(norm):return 1-i[today]/i[norm]
    return 1-close/v['fair'] if v and v.get('fair') and close else None

def candidates(s,liquid,now):
    """Only a plan that already passed on the close can become a buy at 08:50 (gate_signal), so those,
    cheapest first, are what the limited live trial-price check is spent on; then stabilizing ones, then watched."""
    from .buy_plan import store_plan
    ranked={'conditional':[],'wait_stabilize':[]}
    for inst in liquid:
        v=s.active(inst['symbol'])
        if not v:continue
        p=store_plan(s,inst,now)
        if p['status'] in ranked:ranked[p['status']].append((-(discount(v,(s.fact(inst['symbol'],'public_close') or {}).get('close')) or 0),inst['symbol']))
    order=[sym for st in ('conditional','wait_stabilize') for _,sym in sorted(ranked[st])]
    order+=[i['symbol'] for i in s.instruments() if i.get('watched') and i['symbol'] not in order]
    s.set_meta('morning_candidates',{'as_of':now.isoformat(),'symbols':order[:CANDIDATE_CAP],'conditional':len(ranked['conditional']),'wait_stabilize':len(ranked['wait_stabilize'])})
    return order[:CANDIDATE_CAP]

def scan(s,now):
    prices=market.load(s,'prices');ratios=market.load(s,'ratios');flows=market.load(s,'flows',5)
    if not prices:return {'error':'尚無全市場價格資料'}
    liq=market.liquidity(prices);s.set_meta('liquidity',{'as_of':prices[-1][0],'min_lots':market.LIQUIDITY_LOTS,'lots':liq})
    targets=[i for i in s.instruments() if i.get('market')=='TSE' and (i['kind']=='stock' or (i['kind'] in ETF_KINDS and not i['symbol'].endswith(DECAYING_SUFFIX)))]
    liquid=[i for i in targets if liq.get(i['symbol'],0)>=market.LIQUIDITY_LOTS]
    built=[]
    for inst in liquid:
        sym=inst['symbol'];t=technical(sym,prices,flows)
        if t:s.set_fact(sym,'technical',t)
        close=s.fact(sym,'public_close')
        if inst['kind']=='stock':v,why=stock_valuation(inst,close,s.fact(sym,'financials'),ratios)
        else:v,why=etf_valuation(inst,close,prices)
        if v:v=valuation_engine.finish(s,inst,v,now,source=f"{market.URLS['prices' if inst['kind'] in ETF_KINDS else 'ratios']}?response=json&date={prices[-1][0].replace('-','')}")
        built.append((inst,v,why))
    out=valuation_engine.publish_all(s,now,built)
    cands=candidates(s,liquid,now)
    summary={'as_of':prices[-1][0],'listed':len(targets),'liquid':len(liquid),**{k:sum(1 for r in out if r[1]==k) for k in ('published','same','skip','error')},'candidates':len(cands)}
    s.set_meta('market_scan',{**summary,'at':now.isoformat()})
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,required=True);a=p.parse_args()
    s=Store(a.db)
    try:print(scan(s,datetime.now(TW)))
    finally:s.close()
if __name__=='__main__':main()
