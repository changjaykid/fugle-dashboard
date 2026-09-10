// Runs the actual dashboard script against a minimal DOM to verify public output.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const script=fs.readFileSync('docs/radar/radar.js','utf8');
const now=Date.parse('2026-09-10T08:50:00+08:00');
const iso=delta=>new Date(now+delta).toISOString();
const fixture=()=>({schema_version:1,mode:'simulation',generated_at:iso(0),items:[{symbol:'TEST',name:'<script>unsafe</script>',kind:'stock',quote:{as_of:iso(0)},valuation:{buy:100,valid_until:iso(3600000),evidence_reviewed:true},signal:{status:'buy',suggested:95,calculated_at:iso(0),valid_until:iso(120000)}}]});
async function app(payload){
  const nodes=new Map(),timers=[];
  function node(id){if(!nodes.has(id))nodes.set(id,{value:['industry','status'].includes(id)?'all':'',innerHTML:'',textContent:'',hidden:false,options:[{value:'all'}],handlers:{},classList:{add(){}},querySelectorAll(){return [];},querySelector(){return node(id+' child');},addEventListener(k,fn){this.handlers[k]=fn;}});return nodes.get(id);}
  let currentTime=now, response=payload, fail=false;
  class Clock extends Date {constructor(...args){super(...(args.length?args:[currentTime]));}static now(){return currentTime;}}
  const context={document:{getElementById:node,querySelectorAll:()=>[]},Date:Clock,Intl,URL,AbortSignal,console,setInterval:(fn,ms)=>timers.push({fn,ms}),fetch:async()=>{if(fail)throw Error('offline');return{ok:true,json:async()=>response};}};
  vm.runInNewContext(script,context);await new Promise(setImmediate);
  return {node,timers,advance:ms=>currentTime+=ms,fail:(v=true)=>fail=v,reload:async()=>{await node('refresh').handlers.click();},response:v=>response=v};
}
test('valid signal renders price and escapes external names',async()=>{const a=await app(fixture());assert.match(a.node('rows').innerHTML,/>95</);assert.doesNotMatch(a.node('rows').innerHTML,/<script>/);assert.match(a.node('notice').textContent,/測試資料/);});
test('signal expiry removes actionable price without network',async()=>{const a=await app(fixture());a.advance(121000);a.timers.find(t=>t.ms===30000).fn();assert.doesNotMatch(a.node('rows').innerHTML,/>95</);assert.match(a.node('rows').innerHTML,/暫不掛/);});
for(const [name,change] of Object.entries({aboveAnchor:d=>d.items[0].signal.suggested=101,expiredValuation:d=>d.items[0].valuation.valid_until=iso(-1),unreviewed:d=>d.items[0].valuation.evidence_reviewed=false,invalidTimestamp:d=>d.items[0].quote.as_of='2026-09-10',futureQuote:d=>d.items[0].quote.as_of=iso(1000)}))test(name+' cannot render actionable price',async()=>{const d=fixture();change(d);const a=await app(d);assert.match(a.node('rows').innerHTML,/暫不掛/);assert.doesNotMatch(a.node('rows').innerHTML,/>95</);});
test('refresh failure remains visible after timer and recovers on success',async()=>{const a=await app(fixture());a.fail();await a.reload();a.timers.find(t=>t.ms===30000).fn();assert.match(a.node('notice').textContent,/offline/);a.fail(false);await a.reload();assert.doesNotMatch(a.node('notice').textContent,/offline/);});
test('large universe is capped to 100 DOM rows and load more expands it',async()=>{const d=fixture();d.items=Array.from({length:2100},(_,i)=>({symbol:String(i),kind:'stock',signal:{status:'pending'}}));const a=await app(d);assert.equal((a.node('rows').innerHTML.match(/<tr>/g)||[]).length,100);a.node('load-more').handlers.click();assert.equal((a.node('rows').innerHTML.match(/<tr>/g)||[]).length,200);assert.equal(a.node('load-more').hidden,false);});
