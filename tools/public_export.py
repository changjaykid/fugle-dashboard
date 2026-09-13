import json,pathlib
p=pathlib.Path.home()/'kid-agent-os/services/stock-radar/docs/radar.json'
r=json.loads(p.read_text())
for i in r['items']:
 i['quote']=None
 i['signal'].update(status='pending',status_label='待估值',suggested=None,conservative=None,extreme=None,action='查看公開財務摘要，等待估值確認')
r['coverage']['quotes']=0
r['health'].append({'name':'公開頁範圍','status':'partial','detail':'公開財務摘要已接入；Fugle 報價的公開再發布權限尚未確認，公開頁暫不提供該報價。','as_of':r['generated_at']})
# Do not label private quote-source health as publicly available quotes.
r['health']=[x for x in r['health'] if x.get('name')!='試撮']
print(json.dumps(r,ensure_ascii=False))
