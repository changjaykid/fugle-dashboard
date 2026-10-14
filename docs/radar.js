'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const timeValue = v => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) ? Date.parse(v) : NaN;
  const clockText = v => v && Number.isFinite(timeValue(v)) ? new Date(v).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false}) : '尚未取得';
  let data = null, busy = false;

  function system() {
    // Data status only: what the scan covered.
    const t=(data.scan&&data.scan.tally)||{};
    $('coverage').textContent=t.universe?`掃描上市普通股 ${t.universe.toLocaleString()} 檔`:'';
    const health=data.health?.length?data.health:[{name:'資料來源',status:'pending',detail:'等待主機提供完整資料狀態'}];
    $('health').innerHTML=health.map(h=>`<article class="health-card"><div class="health-title">${esc(h.name)}<span class="tag ${h.status==='ok'?'sweet':h.status==='blocked'||h.status==='error'?'blocked':''}">${esc({ok:'正常',blocked:'受阻',error:'異常',pending:'待確認',stale:'過期',partial:'部分完成'}[h.status]||'待確認')}</span></div><p>${esc(h.detail||'尚未提供說明')}</p>${h.as_of?`<p>${clockText(h.as_of)}</p>`:''}</article>`).join('');
  }

  // --- Encrypted payload -------------------------------------------------------------------------
  // The host is GitHub Pages, so every published file answers 200 to anyone with its URL. A password
  // prompt in script would therefore protect nothing: the data would already have been fetched, and
  // the password would be in this file. So the payload itself is AES-256-GCM ciphertext and the key is
  // derived here, from what is typed, with WebCrypto. The password never leaves the browser and is in
  // no published file. The protection is only as strong as the password: this stops crawlers, search
  // engines and anyone who merely has the link, not someone who sets out to attack eight digits.
  const KEY_CACHE='radar.key';
  let vaultKey=null;
  const b64bytes=t=>Uint8Array.from(atob(t),c=>c.charCodeAt(0));
  const bytesb64=a=>btoa(String.fromCharCode(...new Uint8Array(a)));
  async function deriveKey(password,salt,iterations){
    const material=await crypto.subtle.importKey('raw',new TextEncoder().encode(password),'PBKDF2',false,['deriveKey']);
    return crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations,hash:'SHA-256'},material,
      {name:'AES-GCM',length:256},true,['decrypt']);
  }
  async function openEnvelope(envelope,key){
    if(envelope.v!==1||envelope.cipher!=='AES-256-GCM')throw new Error('資料格式不相容，等待主機更新');
    const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:b64bytes(envelope.iv)},key,b64bytes(envelope.ct));
    let bytes=new Uint8Array(plain);
    if(envelope.compression==='gzip'){
      const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      bytes=new Uint8Array(await new Response(stream).arrayBuffer());
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  }
  async function fetchEnvelope(){
    const res=await fetch(`radar.enc.json?t=${Date.now()}`,{cache:'no-store',signal:AbortSignal.timeout(20000)});
    if(!res.ok)throw new Error(`雷達資料讀取失敗 (${res.status})`);
    return res.json();
  }
  // The derived key is kept on this device for thirty days, so neither a refresh nor closing the tab
  // asks for the password again (the user, 2026-10-15). It works across publishes because the payload
  // is now encrypted with one fixed salt; before that, every publish changed the key and a kept key
  // opened nothing, which showed as a blank page. A key that no longer opens the file is dropped and
  // the password asked for, never a blank page.
  const KEY_DAYS=30;
  function forget(){vaultKey=null;try{localStorage.removeItem(KEY_CACHE);sessionStorage.removeItem(KEY_CACHE);}catch(e){}}
  async function cachedKey(){
    if(vaultKey)return vaultKey;
    let stored=null;
    try{stored=JSON.parse(localStorage.getItem(KEY_CACHE)||'null');}catch(e){stored=null;}
    if(!stored||!stored.k||!(stored.until>Date.now())){forget();return null;}
    try{
      vaultKey=await crypto.subtle.importKey('raw',b64bytes(stored.k),{name:'AES-GCM',length:256},true,['decrypt']);
      return vaultKey;
    }catch(e){forget();return null;}
  }
  async function unlock(password){
    const envelope=await fetchEnvelope();
    const key=await deriveKey(password,b64bytes(envelope.salt),envelope.iterations);
    const payload=await openEnvelope(envelope,key);   // throws on a wrong password: GCM authenticates
    vaultKey=key;
    try{localStorage.setItem(KEY_CACHE,JSON.stringify({k:bytesb64(await crypto.subtle.exportKey('raw',key)),
                                                        until:Date.now()+KEY_DAYS*86400000}));}catch(e){}
    return payload;
  }
  function showLocked(message){
    // Locked means there is nothing in the page to read, not merely that something is covering it:
    // the payload is dropped and everything drawn from it is blanked.
    data=null;
    ['health','coverage','paper-head','paper-stats','paper-open','paper-closed','paper-note','paper-stamp',
     'paper-orders','scan-picks','scan-near','scan-tally','scan-stamp'].forEach(id=>{const n=$(id);if(n)n.innerHTML='';});
    $('paper-section').hidden=true;$('scan-section').hidden=true;
    $('lock').hidden=false;document.querySelector('main').hidden=true;
    const err=$('lock-error');
    if(message){err.hidden=false;err.textContent=message;}else{err.hidden=true;}
    $('lock-go').disabled=false;$('lock-input').disabled=false;$('lock-input').focus();$('lock-input').select();
  }
  function showUnlocked(){$('lock').hidden=true;document.querySelector('main').hidden=false;}

  // --- Simulated account -------------------------------------------------------------------------
  // Every figure here travels with the benchmark it is being compared against. An absolute return in a
  // rising market says nothing.
  const money=n=>n==null?'—':Math.round(n).toLocaleString('zh-TW');
  const pct=n=>n==null?'—':`${n>=0?'+':''}${n.toFixed(2)}%`;
  const swing=n=>n==null?'':n>=0?'paper-up':'paper-down';
  function paperTable(head,rows){
    if(!rows.length)return '';
    return `<div class="paper-scroll"><table class="paper-table"><thead><tr>${head.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  }
  // --- Tonight's scan (swing.py) ---------------------------------------------------------------
  // The one set of entry and exit rules: buy a pullback at the lower Bollinger band, aim at the upper
  // band, let it run once it gets there and sell when it gives back 3% from its best price.
  function scan() {
    const d=data.scan, box=$('scan-section');
    if(!d||!d.candidates){box.hidden=true;return;}
    box.hidden=false;
    const t=d.tally||{}, r=d.rule_record;
    $('scan-stamp').textContent=`${d.day} 收盤掃描`;
    const num=n=>n==null?'—':Number(n).toLocaleString('zh-TW',{maximumFractionDigits:2});
    const signed=(n,unit)=>n==null?'—':`${n>0?'+':''}${Number(n).toLocaleString('zh-TW',{maximumFractionDigits:1})}${unit}`;
    const cell=(label,value,note)=>`<div><dt>${esc(label)}</dt><dd>${value}${note?`<small>${esc(note)}</small>`:''}</dd></div>`;
    const card=c=>`<article class="pick"><div class="pick-head"><span class="pick-name">${esc(c.name)} ${esc(c.symbol)}</span>`+
      `<span class="pick-industry">${esc(c.industry||'')}</span></div>`+
      `<p class="pick-business">${esc(c.business||'主要業務資料尚未取得')}</p><dl class="pick-grid">`+
      cell('掛單價',num(c.limit),c.below_close_pct!=null?`比收盤低 ${c.below_close_pct.toFixed(1)}%`:'')+
      cell('先看目標',num(c.target),c.target_pct!=null?`+${c.target_pct.toFixed(1)}%`:'')+
      cell('停損',num(c.stop),c.stop_pct!=null?`−${c.stop_pct.toFixed(1)}%`:'')+
      cell('建議買進',c.suggested_shares?`${num(c.suggested_shares)} 股`:'—',c.suggested_amount?`約 ${num(c.suggested_amount)} 元`:'')+
      cell('近60日 vs 大盤',signed(c.vs_market_60_pct,'%'))+
      cell('外資 5日',signed(c.foreign_lots,' 張'))+
      cell('投信 5日',signed(c.trust_lots,' 張'))+
      cell('月營收年增',signed(c.revenue_yoy_pct,'%'),c.revenue_month?`${String(c.revenue_month).slice(0,3)}/${String(c.revenue_month).slice(3)}`:'')+
      cell('本益比',num(c.pe))+
      cell('殖利率',c.dividend_yield!=null?`${num(c.dividend_yield)}%`:'—')+
      cell('EPS（近一季）',num(c.eps))+
      `</dl></article>`;
    if(!d.market_open){
      $('scan-picks').innerHTML='<p class="muted">0050 在 120 日線之下：大盤走弱時這套規則不掛新單。</p>';
    } else if(d.candidates.length){
      $('scan-picks').innerHTML=(r?`<p class="pick-record">這套規則回測 ${esc(r.period)}，${num(r.trades)} 筆：勝率 ${r.win_pct}%、平均每筆 ${signed(r.mean_pct,'%')}、平均持有 ${Math.round(r.held_days)} 個交易日。`+
        `限價掛在相對低點，跌到才買、最多掛 5 個交易日；碰到目標後續抱，自最高點回落 3% 賣；停損為買進價下 10%。</p>`:'')+
        `<div class="picks">${d.candidates.map(card).join('')}</div>`;
    } else {
      $('scan-picks').innerHTML='<p class="muted">今天沒有符合條件的標的，不掛新單。</p>';
    }
    const near=(d.near||[]).slice(0,8);
    $('scan-near').innerHTML=near.length
      ? `<h3 class="paper-sub">接近但不買</h3><div class="paper-scroll"><table class="paper-table"><thead><tr>`+
        ['標的','產業','收盤','差在哪裡'].map(h=>`<th>${esc(h)}</th>`).join('')+`</tr></thead><tbody>`+
        near.map(c=>`<tr><td>${esc(c.name)} ${esc(c.symbol)}</td><td>${esc(c.industry||'')}</td><td>${num(c.close)}</td>`+
          `<td style="text-align:left">${esc(c.why)}</td></tr>`).join('')+`</tbody></table></div>`
      : '';
    $('scan-tally').textContent=`掃描上市普通股 ${t.universe||0} 檔，可判斷 ${t.judged||0} 檔，符合 ${t.buy||0} 檔。`+
      (d.budget_per_position?`建議買進以模擬帳戶每檔約 ${num(d.budget_per_position)} 元計。`:'');
  }

  function paper() {
    const p=data.paper;
    const section=$('paper-section');
    if(!p||!p.performance){section.hidden=true;return;}
    section.hidden=false;
    const f=p.performance,b=p.benchmark;
    // An account that has been started and has not traded yet is a real state, not a missing one.
    // Hiding it made a deliberate reset look like a broken export.
    if(!f.closed_trades&&!f.open_positions&&f.net_equity===f.starting_cash){
      $('paper-stamp').textContent=`${p.started_day} 起｜起始 ${money(f.starting_cash)} 元`;
      $('paper-head').innerHTML=`<span class="paper-equity">${money(f.net_equity)} 元</span>`+
        `<span class="paper-bench">帳戶已重置，尚未有任何交易。第一筆會在掃描出符合條件的標的後產生。</span>`;
      $('paper-stats').innerHTML='';$('paper-open').innerHTML='';$('paper-closed').innerHTML='';$('paper-orders').innerHTML='';
      $('paper-note').textContent='';
      return;
    }
    const excess=b?f.total_return_pct-b.equal_weight_pct:null;
    $('paper-stamp').textContent=`${p.started_day} 起｜起始 ${money(f.starting_cash)} 元`;
    $('paper-head').innerHTML=
      `<span class="paper-equity">${money(f.net_equity)} 元</span>`+
      `<span class="paper-gain ${swing(f.total_return_pct)}">${pct(f.total_return_pct)}</span>`+
      (b?`<span class="paper-bench">同期全市場等權 ${pct(b.equal_weight_pct)}（${b.symbols} 檔）→ 超額 <strong class="${swing(excess)}">${pct(excess)}</strong></span>`
        :`<span class="paper-bench">同期大盤基準尚無法計算</span>`);
    const stat=(label,value,note)=>`<article class="stat"><span class="stat-label">${esc(label)}</span><span class="stat-value">${esc(value)}</span>${note?`<span class="muted">${esc(note)}</span>`:''}</article>`;
    $('paper-stats').innerHTML=[
      stat('已結交易',`${f.closed_trades} 筆`,f.win_rate_pct==null?'':`勝 ${f.wins} 敗 ${f.losses}，勝率 ${f.win_rate_pct.toFixed(0)}%`),
      stat('已實現損益',`${money(f.realised_profit)} 元`,`平均獲利 ${pct(f.average_win_pct)}／虧損 ${pct(f.average_loss_pct)}`),
      stat('手續費與稅',`${money(f.fees_and_tax)} 元`,`佔起始資金 ${(f.fees_and_tax/f.starting_cash*100).toFixed(2)}%`),
      stat('現金',`${money(f.cash)} 元`,f.unsettled?`另有未交割 ${money(f.unsettled)} 元`:'無未交割款'),
      stat('持倉',`${f.open_positions} 檔`,`市值 ${money(f.holdings_value)} 元`),
    ].join('');
    const unreal=p.unrealised||{};
    const open=(p.positions||[]).slice().sort((a,b2)=>(unreal[b2.symbol]?.return_pct??0)-(unreal[a.symbol]?.return_pct??0)).map(x=>{
      const u=unreal[x.symbol]||{};
      return `<tr><td>${esc(x.name||x.symbol)} ${esc(x.symbol)}<span class="paper-tag">${esc({lot:'整股',odd_lot:'零股',mixed:'整股+零股'}[x.order_type]||x.order_type||'')}</span></td>`+
        `<td>${esc(x.entry_day||'')}</td><td>${x.shares.toLocaleString('zh-TW')}</td><td>${x.entry}</td><td>${x.mark}</td>`+
        `<td class="${swing(u.profit)}">${money(u.profit)}</td><td class="${swing(u.return_pct)}">${pct(u.return_pct)}</td>`+
        `<td>${x.stop??'—'}</td><td>${x.armed?`已碰上緣，跌破 ${(x.peak*0.97).toFixed(2)} 賣`:(x.target??'—')}</td></tr>`;
    });
    $('paper-open').innerHTML=open.length
      ? `<h3 class="paper-sub">目前持倉</h3>`+paperTable(['標的','買進日','股數','成本','現價','未實現','報酬','停損','賣出點'],open)
      : `<h3 class="paper-sub">目前持倉</h3><p class="muted">沒有持倉。</p>`;
    const why={stop:'停損',target:'碰上緣後回落賣出',time:'時間到'};
    const closed=(p.closed||[]).slice().sort((a,b2)=>(b2.exit_day||'').localeCompare(a.exit_day||'')).map(x=>
      `<tr><td>${esc(x.name||x.symbol)} ${esc(x.symbol)}<span class="paper-tag">${esc(x.source==='live'?'實際':'回放')}</span></td>`+
      `<td>${esc(x.entry_day||'')} → ${esc(x.exit_day||'')}</td><td>${x.shares.toLocaleString('zh-TW')}</td>`+
      `<td>${x.entry} → ${x.exit}</td><td class="${swing(x.profit)}">${money(x.profit)}</td>`+
      `<td class="${swing(x.return_pct)}">${pct(x.return_pct)}</td><td>${esc(why[x.exit_reason]||x.exit_reason||'')}</td>`+
      `<td>${x.held_days==null?'—':x.held_days+' 日'}</td></tr>`);
    $('paper-closed').innerHTML=closed.length
      ? `<h3 class="paper-sub">已結交易</h3>`+paperTable(['標的','進出日期','股數','進出價','損益','報酬','原因','持有'],closed)
      : '';
    const orders=(p.orders||[]);
    $('paper-orders').innerHTML=orders.length
      ? `<h3 class="paper-sub">掛單中</h3>`+paperTable(['標的','掛單價','先看','停損','掛單日','剩餘交易日'],
          orders.map(o=>`<tr><td>${esc(o.name)} ${esc(o.symbol)}</td><td>${o.limit}</td><td>${o.target}</td><td>${o.stop}</td><td>${esc(o.placed)}</td><td>${o.left}</td></tr>`))
      : '';
    $('paper-note').textContent=`這是模擬帳戶，沒有真實下單。報酬是以投入現金計算，已扣手續費 0.1425%／邊與賣出證交稅；`+
      `限價單只在當日最低真的觸及時才算成交。`;
  }

  function draw(){
    $('last-scan').textContent=`最後掃描 ${clockText(data.generated_at)}`;
    $('market-date').textContent=`${new Date().toLocaleDateString('zh-TW',{timeZone:'Asia/Taipei'})} · 台北`;
    $('notice').hidden=true;
    scan();paper();system();
  }
  // It needs the schema, a known mode, and tonight's scan -- without which there is nothing to say.
  function accept(next){
    if(next.schema_version!==1||!['live','simulation','initializing'].includes(next.mode))throw new Error('資料格式不相容，等待主機更新');
    if(!next.scan||!Array.isArray(next.scan.candidates))throw new Error('今天的掃描資料不完整');
    return next;
  }

  async function load() {
    if(busy)return;busy=true;$('refresh').disabled=true;
    try {
      const key=await cachedKey();
      if(!key){showLocked();return;}
      let opened;
      try{opened=await openEnvelope(await fetchEnvelope(),key);}
      catch(e){if(e&&e.name==='OperationError'){forget();showLocked('資料已更新，請重新輸入一次密碼。');return;}throw e;}
      data=accept(opened);
      draw();
    } catch(e) {
      $('notice').hidden=false;$('notice').className='notice error';
      $('notice').textContent=`${e.message}。${data?'目前保留上次資料。':'目前無可用資料，請稍後重新整理。'}`;
    } finally {busy=false;$('refresh').disabled=false;}
  }
  $('refresh').addEventListener('click',load);
  $('lock-form').addEventListener('submit',async e=>{
    e.preventDefault();
    const password=$('lock-input').value;
    if(!password)return;
    $('lock-go').disabled=true;$('lock-input').disabled=true;$('lock-error').hidden=true;$('lock-go').textContent='解開中…';
    try{
      data=accept(await unlock(password));
      $('lock-input').value='';$('lock-go').textContent='解鎖';
      showUnlocked();draw();
      setInterval(load,60000);
    }catch(err){
      $('lock-go').textContent='解鎖';
      showLocked(err?.name==='OperationError'?'密碼不對。':`無法解開：${err.message}`);
    }
  });
  (async()=>{
    if(await cachedKey()){showUnlocked();await load();setInterval(load,60000);}
    else showLocked();
  })();
})();
