import json,pathlib,sys
from stock_radar.store import Store
h=pathlib.Path.home()
old=json.loads((h/'.openclaw/workspace/fugle-dashboard/engine/config.json').read_text())
# Preserve only explicit legacy watch flags; no sample valuations or quotes.
watch=set(old.get('watchlist',[]))
s=Store(h/'kid-agent-data/stock-radar/prod.db')
items=s.instruments()
for i in items:i['watched']=i['symbol'] in watch
s.upsert_instruments(items)
print(json.dumps({'watchlist_count':sum(i['watched'] for i in items),'source':'existing_engine_watchlist','imported_valuations':0,'imported_quotes':0}))
s.close()
