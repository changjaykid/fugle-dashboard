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
  const quoteToday = q => q?.trade_date === day(Date.now());
  const publicClose = i => {const c=i.public_close;return c && /^\d{4}-\d{2}-\d{2}$/.test(c.trade_date||'') && c.trade_date<=day(Date.now()) && positive(c.close)?c:null;};
  const kindName = k => k === 'stock' ? '個股' : k?.startsWith('etf') ? 'ETF' : '其他';
  const safeURL = url => { try {const u = new URL(url); return u.protocol === 'https:' ? u.href : null;} catch {return null;} };
  let data = null, scope = 'all', favorites = null, kind = 'all', selected = null, busy = false, loadError = null, visibleLimit = 100, lastFilter = '';
  const favoriteKey='kid-stock-favorites-v1';
  function initFavorites() {
    if(favorites!==null)return;
    try {const saved=JSON.parse(localStorage.getItem(favoriteKey));if(Array.isArray(saved)){favorites=new Set(saved.filter(x=>typeof x==='string'));return;}}catch{}
    favorites=new Set(data.items.filter(i=>i.watched).map(i=>i.symbol));
  }
  function star(i) {const yes=favorites?.has(i.symbol);return `<button class="favorite" data-favorite="${esc(i.symbol)}" aria-pressed="${!!yes}" aria-label="${yes?'取消關注':'加入關注'} ${esc(i.name||i.symbol)}">${yes?'★':'☆'}</button>`;}
  function bindStars(root) {root.querySelectorAll('[data-favorite]').forEach(b=>b.addEventListener('click',()=>{const symbol=b.dataset.favorite;if(favorites.has(symbol))favorites.delete(symbol);else favorites.add(symbol);try{localStorage.setItem(favoriteKey,JSON.stringify([...favorites]));}catch{}render();if(selected&&$('detail').open)openDetail(selected);}));}
  function jumpToResults() {$('radar-section')?.scrollIntoView?.({behavior:'smooth',block:'start'});$('radar-title')?.focus?.({preventScroll:true});}
  function basicHTML(i) {
    const f=i.fundamentals;if(!f)return '<p>尚未取得此標的的基本面資料；未完成估值，不提供買進價。</p>';
    const num=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-TW',{maximumFractionDigits:2}):'—';
    return `<section class="fundamentals"><h3>已公布基本面</h3><p>${esc(f.income_period||'財報期別未提供')}：EPS ${num(f.eps)} 元、營業利益率 ${num(f.operating_margin)}%。</p><p>${esc(f.revenue_period||'營收期別未提供')}：月營收 ${num(f.monthly_revenue_100m)} 億元、年增 ${num(f.revenue_yoy)}%。</p><p>${esc(f.reading)}</p><p class="muted">這是依公開數字整理的初步觀察，尚不等於完整估值或買進推薦。</p>${sourceLinks(f.sources)}</section>`;
  }
  function purchaseReasons(i) {
    const reasons=i.research_detail?.buy_reasons;
    if(!reasons?.length)return '';
    return `<section class="buy-reasons"><h4>值得考慮的理由</h4><ul>${reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul><p><strong>仍需確認：</strong>${esc(i.research_detail?.buy_caveat||'需符合盤前行情與風險條件')}</p></section>`;
  }
  function plan(i) {
    const p=i.plan,c=publicClose(i),v=i.valuation;
    if(!p || !v || !c || v.evidence_reviewed!==true || !Number.isFinite(timeValue(v.as_of)) || timeValue(v.as_of)>Date.now() || timeValue(p.valid_until)<=Date.now() || !Number.isFinite(timeValue(p.valid_until)) || timeValue(v.valid_until)<=Date.now() || c.trade_date!==p.close_date || !positive(p.buy_max) || p.buy_max!==v.buy || (p.entry!=null && (!positive(p.entry)||p.entry>v.buy||p.entry>c.close)))return null;
    return p;
  }
  const displaySignal=i=>{const p=plan(i);return p?{...signal(i),status:p.band,action:p.reason}:signal(i);};
  function signal(item) {
    const s = {...(item.signal || {status:'pending',action:'等待估值'})};
    if (!labels[s.status]) {s.status='stale';s.action='未識別的資料狀態';}
    if (['sweet','add','buy'].includes(s.status)) {
      const now=Date.now(), expiry=timeValue(s.valid_until), calculated=timeValue(s.calculated_at);
      const v = item.valuation, q = item.quote;
      const marketPrice=q?.is_trial===true?q.trial_price:q?.is_trial===false?q.price:null;
      if (!positive(s.suggested) || !v || !positive(v.buy) || s.suggested > v.buy ||
          !positive(marketPrice) || s.suggested > marketPrice ||
          !Number.isFinite(expiry) || expiry<=now || !Number.isFinite(calculated) || calculated>now ||
          day(calculated)!==day(now) || !q || !Number.isFinite(timeValue(q.as_of)) || timeValue(q.as_of)>now || day(q.as_of)!==day(now) ||
          !quoteToday(q) || now-timeValue(q.as_of)>120000 || !Number.isFinite(timeValue(q.book_as_of)) || timeValue(q.book_as_of)>now || now-timeValue(q.book_as_of)>120000 ||
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
    $('stats').querySelectorAll('button').forEach(b => b.addEventListener('click',()=>{$('status').value=$('status').value===b.dataset.status?'all':b.dataset.status;scope='all';kind='all';$('search').value='';$('industry').value='all';render();jumpToResults();}));
    const valid=counts.sweet+counts.add+counts.buy;
    const pending=counts.pending+counts.blocked+counts.stale;
    const notice=$('notice');notice.className='notice';
    if(loadError){notice.classList.add('error');notice.textContent=`${loadError}。目前保留上次資料，過期掛價會自動停用。`;}
    else if(data.mode==='simulation'){notice.textContent='測試資料｜此畫面用於驗證流程，所有測試價格均不可作為即時交易依據。';}
    else if(data.mode==='initializing'){notice.textContent='新雷達正在接入資料。下方先列既有觀察標的；尚未取得可驗證的估值與盤前試撮。';}
    else {notice.textContent=`${valid} 檔收盤價位於分析買區。四格依最近收盤分類；下方列承接計畫與等待條件，當日掛價以 08:50 Discord 通知為準。`;}

  }
  function render() {
    if(!data)return;
    const signals=new Map(data.items.map(i=>[i.symbol,displaySignal(i)]));
    initFavorites();
    document.querySelectorAll('[data-scope]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.scope===scope)));
    document.querySelectorAll('[data-kind]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.kind===kind)));
    stats(signals);
    renderResearch();
    const candidates=$('candidates');
    if(candidates){
      const eligible=data.mode==='live'&&!loadError?data.items.filter(i=>plan(i)?.entry).sort((a,b)=>Number(plan(a).status==='wait_stabilize')-Number(plan(b).status==='wait_stabilize')):[];
      candidates.innerHTML=eligible.length?eligible.slice(0,6).map(i=>{const p=plan(i),r=i.research_detail||{};return `<article class="health-card plan-card"><span class="tag ${p.status==='conditional'?'buy':''}">${esc(p.label)}</span><h3><button class="stock-name" data-symbol="${esc(i.symbol)}">${esc(i.name)} ${esc(i.symbol)}</button></h3><p class="plan-price">承接參考 <strong>${price(p.entry)}</strong><span> 元</span></p><p>最高接受 <strong>${price(p.buy_max)} 元</strong> · 超過不追</p><p>${esc(p.reason)}。</p><p>${esc(r.thesis)}</p>${purchaseReasons(i)}<p class="watch-risk">風險：${esc(r.risks?.[0]||'待確認')}</p><small>${esc(p.close_date)} 收盤規劃 · 更新期限 ${esc(clockText(p.valid_until))}<br>盤前確認後才考慮掛單</small></article>`;}).join(''):'<p>目前没有完整有效的承接計畫，請查看下方等待原因。</p>'.replace('没有','沒有');
      candidates.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.symbol)));
    }
    const q=$('search').value.trim().toLowerCase(), status=$('status').value, industry=$('industry').value;
    const filter=JSON.stringify([q,status,industry,kind,scope]);
    if(filter!==lastFilter){visibleLimit=100;lastFilter=filter;}
    const order={sweet:0,add:1,buy:2,avoid:3,blocked:4,pending:5,stale:6};
    const items=data.items.filter(i => (scope==='all'||(scope==='favorites'?favorites.has(i.symbol):!!i.valuation)) && (kind==='all'||(kind==='etf'?i.kind?.startsWith('etf'):i.kind==='stock')) &&
      (industry==='all'||i.industry===industry) && (status==='all'||signals.get(i.symbol).status===status) &&
      (!q||`${i.symbol} ${i.name}`.toLowerCase().includes(q))).sort((a,b)=>order[signals.get(a.symbol).status]-order[signals.get(b.symbol).status] || Number(!!b.watched)-Number(!!a.watched) || a.symbol.localeCompare(b.symbol));
    $('result-count').textContent=`顯示 ${Math.min(visibleLimit,items.length).toLocaleString()} / 符合 ${items.length.toLocaleString()} 檔 · 全部 ${data.items.length.toLocaleString()}`;
    $('load-more').hidden=items.length<=visibleLimit;
    $('empty').hidden=items.length>0;
    $('rows').innerHTML=items.slice(0,visibleLimit).map(i=>{
      const s=signals.get(i.symbol),v=i.valuation||{},q=i.quote||{};
      return `<tr><td>${star(i)}<button class="stock-name" data-symbol="${esc(i.symbol)}">${esc(i.name||i.symbol)}<span class="code">${esc(i.symbol)}</span></button><div class="desc" title="${esc(i.description||'公司／ETF 介紹待補')} "><span class="kind">${kindName(i.kind)}</span>${esc(i.description||i.industry||'介紹待補')}</div></td><td title="${esc(q.previous_close_date||q.trade_date||'資料日未提供')}">${price(publicClose(i)?.close ?? (quoteToday(q)?q.previous_close:null))}${publicClose(i)?`<div class="action">${esc(publicClose(i).trade_date)} · 收盤</div>`:!quoteToday(q)&&positive(q.previous_close)?'<div class="action">舊行情，待更新</div>':''}</td><td title="${esc(clockText(q.trial_as_of||q.as_of))}">${price(quoteToday(q)?q.trial_price:null)}</td><td class="suggest-cell ${positive(s.suggested)?'':'missing'}">${plan(i)?.entry?price(plan(i).entry):positive(s.suggested)?price(s.suggested):'暫不掛'}</td><td class="anchor">${price(v.sweet)}</td><td class="anchor">${price(v.add)}</td><td class="anchor">${price(v.buy)}</td><td><span class="tag ${s.status}">${labels[s.status]}</span><div class="action">${esc(!i.valuation&&i.fundamentals?i.fundamentals.reading:s.action||s.reason||'等待確認')}</div></td><td class="date-cell">${i.valuation?'完整分析':i.fundamentals?'基本面觀察':'資料待補'}</td></tr>`;
    }).join('');
    bindStars($('rows'));
    $('rows').querySelectorAll('button[data-symbol]').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.symbol)));
  }
  function renderResearch() {
    const box=$('research-cards');if(!box)return;
    const rows=data.items.filter(i=>favorites.has(i.symbol));
    box.innerHTML=rows.length?rows.map(i=>{const r=i.research_detail||{},c=publicClose(i),v=i.valuation,valid=timeValue(r.valid_until)>Date.now()&&timeValue(r.as_of)<=Date.now();return `<article class="watch-row"><div>${star(i)}<button class="stock-name" data-symbol="${esc(i.symbol)}">${esc(i.name)} <span class="code">${esc(i.symbol)}</span></button><p class="muted">${esc(r.description||i.industry||'')}</p></div><div class="watch-price"><strong>${price(c?.close)}</strong><small>${esc(c?.trade_date||'價格未取得')} 收盤</small></div><div class="watch-verdict"><strong>${esc(valid?(plan(i)?.label||'等待資料'):'先等資料確認')}</strong><p>${esc(r.thesis||i.fundamentals?.reading||'尚未完成分析，加入關注後可在這裡集中查看。')}</p><p class="watch-risk">風險：${esc(r.risks?.[0]||'尚待確認')}</p></div><div><span class="tag">${plan(i)?.buy_max?'買進上限 '+price(plan(i).buy_max):'買進價尚未確認'}</span><button class="quiet" data-symbol="${esc(i.symbol)}">看分析依據 ↗</button></div></article>`;}).join(''):'<p>尚未加入關注。到全市場清單點選 ☆，即可加入這裡。</p>';
    bindStars(box);
    box.querySelectorAll('button[data-symbol]').forEach(b=>b.addEventListener('click',()=>openDetail(b.dataset.symbol)));
  }
  function sourceLinks(sources) {
    return sources?.length ? `<ul>${sources.map(s=>{const url=safeURL(s.url);return `<li>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(s.title||new URL(url).hostname)}</a>`:'來源網址未驗證'}${s.as_of?` · ${esc(dateText(s.as_of))}`:''}</li>`;}).join('')}</ul>` : '<p class="muted">尚未提供可查驗來源</p>';
  }
  function textSection(title,text) {return `<section><h3>${title}</h3><p>${esc(text||'尚未取得研究資料')}</p></section>`;}
  function listSection(title,list) {return `<section><h3>${title}</h3>${Array.isArray(list)&&list.length?`<ul>${list.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`:'<p>尚未取得研究資料</p>'}</section>`;}
  function detailHTML(i) {
    const q=i.quote||{}, v=i.valuation||{}, s=displaySignal(i), r=i.research||{};
    if(!i.valuation&&!i.research_detail)return `<div class="detail-head">${star(i)}<h2 id="detail-title">${esc(i.name)} ${esc(i.symbol)}</h2><p>${esc(i.industry||kindName(i.kind))} · ${i.fundamentals?'基本面觀察':'資料待補'}</p><p>最近公開收盤 ${price(publicClose(i)?.close)} 元 · ${esc(publicClose(i)?.trade_date||'未取得')}</p></div>${basicHTML(i)}`;
    const tiles=[['承接參考（待盤前確認）',plan(i)?.entry],['最近公開收盤',publicClose(i)?.close ?? (quoteToday(q)?q.previous_close:null)],['盤前試撮',quoteToday(q)?q.trial_price:null],['甜甜價',v.sweet],['加碼價',v.add],['買進價',v.buy]];
    return `<div class="detail-head">${star(i)}<h2 id="detail-title">${esc(i.name||i.symbol)} <span class="code">${esc(i.symbol)} · ${kindName(i.kind)}</span></h2><p>${esc(i.description||'公司／ETF 介紹待補')}</p><span class="tag ${s.status}">${labels[s.status]}</span><p>${esc(s.action||s.reason)}</p></div><div class="detail-prices">${tiles.map(([label,n],j)=>`<div class="price-block ${j===0?'primary':''}"><span>${label}</span><strong>${price(n)}</strong></div>`).join('')}</div><p class="muted">價格依據 ${esc(publicClose(i)?.trade_date||'未取得')} 收盤；承接計畫有效至 ${esc(clockText(plan(i)?.valid_until))}。當日試撮與五檔由 Discord 盤前通知確認。</p>${purchaseReasons(i)}${basicHTML(i)}<div class="research-grid">${textSection('為什麼看好',r.thesis||v.thesis)}${textSection('研究失效條件',v.invalidation?.join('；')||i.research_detail?.invalidation?.join('；'))}${textSection('為什麼現在買／不買',plan(i)?.reason||r.why_now||s.reason)}${textSection('籌碼',r.chips)}${listSection('催化劑',r.catalysts)}${listSection('主要風險',r.risks)}${textSection('估值方法',v.method ? `${v.method}；${v.reason||''}`:null)}${textSection('其他承接位置',positive(s.suggested)?`保守撿價 ${price(s.conservative)}；極端承接 ${price(s.extreme)}。未有價格結構支持的欄位留空。`:null)}${textSection('不追與減碼',positive(v.avoid)||positive(v.sell)||positive(v.reduce)?`不追 ${price(v.avoid)}；開始賣 ${price(v.sell)}；強減碼 ${price(v.reduce)}`:null)}</div><section class="sources"><h3>研究來源</h3>${sourceLinks(r.sources?.length?r.sources:v.sources)}</section>`;
  }
  function openDetail(symbol) {selected=symbol;const i=data.items.find(i=>i.symbol===symbol);if(!i)return;$('detail-content').innerHTML=detailHTML(i);bindStars($('detail-content'));if(!$('detail').open)$('detail').showModal();}
  function system() {
    const c=data.coverage||{};
    $('coverage').textContent=`母表 ${(c.universe??data.items.length).toLocaleString()} · 有估值 ${c.valued??0} · 公開收盤 ${c.public_closes??0} · 研究 ${c.research??0}`;
    const health=data.health?.length?data.health:[{name:'資料來源',status:'pending',detail:'等待主機提供完整資料狀態'}];
    $('health').innerHTML=health.map(h=>`<article class="health-card"><div class="health-title">${esc(h.name)}<span class="tag ${h.status==='ok'?'sweet':h.status==='blocked'||h.status==='error'?'blocked':''}">${esc({ok:'正常',blocked:'受阻',error:'異常',pending:'待確認',stale:'過期',partial:'部分完成'}[h.status]||'待確認')}</span></div><p>${esc(h.detail||'尚未提供說明')}</p>${h.as_of?`<p>${clockText(h.as_of)}</p>`:''}</article>`).join('');
  }
  async function load() {
    if(busy)return;busy=true;$('refresh').disabled=true;
    try {
      const res=await fetch(`radar.json?t=${Date.now()}`,{cache:'no-store',signal:AbortSignal.timeout(15000)});
      if(!res.ok)throw new Error(`雷達資料讀取失敗 (${res.status})`);
      const next=await res.json();
      if(next.schema_version!==1||!Array.isArray(next.items)||!['live','simulation','initializing'].includes(next.mode))throw new Error('資料格式不相容，等待主機更新');
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
  document.querySelectorAll('[data-scope]').forEach(b=>b.addEventListener('click',()=>{scope=b.dataset.scope;$('status').value='all';render();}));
  $('show-my-stocks')?.addEventListener('click',()=>{scope='favorites';$('status').value='all';$('search').value='';render();jumpToResults();});
  $('refresh').addEventListener('click',load);
  $('load-more').addEventListener('click',()=>{visibleLimit+=100;render();});
  $('search').addEventListener('input',render);$('industry').addEventListener('change',render);$('status').addEventListener('change',render);
  document.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{kind=b.dataset.kind;document.querySelectorAll('[data-kind]').forEach(n=>n.setAttribute('aria-pressed',String(n===b)));render();}));
  $('clear-filters').addEventListener('click',()=>{scope='all';$('search').value='';$('status').value='all';$('industry').value='all';document.querySelector('[data-kind="all"]').click();});
  $('close-detail').addEventListener('click',()=>$('detail').close());
  $('detail').addEventListener('close',()=>{selected=null;});
  setInterval(()=>{if(data){render();if(selected&&$('detail').open)openDetail(selected);}},30000);
  setInterval(load,60000);
  load();
})();
