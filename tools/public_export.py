"""Export what the page shows: the one set of entry and exit rules, and the account that trades them.

The rules are swing.py: tonight's scan, the orders it proposes, and the simulated account that trades
them. Nothing else leaves the host -- the valuation rows decide nothing the account trades, and the
user asked on 2026-10-12 for one set of entry and exit rules.

Never private Fugle quotes: the scan and the account are built from public closing data. The payload is
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
    out={k:r[k] for k in ('schema_version','mode','generated_at','market_date','scan','paper') if k in r}
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
