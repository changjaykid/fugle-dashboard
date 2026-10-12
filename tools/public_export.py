"""Export what the page shows: the one set of entry and exit rules, and the account that trades them.

The page used to carry two answers to "should I buy this". One was Strategy v1 -- setups, scores,
risk/reward, stops, targets, sizing -- which decides what the simulated account buys and what the
morning list says. The other was the valuation band: buy prices, the four-box filters, research
cards, 2,362 rows of it. That second one decided nothing the account traded, and on 2026-10-12 the
user asked for one set of entry and exit rules. So the valuation rows no longer leave the host; the
page shows v1 and the account, and the payload carries exactly that.

Never private Fugle quotes: v1 and the account are built from public closing data. The payload is
still encrypted before it is published (tools/encrypt_payload.py) -- GitHub Pages answers 200 to
anyone with the URL.
"""
import json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from stock_radar.store import Store
from stock_radar.domain import TW
from datetime import datetime

# Health rows that described the valuation pipeline, or private quotes, and so no longer belong here.
DROPPED_HEALTH=('試撮','報價','公開頁範圍','公開收盤','研究涵蓋')

def sanitize(r,s,now):
    out={k:r[k] for k in ('schema_version','mode','generated_at','market_date','v1','paper') if k in r}
    health=[h for h in r.get('health',[]) if h.get('name') not in DROPPED_HEALTH]
    sync=s.meta('public_close_sync') or {}
    health.append({'name':'公開收盤','status':'ok' if sync.get('at') else 'blocked',
                   'detail':'證交所開放資料；每筆顯示實際交易日，不是即時行情。','as_of':sync.get('at')})
    out['health']=health
    return out

def main():
    h=pathlib.Path.home();r=json.loads((h/'kid-agent-os/services/stock-radar/docs/radar.json').read_text());s=Store(h/'kid-agent-data/stock-radar/prod.db')
    try:print(json.dumps(sanitize(r,s,datetime.now(TW)),ensure_ascii=False,allow_nan=False))
    finally:s.close()
if __name__=='__main__':main()
