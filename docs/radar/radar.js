'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const labels = {sweet:'甜甜價',add:'加碼區',buy:'買進區',avoid:'不追',pending:'待估值',blocked:'暫停',stale:'資料不足／過期'};
  const positive = n => typeof n === 'number' && Number.isFinite(n) && n > 0;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const price = n => positive(n) ? n.toLocaleString('zh-TW',{maximumFractionDigits:2}) : '—';
  const timeValue = v => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) ? Date.parse(v) : NaN;
  const day = v => Number.isFinite(new Date(v).getTime()) ? new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(v)) : '';
  const dateText = v => v && Number.isFinite(timeValue(v)) ? new Date(v).toLocaleDateString('zh-TW',{timeZone:'Asia/Taipei'}) : '尚未估值';
  const clockText = v => v && Number.isFinite(timeValue(v)) ? new Date(v).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false}) : '尚未取得';
  const kindName = k => k === 'stock' ? '個股' : k?.startsWith('etf') ? 'ETF' : '其他';
  const safeURL = url => { try {const u = new URL(url); return u.protocol === 'https:' ? u.href : null;} catch {return null;} };
  let data = null, kind = 'all', selected = null, busy = false, loadError = null, visibleLimit = 100, lastFilter = '';
  function signal(item) {
    const s = {...(item.signal || {status:'pending',action:'等待估值'})};
    if (!labels[s.status]) {s.status='stale';s.action='未識別的資料狀態';}
    if (['sweet','add','buy'].includes(s.status)) {
      const now=Date.now(), expiry=timeValue(s.valid_until), calculated=timeValue(s.calculated_at);
      const v = item.valuation, q = item.quote;
      if (!positive(s.suggested) || !v || !positive(v.buy) || s.suggested > v.buy ||
          !Number.isFinite(expiry) || expiry<=now || !Number.isFinite(calculated) || calculated>now ||
          day(calculated)!==day(now) || !q || !Number.isFinite(timeValue(q.as_of)) || timeValue(q.as_of)>now || day(q.as_of)!==day(now) ||
          !Number.isFinite(timeValue(v.valid_until)) || timeValue(v.valid_until)<=now || v.evidence_reviewed!==true) {
        s.status='stale'; s.action='掛價已過期或資料不完整，等待新掃描';s.suggested=null;s.conservative=null;s.extreme=null;
      }
    }
    if (!['sweet','add','buy'].includes(s.status)) s.suggested=null;
    return s;
  }
  function stats(signals) {
    const counts = {sweet:0,add:0,buy:0,avoid:0,pending:0,blocked:0,stale:0};
    for (const i of data.items) counts[signals.get(i.symbol).status]++;
    const notes = {sweet:'已進入深度折價區',add:'已進入加碼定錨區',buy:'已進入第一層買進區',avoid:'高於基本面買進價'};
    $('stats').innerHTML = ['sweet','add','buy','avoid'].map(k => `<button class="stat" data-status="${k}" aria-pressed="${$('status').value===k}"><span class="stat-top">${labels[k]} <span aria-hidden="true">↗</span></span><strong>${counts[k]}<small>檔</small></strong><span class="stat-note">${notes[k]}</span></button>`).join('');
    $('stats').querySelectorAll('button').forEach(b => b.addEventListener('click',()=>{$('status').value=$('status').value===b.dataset.status?'all':b.dataset.status;render();}));
    const valid=counts.sweet+counts.add+counts.buy;
    const pending=counts.pending+counts.blocked+counts.stale;
    const notice=$('notice');notice.className='notice';
    if(loadError){notice.classList.add('error');notice.textContent=`${loadError}。目前保留上次資料，過期掛價會自動停用。`;}
    else if(data.mode==='simulation'){notice.textContent='測試資料｜此畫面用於驗證流程，所有測試價格均不可作為即時交易依據。';}
    else if(data.mode==='initializing'){notice.textContent='新雷達正在接入資料。下方先列既有觀察標的；尚未取得可驗證的估值與盤前試撮。';}
    else if(valid===0){notice.textContent=`目前沒有資料完整且仍有效的掛價。${pending.toLocaleString()} 檔待估值、待確認或資料過期；這不代表市場沒有機會。`;}
    else {notice.classList.add('ok');notice.textContent=`${valid} 檔具有有效掛價，${pending.toLocaleString()} 檔仍待研究或確認資料。試撮會變動，掛價以標示效期為準。`;}
  }
  function render() {
    if(!data)return;
    const signals=new Map(data.items.map(i=>[i.symbol,signal(i)]));
    stats(signals);
    const q=$('search').value.trim().toLowerCase(), status=$('status').value, industry=$('industry').value;
    const filter=JSON.stringify([q,status,industry,kind]);
    if(filter!==lastFilter){visibleLimit=100;lastFilter=filter;}
    const order={sweet:0,add:1,buy:2,avoid:3,blocked:4,pending:5,stale:6};
    const items=data.items.filter(i => (kind==='all'||(kind==='etf'?i.kind?.startsWith('etf'):i.kind==='stock')) &&
      (industry==='all'||i.industry===industry) && (status==='all'||signals.get(i.symbol).status===status) &&
      (!q||`${i.symbol} ${i.name}`.toLowerCase().includes(q))).sort((a,b)=>order[signals.get(a.symbol).status]-order[signals.get(b.symbol).status] || Number(!!b.watched)-Number(!!a.watched) || a.symbol.localeCompare(b.symbol));
    $('result-count').textContent=`顯示 ${Math.min(visibleLimit,items.length).toLocaleString()} / 符合 ${items.length.toLocaleString()} 檔 · 全部 ${data.items.length.toLocaleString()}`;
    $('load-more').hidden=items.length<=visibleLimit;
    $('empty').hidden=items.length>0;
    $('rows').innerHTML=items.slice(0,visibleLimit).map(i=>{
      const s=signals.get(i.symbol),v=i.valuation||{},q=i.quote||{};
      return `<tr><td><button class="stock-name" data-symbol="${esc(i.symbol)}">${esc(i.name||i.symbol)}<span class="code">${esc(i.symbol)}</span></button><div class="desc" title="${esc(i.description||'公司／ETF 介紹待補')} "><span class="kind">${kindName(i.kind)}</span>${esc(i.description||'介紹待補')}</div></td><td title="${esc(q.previous_close_date||q.trade_date||'資料日未提供')}">${price(q.previous_close)}</td><td title="${esc(clockText(q.trial_as_of||q.as_of))}">${price(q.trial_price)}</td><td class="suggest-cell ${positive(s.suggested)?'':'missing'}">${positive(s.suggested)?price(s.suggested):'暫不掛'}</td><td class="anchor">${price(v.sweet)}</td><td class="anchor">${price(v.add)}</td><td class="anchor">${price(v.buy)}</td><td><span class="tag ${s.status}">${labels[s.status]}</span><div class="action">${esc(s.action||s.reason||'等待確認')}</div></td><td class="date-cell">${dateText(v.as_of)}</td></tr>`;
    }).join('');
    $('rows').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.symbol)));
  }
  function sourceLinks(sources) {
    return sources?.length ? `<ul>${sources.map(s=>{const url=safeURL(s.url);return `<li>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(s.title||new URL(url).hostname)}</a>`:'來源網址未驗證'}${s.as_of?` · ${esc(dateText(s.as_of))}`:''}</li>`;}).join('')}</ul>` : '<p class="muted">尚未提供可查驗來源</p>';
  }
  function textSection(title,text) {return `<section><h3>${title}</h3><p>${esc(text||'尚未取得研究資料')}</p></section>`;}
  function listSection(title,list) {return `<section><h3>${title}</h3>${Array.isArray(list)&&list.length?`<ul>${list.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`:'<p>尚未取得研究資料</p>'}</section>`;}
  function detailHTML(i) {
    const q=i.quote||{}, v=i.valuation||{}, s=signal(i), r=i.research||{};
    const tiles=[['今日建議掛價',s.suggested],['昨日收盤',q.previous_close],['盤前試撮',q.trial_price],['甜甜價',v.sweet],['加碼價',v.add],['買進價',v.buy]];
    return `<div class="detail-head"><h2 id="detail-title">${esc(i.name||i.symbol)} <span class="code">${esc(i.symbol)} · ${kindName(i.kind)}</span></h2><p>${esc(i.description||'公司／ETF 介紹待補')}</p><span class="tag ${s.status}">${labels[s.status]}</span><p>${esc(s.action||s.reason)}</p></div><div class="detail-prices">${tiles.map(([label,n],j)=>`<div class="price-block ${j===0?'primary':''}"><span>${label}</span><strong>${price(n)}</strong></div>`).join('')}</div><p class="muted">行情時間 ${clockText(q.as_of)} · 來源 ${esc(q.source||'未取得')}<br>現價 ${price(q.price)} · 今日參考價 ${price(q.reference_price)}<br>掛價效期 ${s.suggested?clockText(s.valid_until):'目前無有效掛價'} · 估值日期 ${dateText(v.as_of)}</p><div class="research-grid">${textSection('為什麼看好',r.thesis||v.thesis)}${textSection('為什麼現在買／不買',r.why_now||s.reason)}${textSection('籌碼',r.chips)}${listSection('催化劑',r.catalysts)}${listSection('主要風險',r.risks)}${textSection('估值方法',v.method ? `${v.method}；${v.reason||''}`:null)}${textSection('其他承接位置',positive(s.suggested)?`保守撿價 ${price(s.conservative)}；極端承接 ${price(s.extreme)}。未有價格結構支持的欄位留空。`:null)}${textSection('不追與減碼',positive(v.avoid)||positive(v.sell)||positive(v.reduce)?`不追 ${price(v.avoid)}；開始賣 ${price(v.sell)}；強減碼 ${price(v.reduce)}`:null)}</div><section class="history"><h3>估值歷史</h3>${i.valuation_history?.length?i.valuation_history.map(h=>`<article><strong>${dateText(h.created_at)}<span class="tag">${esc({active:'已套用',proposed:'待套用',superseded:'歷史版本',rejected:'未採用'}[h.status]||h.status)}</span></strong><p>甜甜 ${price(h.payload?.sweet)} / 加碼 ${price(h.payload?.add)} / 買進 ${price(h.payload?.buy)}</p><p>${esc(h.payload?.reason||'未提供修改原因')}</p></article>`).join(''):'<p>尚無估值版本。研究提案套用後會保留修改原因。</p>'}</section><section class="sources"><h3>研究來源</h3>${sourceLinks(r.sources?.length?r.sources:v.sources)}</section>`;
  }
  function openDetail(symbol) {selected=symbol;const i=data.items.find(i=>i.symbol===symbol);if(!i)return;$('detail-content').innerHTML=detailHTML(i);if(!$('detail').open)$('detail').showModal();}
  function system() {
    const c=data.coverage||{};
    $('coverage').textContent=`母表 ${(c.universe??data.items.length).toLocaleString()} · 有估值 ${c.valued??0} · 有行情 ${c.quotes??0}`;
    const health=data.health?.length?data.health:[{name:'資料來源',status:'pending',detail:'等待主機提供完整資料狀態'}];
    $('health').innerHTML=health.map(h=>`<article class="health-card"><div class="health-title">${esc(h.name)}<span class="tag ${h.status==='ok'?'sweet':h.status==='blocked'||h.status==='error'?'blocked':''}">${esc({ok:'正常',blocked:'受阻',error:'異常',pending:'待確認',stale:'過期',partial:'部分完成'}[h.status]||'待確認')}</span></div><p>${esc(h.detail||'尚未提供說明')}</p>${h.as_of?`<p>${clockText(h.as_of)}</p>`:''}</article>`).join('');
  }
  async function load() {
    if(busy)return;busy=true;$('refresh').disabled=true;
    try {
      const res=await fetch(`radar.json?t=${Date.now()}`,{cache:'no-store',signal:AbortSignal.timeout(15000)});
      if(!res.ok)throw new Error(`雷達資料讀取失敗 (${res.status})`);
      const next=await res.json();
      if(next.schema_version!==1||!Array.isArray(next.items))throw new Error('資料格式不相容，等待主機更新');
      if(next.items.some(i=>!i||typeof i.symbol!=='string'))throw new Error('標的資料格式異常');
      data=next;loadError=null;
      const current=$('industry').value;
      $('industry').innerHTML='<option value="all">所有產業</option>'+[...new Set(data.items.map(i=>i.industry).filter(Boolean))].sort().map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');
      if([...$('industry').options].some(o=>o.value===current))$('industry').value=current;
      $('last-scan').textContent=`最後掃描 ${clockText(data.generated_at)}`;
      $('market-date').textContent=`${new Date().toLocaleDateString('zh-TW',{timeZone:'Asia/Taipei'})} · 台北`;
      render();system();if(selected&&$('detail').open)openDetail(selected);
    } catch(e) {
      loadError=e.message;
      if(data)render();
      $('notice').className='notice error';$('notice').textContent=`${e.message}。${data?'目前保留上次資料，過期掛價會自動停用。':'目前無可用資料，請稍後重新整理。'}`;
      if(!data){$('stats').innerHTML='';$('rows').innerHTML='';$('empty').hidden=false;$('empty').querySelector('h3').textContent='尚未取得雷達資料';}
    } finally {busy=false;$('refresh').disabled=false;}
  }
  $('refresh').addEventListener('click',load);
  $('load-more').addEventListener('click',()=>{visibleLimit+=100;render();});
  $('search').addEventListener('input',render);$('industry').addEventListener('change',render);$('status').addEventListener('change',render);
  document.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{kind=b.dataset.kind;document.querySelectorAll('[data-kind]').forEach(n=>n.setAttribute('aria-pressed',String(n===b)));render();}));
  $('clear-filters').addEventListener('click',()=>{$('search').value='';$('status').value='all';$('industry').value='all';document.querySelector('[data-kind="all"]').click();});
  $('close-detail').addEventListener('click',()=>$('detail').close());
  $('detail').addEventListener('close',()=>{selected=null;});
  setInterval(()=>{if(data){render();if(selected&&$('detail').open)openDetail(selected);}},30000);
  setInterval(load,60000);
  load();
})();
