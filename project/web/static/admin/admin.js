'use strict';
const names={overview:'營運總覽',products:'庫存',orders:'訂單管理',counter:'計數與回寫',experiments:'對照實驗',requirements:'交付檢查'};
const state={page:'overview',pageNo:1,query:'',category:'',availability:'',sort:'id',bootstrap:null,source:'formal',batch:'',scenario:'',concurrency:''};
const autoRefreshPages=new Set(['overview','products','orders','counter']);
let renderSerial=0,pendingRenders=0,renderedPage=null,autoRefreshTimer=null;
let autoRefreshEnabled=false,lastSuccessfulAt='',refreshFailed=false;
try{autoRefreshEnabled=localStorage.getItem('q01_admin_autorefresh')==='true';}catch{}
function currentPage(){return names[location.hash.slice(1)]?location.hash.slice(1):'overview';}
function refreshStatus(){
  return [autoRefreshEnabled?`每 2 秒更新 · ${lastSuccessfulAt||'尚未更新'}`:'',refreshFailed?'更新失敗':''].filter(Boolean).join(' · ');
}
function refreshControls(){
  if(!autoRefreshPages.has(state.page))return refreshButton;
  return `<div class="refresh-controls">${refreshButton}<button id="autorefresh-toggle" class="button" data-action="autorefresh" aria-pressed="${autoRefreshEnabled}">自動更新</button><span id="autorefresh-status" class="subtle" role="status" aria-live="polite">${esc(refreshStatus())}</span></div>`;
}
function updateRefreshControls(){
  const button=$('#autorefresh-toggle'),status=$('#autorefresh-status');
  if(button)button.setAttribute('aria-pressed',String(autoRefreshEnabled));
  if(status){status.textContent=refreshStatus();status.classList.toggle('refresh-error',refreshFailed);}
}
function refreshBlocked(){
  return document.hidden || $('#order-dialog').open ||
    (currentPage()==='products' && !!document.activeElement?.closest('#inventory-filter'));
}
async function autoRefreshTick(){
  if(!autoRefreshEnabled || !autoRefreshPages.has(currentPage()) || pendingRenders || refreshBlocked())return;
  await render({automatic:true});
}
function syncAutoRefreshTimer(){
  if(autoRefreshTimer!==null){window.clearInterval(autoRefreshTimer);autoRefreshTimer=null;}
  if(autoRefreshEnabled)autoRefreshTimer=window.setInterval(autoRefreshTick,2000);
}
function toggleAutoRefresh(){
  autoRefreshEnabled=!autoRefreshEnabled;
  try{localStorage.setItem('q01_admin_autorefresh',String(autoRefreshEnabled));}catch{}
  syncAutoRefreshTimer();updateRefreshControls();
}
function liveValue(key,value){return `<span data-live-value="${esc(key)}">${esc(value)}</span>`;}
function highlightChanges(previous){
  if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)return;
  $('#main').querySelectorAll('[data-live-value]').forEach(el=>{
    if(previous.has(el.dataset.liveValue) && previous.get(el.dataset.liveValue)!==el.textContent)el.classList.add('value-changed');
  });
}
async function overview() {
  const [d,recent] = await Promise.all([api('/api/admin/dashboard'),api('/api/admin/orders')]);
  const maximum = Math.max(1,...d.top.map(p => p.view_count));
  return heading('讓每一次瀏覽，都有跡可循。','從商品到訂單，在這裡掌握商店的每一個環節。',refreshControls()) +
  `<div class="stats">${stat('商品總數',liveValue('products',num(d.products)),'已載入課程商品 · 獨立展示庫存','box')}${stat('累計瀏覽',liveValue('views',num(d.pg_views)),'PostgreSQL 已保存的展示計數','chart')}${stat('成立訂單',liveValue('orders',num(d.orders)),'成功提交，庫存已同步扣除','list')}${stat('訂單總額',liveValue('revenue',money(d.revenue)),'展示訂單金額 · 未串接付款','bag')}</div>
  <section class="welcome"><div><div class="eyebrow">ONE FLOW, ALL CONNECTED</div><h2>從一次瀏覽，到一筆完整訂單</h2><p>操作真實資料，觀察計數與庫存如何變化。</p></div><div class="welcome-steps"><span>瀏覽商品</span>${icon('arrow')}<span>加入購物車</span>${icon('arrow')}<span>下單扣庫存</span></div><a class="button primary" href="#products">查看庫存${icon('arrow')}</a></section>
  ${d.error ? `<div class="alert error">${esc(d.error)}</div>` : ''}
  <div class="columns"><section class="panel"><div class="panel-head"><div><h2>熱門商品</h2><div class="subtle">依 PostgreSQL 已保存的瀏覽數排序</div></div><a href="#products">查看庫存 →</a></div>
  ${d.top.map((p,i) => `<div class="rank-row"><span class="rank-num">0${i+1}</span><span class="rank-thumb">${icon('box')}</span><div><div class="rank-name">${esc(p.name)} <span class="subtle">/ ${esc(p.category)}</span></div><meter min="0" max="${maximum}" value="${p.view_count}" aria-label="${esc(p.name)} 瀏覽數"></meter></div><div class="rank-count">${num(p.view_count)}<small>次瀏覽</small></div></div>`).join('')}</section>
  <section class="panel"><div class="panel-head"><div><h2>計數與回寫狀態</h2><div class="subtle">當前展示服務</div></div><span class="mode-badge">${d.counter==='pg'?'方案 A':'方案 B'}</span></div><div class="status-content"><div class="status-row"><span>計數方式</span><strong>${d.counter==='pg'?'PostgreSQL 直接計數':'Redis ＋ 定期回寫'}</strong></div><div class="status-row"><span>PostgreSQL 計數</span><strong>${num(d.pg_views)}</strong></div><div class="status-row"><span>Redis 即時計數</span><strong>${d.counter==='redis'?num(d.live_views):'本模式未啟用'}</strong></div><div class="status-row"><span>回寫間隔</span><strong>${d.counter==='redis'?d.interval+' 秒':'即時寫入 PostgreSQL'}</strong></div><div class="status-row"><span>最近回寫</span><strong>${d.counter==='redis'?esc(time(d.last_flush)):'無需回寫'}</strong></div><div class="note-box">網站與壓測共用 public 的資料，但網站延遲不是微基準。正式效能結論請查看對照實驗，並使用完整條件下的量測。</div></div></section></div>
  <section class="panel"><div class="panel-head"><h2>最近成立的訂單</h2><a href="#orders">查看訂單 →</a></div><div id="recent-orders">${orderTable(recent.items,true)}</div></section>`;
}
async function orders() {
  const d = await api('/api/admin/orders');
  return heading('每一筆訂單，都有依據。','訂單與庫存於同一筆 PostgreSQL 交易中提交；最多顯示最近 100 筆。',refreshControls()) +
    `<section class="panel"><div class="panel-head"><h2>訂單紀錄</h2><span class="badge">q01 public</span></div>${orderTable(d.items)}</section>`;
}
function numberCell(value) { return value==='' || value==null ? '尚無資料' : num(value); }
function runValid(r) { return !/RUN VOID|COUNT MISMATCH/.test(r.notes || ''); }
function errorRates(r) {
  if([r.successful_ops,r.error_ops,r.unknown_ops].some(x=>x==null||x===''))return '尚無資料';
  const total=Number(r.successful_ops)+Number(r.error_ops)+Number(r.unknown_ops);
  if(!total)return '尚無資料';
  return `${num(Number(r.error_ops)/total*100)}% / ${num(Number(r.unknown_ops)/total*100)}%`;
}
function lockShare(r) {
  const match=(r.notes||'').match(/lock_share=([0-9.]+)/);
  return match ? num(Number(match[1])*100)+'%' : '—';
}
async function experiments() {
  const d = await api('/api/admin/experiments');
  // 正式量測分批次（results/<YYYYMMDD>/，D39），預設最新一批；不同批次不混在同一張表
  const batches = d.batches || [];
  if (state.source==='formal' && batches.length && !batches.includes(state.batch)) state.batch = batches[0];
  const inBatch = r => state.source!=='formal' || !batches.length || r.batch===state.batch;
  const rows = d.runs.filter(r => r.source===state.source && inBatch(r) && (!state.scenario||r.scenario===state.scenario) && (!state.concurrency||r.concurrency===state.concurrency));
  const fails = d.failures.filter(r => r.source===state.source && inBatch(r));
  const batchLabel = b => `${b.slice(0,4)}-${b.slice(4,6)}-${b.slice(6)}`;
  return heading('同一個需求，兩種答案。','保留原始量測的條件與結果，用證據比較 PostgreSQL 與 Redis。',refreshButton)+
  `<div class="alert">${state.source==='smoke'?'目前顯示煙霧測試：僅驗證流程，不能據此宣稱效能優劣。':'正式數據由組員親自量測並保存原始 log；尚無資料時不產生模擬數字。'} 只應比較相同流量、併發與暖機條件的結果。</div>
  <div class="columns"><section class="panel"><div class="panel-head"><h2>方案 A · PostgreSQL</h2><span class="badge neutral">同步寫入</span></div><p class="section-intro">每次瀏覽直接更新 view_count。觀察熱門商品集中寫入時的行鎖競爭與 p95 延遲。</p></section><section class="panel"><div class="panel-head"><h2>方案 B · Redis</h2><span class="badge">定期回寫</span></div><p class="section-intro">先累計計數，再以快照回寫 PostgreSQL。比較寫入效能，同時量測服務中斷的少算視窗。</p></section></div>
  <section class="panel"><div class="panel-head"><div><h2>量測結果</h2><div class="subtle">逐輪呈現 · 錯誤／未知率分母為正式期成功＋錯誤＋未知總數</div></div><span class="badge ${state.source==='smoke'?'amber':''}">${state.source==='smoke'?'煙霧測試':'正式量測'}</span></div>
  <div class="experiment-filters"><label>資料來源 <select id="experiment-source"><option value="smoke" ${state.source==='smoke'?'selected':''}>煙霧測試</option><option value="formal" ${state.source==='formal'?'selected':''}>正式量測</option></select></label>${state.source==='formal'&&batches.length?`<label>批次 <select id="experiment-batch">${batches.map(b=>`<option value="${esc(b)}" ${b===state.batch?'selected':''}>${esc(batchLabel(b))}</option>`).join('')}</select></label>`:''}<label>流量 <select id="experiment-scenario"><option value="">全部</option>${['hot80','uniform1000','hot5'].map(x=>`<option ${state.scenario===x?'selected':''}>${x}</option>`).join('')}</select></label><label>併發 <select id="experiment-concurrency"><option value="">全部</option>${[...new Set(d.runs.map(r=>r.concurrency))].sort((a,b)=>a-b).map(x=>`<option ${state.concurrency===x?'selected':''}>${esc(x)}</option>`).join('')}</select></label></div>
  ${!rows.length?'<div class="empty"><h3>尚無符合條件的量測資料</h3><p>完成 CLI 實驗並保存結果後，可在此重新整理查看。</p></div>':`<div class="table-wrap"><table><thead><tr><th>方案 / 流量</th><th>併發</th><th>暖機 / 正式</th><th>吞吐 ops/s</th><th>p95 ms</th><th>錯誤率 / 未知率</th><th>Lock 取樣比例</th><th>對帳 / 有效性</th><th>原始紀錄</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.backend)} <span class="subtle">/ ${esc(r.scenario)}</span></td><td>${numberCell(r.concurrency)}</td><td>${num(r.warmup_s)}s / ${num(r.measured_s)}s</td><td>${numberCell(r.success_ops_per_s)}</td><td>${numberCell(r.p95_ms)}</td><td>${errorRates(r)}<br><span class="subtle">${numberCell(r.error_ops)} / ${numberCell(r.unknown_ops)} 次</span></td><td>${lockShare(r)}</td><td><span class="badge ${runValid(r)?'':'amber'}">${runValid(r)?'未標記異常':'無效或對帳異常'}</span></td><td class="run-id">${esc(r.run_id)}<br>${esc(r.notes)}</td></tr>`).join('')}</tbody></table></div>`}</section>
  <section class="panel"><div class="panel-head"><div><h2>故障與少算視窗</h2><div class="subtle">保留遺失量上下界、在途請求與原判定</div></div></div>${!fails.length?'<div class="empty">尚無故障量測資料</div>':`<div class="table-wrap"><table><thead><tr><th>回寫間隔</th><th>已確認次數</th><th>PG 已回寫</th><th>差額 N − P</th><th>原始判定與上下界</th></tr></thead><tbody>${fails.map(r=>`<tr><td>${num(r.flush_interval_s)}s</td><td>${num(r.acknowledged_N)}</td><td>${num(r.pg_increment_P)}</td><td>${num(r.pg_gap_N_minus_P)}</td><td class="run-id">${esc(r.notes)}</td></tr>`).join('')}</tbody></table></div>`}</section>`;
}
function requirements() {
  const reqs = [
    ['核心購物流程','第一版已接真實資料','商品瀏覽 → 購物車 → 下單 → 扣庫存；可查詢訂單明細，庫存不足整筆拒絕。','說明書 p.4、p.35'],
    ['題 01 對照實驗','主矩陣已完成，故障測試待補','兩方案 × 50／200／500 併發 × hot80／uniform1000（另加 hot5），每格 3 輪、暖機 30 秒＋正式 60 秒，已保留原始 log；Redis 故障少算量 T=1、5 各需 3 次，目前各 1 次。','說明書 p.10–11'],
    ['效能報告','必要，尚待完成','至少 3 個真實查詢，各附索引前後 EXPLAIN ANALYZE；資料 ≥ 10 萬列；量測索引大小與寫入成本，包含一個加索引未變快的案例。','說明書 p.27、p.34'],
    ['交易實驗','必要，尚待完整實驗','實際重現超賣，保存雙 session 或壓測原始輸出；至少兩種機制不同的修法；比較吞吐與失敗／重試率，說明隔離級別。','說明書 p.27、p.34–35'],
    ['設計書與取捨','已有草案，待定稿','正式實驗前完成 1–2 頁設計書，記錄預期與條件；結果須解釋何時改變選擇，並說明與提案的差異。','說明書 p.27、p.35'],
    ['成果交付與防禦','後續必要','程式、可重現腳本、原始輸出、分工與 AI 使用說明；7:30–8:30 真實螢幕錄影，另準備個人架構防禦與同儕提問。','說明書 p.28、p.35、p.40–42']
  ];
  return heading('把功能做完，也把證據備齊。','依你指定的專題說明書 (1) 核對；此處標示交付範圍，不代替驗收。') + `<div class="alert">前端不計分；必要的是核心流程能跑通，以及效能、交易和對照實驗的證據完整。</div><div class="requirements-grid">${reqs.map((r,i)=>`<article class="panel requirement"><span class="badge ${i===0?'':'amber'}">${r[1]}</span><h3>0${i+1} / ${r[0]}</h3><p>${r[2]}</p><small>${r[3]}</small></article>`).join('')}</div>`;
}
async function products(){
  const q=new URLSearchParams({page:state.pageNo,q:state.query,category:state.category,availability:state.availability,sort:state.sort});
  const d=await api('/api/admin/products?'+q),pages=Math.max(1,Math.ceil(d.total/12));
  const options=(values,current)=>values.map(([v,t])=>`<option value="${esc(v)}" ${v===current?'selected':''}>${esc(t)}</option>`).join('');
  return heading('庫存一覽','價格、確切庫存與已售數量；本版唯讀，不提供調整庫存。',refreshControls())+
  `<form id="inventory-filter" class="toolbar"><input name="q" aria-label="搜尋商品" placeholder="搜尋名稱或編號" value="${esc(state.query)}"><select name="category" aria-label="分類">${options([['','全部分類'],...state.bootstrap.categories.map(c=>[c,c])],state.category)}</select><select name="availability" aria-label="庫存狀態">${options([['','全部庫存'],['in_stock','有貨'],['low','低庫存（1–10）'],['out','售完']],state.availability)}</select><select name="sort" aria-label="庫存排序">${options([['id','商品編號'],['stock_asc','庫存由少到多'],['stock_desc','庫存由多到少']],state.sort)}</select><button class="button">篩選</button></form>
  <section class="panel"><div class="table-wrap"><table><thead><tr><th>商品</th><th>分類</th><th>價格</th><th>確切庫存</th><th>已售數量</th><th>狀態</th></tr></thead><tbody>${d.items.map(p=>`<tr><td>#${p.product_id} ${esc(p.name)}</td><td>${esc(p.category)}</td><td>${money(p.price)}</td><td>${liveValue(`stock-${p.product_id}`,num(p.stock))}</td><td>${liveValue(`sold-${p.product_id}`,num(p.sold))}</td><td><span class="badge ${p.stock<=10?'amber':''}">${p.stock===0?'已售完':p.stock<=10?'低庫存':'有貨'}</span></td></tr>`).join('')}</tbody></table></div>${!d.items.length?'<div class="empty">尚無符合條件的商品</div>':''}</section><div class="pagination"><span>第 ${d.page} / ${pages} 頁 · ${num(d.total)} 件</span><div><button class="button" data-action="prev" ${d.page<=1?'disabled':''}>上一頁</button><button class="button" data-action="next" ${d.page>=pages?'disabled':''}>下一頁</button></div></div>`;
}
async function counter(){
  const d=await api('/api/admin/dashboard');
  return heading('計數與回寫','目前展示模式與同步狀態。',refreshControls())+`<section class="panel"><div class="status-content">${[['計數模式',d.counter],['PG 計數',num(d.pg_views)],['Redis 計數',d.live_views==null?'未啟用或無法讀取':num(d.live_views)],['Redis 與 PG 差額',d.live_views==null?'—':num(d.live_views-d.pg_views)],['最後回寫',time(d.last_flush)],['間隔',d.counter==='redis'?d.interval+' 秒':'即時'],['run_id',d.run_id],['狀態',d.error||'正常']].map(([k,v])=>`<div class="status-row"><span>${esc(k)}</span><strong>${['PG 計數','Redis 計數','Redis 與 PG 差額'].includes(k)?liveValue(k,v):esc(v)}</strong></div>`).join('')}</div></section>`;
}
async function render(options={}){
  const automatic=options.automatic===true;
  if(automatic && (!autoRefreshEnabled || !autoRefreshPages.has(currentPage()) || pendingRenders || refreshBlocked()))return;
  const ticket=++renderSerial,page=currentPage();state.page=page;pendingRenders++;
  document.title=names[state.page]+' · 拾物後台';$('#crumb').textContent=names[state.page];
  document.querySelectorAll('nav [data-page]').forEach(el=>{el.classList.toggle('active',el.dataset.page===state.page);if(el.dataset.page===state.page)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  try{
    const html=await ({overview,products,orders,counter,experiments,requirements}[page])();
    if(ticket!==renderSerial || page!==currentPage())return;
    // 使用者可能在請求途中開始輸入、開啟明細、切至背景或關閉自動更新。
    if(automatic && (!autoRefreshEnabled || refreshBlocked()))return;
    const samePage=renderedPage===page,previous=new Map();
    if(samePage)$('#main').querySelectorAll('[data-live-value]').forEach(el=>previous.set(el.dataset.liveValue,el.textContent));
    const scroll={left:window.scrollX,top:window.scrollY,behavior:'instant'};
    $('#main').innerHTML=html;renderedPage=page;refreshFailed=false;
    lastSuccessfulAt=new Date().toLocaleTimeString('zh-TW',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
    updateRefreshControls();highlightChanges(previous);
    if(samePage)window.scrollTo(scroll);
  }catch(e){
    if(ticket!==renderSerial || page!==currentPage())return;
    if(renderedPage===page && autoRefreshPages.has(page)){refreshFailed=true;updateRefreshControls();}
    else{
      renderedPage=page;lastSuccessfulAt='';refreshFailed=true;
      $('#main').innerHTML=heading('暫時無法讀取資料','請確認後台權杖與本機服務。',refreshControls())+`<div class="alert error">${esc(e.message)}</div>`;
      updateRefreshControls();
    }
  }finally{pendingRenders--;}
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-action]');if(!b)return;
  try{
    const a=b.dataset.action;
    if(a==='close')b.closest('dialog').close();
    if(a==='refresh')await render();
    if(a==='autorefresh')toggleAutoRefresh();
    if(a==='prev'){state.pageNo--;await render();}
    if(a==='next'){state.pageNo++;await render();}
    if(a==='order')showOrder(await api('/api/admin/orders/'+Number(b.dataset.id)));
  }catch(e){toast(e.message);}
});
document.addEventListener('submit',e=>{
  if(e.target.id!=='inventory-filter')return;e.preventDefault();const d=new FormData(e.target);
  for(const k of ['q','category','availability','sort'])state[k==='q'?'query':k]=d.get(k);state.pageNo=1;render();
});
document.addEventListener('change',e=>{const key={'experiment-source':'source','experiment-batch':'batch','experiment-scenario':'scenario','experiment-concurrency':'concurrency'}[e.target.id];if(key){state[key]=e.target.value;render();}});
window.addEventListener('hashchange',render);
async function start(){try{state.bootstrap=await api('/api/admin/bootstrap');await render();syncAutoRefreshTimer();}catch(e){$('#main').innerHTML=`<div class="alert error">${esc(e.message)}</div>`;}}
start();
