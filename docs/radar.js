'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const timeValue = v => typeof v === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(v) ? Date.parse(v) : NaN;
  const clockText = v => v && Number.isFinite(timeValue(v)) ? new Date(v).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false}) : '尚未取得';
  let data = null, busy = false;

  function system() {
    // Data status only. The coverage line used to count valuations and research, which no longer
    // reach this page; what selects is Strategy v1, so its own scan is what this reports.
    const t=(data.v1&&data.v1.tally)||{};
    $('coverage').textContent=t.universe?`v1 掃描上市普通股 ${t.universe.toLocaleString()} 檔`:'';
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
  // The derived key is kept for the tab's session so a refresh neither re-asks nor repeats a one-second
  // key derivation. It goes when the tab closes.
  async function cachedKey(){
    if(vaultKey)return vaultKey;
    const stored=sessionStorage.getItem(KEY_CACHE);
    if(!stored)return null;
    try{
      vaultKey=await crypto.subtle.importKey('raw',b64bytes(stored),{name:'AES-GCM',length:256},true,['decrypt']);
      return vaultKey;
    }catch(e){sessionStorage.removeItem(KEY_CACHE);return null;}
  }
  async function unlock(password){
    const envelope=await fetchEnvelope();
    const key=await deriveKey(password,b64bytes(envelope.salt),envelope.iterations);
    const payload=await openEnvelope(envelope,key);   // throws on a wrong password: GCM authenticates
    vaultKey=key;
    try{sessionStorage.setItem(KEY_CACHE,bytesb64(await crypto.subtle.exportKey('raw',key)));}catch(e){}
    return payload;
  }
  function showLocked(message){
    // Locked means there is nothing in the page to read, not merely that something is covering it:
    // the payload is dropped and everything drawn from it is blanked.
    data=null;
    ['health','coverage','paper-head','paper-stats','paper-open','paper-closed','paper-note','paper-stamp',
     'v1-picks','v1-waits','v1-tally','v1-stamp'].forEach(id=>{const n=$(id);if(n)n.innerHTML='';});
    $('paper-section').hidden=true;$('v1-section').hidden=true;
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
  // --- Strategy v1 -------------------------------------------------------------------------------
  // The one set of entry and exit rules. The valuation band that used to sit above this -- buy
  // prices, the four-box filters, research cards -- was a second answer to "should I buy this", it
  // decided nothing the account traded, and the user asked for one set of rules. It is gone, not
  // hidden: a page that still carried it would still say two things.
  function v1() {
    const d=data.v1, box=$('v1-section');
    if(!d||!d.candidates){box.hidden=true;return;}
    box.hidden=false;
    const t=d.tally||{};
    $('v1-stamp').textContent=`${d.day} 收盤掃描｜下單門檻 ${d.min_score} 分`;
    const kind=x=>x==='breakout'?'突破':'回檔';
    if(d.candidates.length){
      $('v1-picks').innerHTML=`<div class="paper-scroll"><table class="paper-table"><thead><tr>`+
        ['標的','型態','分數','進場','停損','目標','風報比','失效點'].map(h=>`<th>${esc(h)}</th>`).join('')+
        `</tr></thead><tbody>`+d.candidates.map(c=>
          `<tr><td>${esc(c.name)} ${esc(c.symbol)}</td><td>${esc(kind(c.setup))}</td>`+
          `<td>${c.total.toFixed(0)} <span class="paper-tag">${esc(c.grade)}</span></td>`+
          `<td>${c.entry}</td><td>${c.stop}</td><td>${c.target}</td>`+
          `<td>${c.rr.toFixed(1)}</td><td>${esc(c.stop_reason||'')}</td></tr>`).join('')+
        `</tbody></table></div>`;
    } else {
      $('v1-picks').innerHTML='<p class="muted">今天沒有標的同時通過選股、進場與風報比，不交易。</p>';
    }
    const waits=(d.waits||[]).slice(0,8);
    $('v1-waits').innerHTML=waits.length
      ? `<h3 class="paper-sub">分數夠高但不下單</h3><div class="paper-scroll"><table class="paper-table"><thead><tr>`+
        ['標的','型態','分數','不下單的理由'].map(h=>`<th>${esc(h)}</th>`).join('')+`</tr></thead><tbody>`+
        waits.map(c=>`<tr><td>${esc(c.name)} ${esc(c.symbol)}</td><td>${esc(kind(c.setup))}</td>`+
          `<td>${c.total.toFixed(0)}</td><td style="text-align:left">${esc(c.wait_reason||'')}</td></tr>`).join('')+
        `</tbody></table></div>`
      : '';
    $('v1-tally').textContent=`掃描上市普通股 ${t.universe||0} 檔：硬性排除 ${t.filtered||0}、無型態 ${t.no_setup||0}、`+
      `突破 ${t.breakout||0}、回檔 ${t.pullback||0}。選股與進場分開判斷：分數高不代表現在能買。`;
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
      $('paper-stats').innerHTML='';$('paper-open').innerHTML='';$('paper-closed').innerHTML='';
      $('paper-note').textContent=`策略參數：${JSON.stringify(p.settings||{})}`;
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
        `<td>${x.stop_now??x.stop??'—'}</td><td>${x.target??'—'}</td></tr>`;
    });
    $('paper-open').innerHTML=open.length
      ? `<h3 class="paper-sub">目前持倉</h3>`+paperTable(['標的','買進日','股數','成本','現價','未實現','報酬','停損','目標'],open)
      : `<h3 class="paper-sub">目前持倉</h3><p class="muted">沒有持倉。</p>`;
    const why={stop:'停損',target:'達標',time:'時間到',trail:'移動停損'};
    const closed=(p.closed||[]).slice().sort((a,b2)=>(b2.exit_day||'').localeCompare(a.exit_day||'')).map(x=>
      `<tr><td>${esc(x.name||x.symbol)} ${esc(x.symbol)}<span class="paper-tag">${esc(x.source==='live'?'實際':'回放')}</span></td>`+
      `<td>${esc(x.entry_day||'')} → ${esc(x.exit_day||'')}</td><td>${x.shares.toLocaleString('zh-TW')}</td>`+
      `<td>${x.entry} → ${x.exit}</td><td class="${swing(x.profit)}">${money(x.profit)}</td>`+
      `<td class="${swing(x.return_pct)}">${pct(x.return_pct)}</td><td>${esc(why[x.exit_reason]||x.exit_reason||'')}</td>`+
      `<td>${x.held_days==null?'—':x.held_days+' 日'}</td></tr>`);
    $('paper-closed').innerHTML=closed.length
      ? `<h3 class="paper-sub">已結交易</h3>`+paperTable(['標的','進出日期','股數','進出價','損益','報酬','原因','持有'],closed)
      : '';
    const seeded=p.seeded_from?`${p.seeded_from} 起的部分是用已儲存的歷史 K 線回放，標「回放」；之後每天收盤實際推進的標「實際」。`:'';
    $('paper-note').textContent=`這是模擬帳戶，沒有真實下單。報酬是以投入現金計算，已扣手續費 0.1425%／邊與賣出證交稅；`+
      `限價單只在當日最低真的觸及時才算成交。${seeded}策略參數：${JSON.stringify(p.settings||{})}`;
  }

  function draw(){
    $('last-scan').textContent=`最後掃描 ${clockText(data.generated_at)}`;
    $('market-date').textContent=`${new Date().toLocaleDateString('zh-TW',{timeZone:'Asia/Taipei'})} · 台北`;
    $('notice').hidden=true;
    v1();paper();system();
  }
  // The page no longer needs the valuation rows, so it no longer requires them. It needs the schema,
  // a known mode, and the v1 scan -- without which there is nothing to say.
  function accept(next){
    if(next.schema_version!==1||!['live','simulation','initializing'].includes(next.mode))throw new Error('資料格式不相容，等待主機更新');
    if(!next.v1||!Array.isArray(next.v1.candidates))throw new Error('今天的 v1 掃描資料不完整');
    return next;
  }

  async function load() {
    if(busy)return;busy=true;$('refresh').disabled=true;
    try {
      const key=await cachedKey();
      if(!key){showLocked();return;}
      data=accept(await openEnvelope(await fetchEnvelope(),key));
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
