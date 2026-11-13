'use strict';
function cartLimit(p){return Number.isInteger(p.stock) && p.stock >= 0 ? Math.min(p.stock,10000) : 10000;}
function stockLabel(p){return p.availability==='已售完' || !Number.isInteger(p.stock) || p.stock<0 ? p.availability : `${p.availability} · 剩 ${p.stock} 件`;}
function currentCustomer(){return String(attempt?.customer_id || $('#current-customer').value || state.bootstrap.customers[0]?.customer_id || '');}
const names = {products:'商品瀏覽',orders:'我的訂單'};
const state = {page:'products',products:[],pageNo:1,query:'',category:'',bootstrap:null,checkoutBusy:false,cartError:''};
// 購物車存在 PostgreSQL 的 cart_items（D38），每位顧客一台，換分頁、換瀏覽器都在；這裡只是畫面用的副本。
let cart = [];
// 結果未確認（503／斷線）的結帳：保存送出的內容與當時畫面，以同一 request_id 重試。只有這一項存在瀏覽器。
let attempt = null, attemptView = null;
const PENDING_KEY = 'q01-checkout';
try { localStorage.removeItem?.('q01-cart'); } catch (_) {}  // D38 前的瀏覽器購物車，已不使用
try { const saved = JSON.parse(localStorage.getItem(PENDING_KEY) || '{}'); if (saved.attempt && Array.isArray(saved.view)) { attempt = saved.attempt; attemptView = saved.view; } } catch (_) {}
function savePending() {
  try { if (attempt) localStorage.setItem(PENDING_KEY, JSON.stringify({attempt, view: attemptView})); else localStorage.removeItem?.(PENDING_KEY); } catch (_) {}
}
function showCount() { $('#cart-count').textContent = cart.reduce((s,p) => s+p.quantity,0); }
// 從伺服器載入目前顧客的購物車（含現價、庫存與加入時價格）。結果未確認時顯示送出當下的內容，不重新載入。
async function loadCart() {
  if (attempt) { cart = attemptView.map(p => ({...p})); showCount(); return; }
  const customer = currentCustomer();
  const d = await api('/api/store/cart?customer_id=' + customer);
  if (customer === currentCustomer() && !attempt) { cart = d.items; showCount(); }
}
// 設定某件商品的數量（絕對值，0＝移除），伺服器回傳整台購物車。
async function setQuantity(id, quantity) {
  if (attempt) throw Error('請先確認上一筆結帳結果。');
  const d = await api('/api/store/cart', {customer_id: Number(currentCustomer()), product_id: id, quantity});
  cart = d.items; showCount();
}
let renderSerial=0;
async function products() {
  const q = new URLSearchParams({page:state.pageNo,q:state.query,category:state.category});
  const d = await api('/api/store/products?'+q);
  state.products = d.items;
  const pages = Math.max(1,Math.ceil(d.total/12));
  return heading('找到下一件好物。','加入購物車不扣庫存，下單成功後才實際扣除；庫存以結帳時為準。',`<button class="button primary" data-action="cart">${icon('bag')}查看購物車</button>`) +
  `<form id="product-filter" class="toolbar"><label class="search">${icon('search')}<input name="q" aria-label="搜尋商品名稱或編號" placeholder="搜尋商品名稱或編號…" value="${esc(state.query)}"></label><select name="category" aria-label="商品分類"><option value="">全部分類</option>${state.bootstrap.categories.map(c => `<option ${state.category===c?'selected':''} value="${esc(c)}">${esc(c)}</option>`).join('')}</select><button class="button" type="submit">搜尋商品</button><span class="subtle">${num(d.total)} 件商品</span></form>
  <div class="products">${d.items.map(p => `<article class="product-card">${artwork(p)}<div class="product-info"><div class="product-id">ITEM / ${String(p.product_id).padStart(4,'0')}</div><h3>${esc(p.name)}</h3><span class="product-price">${money(p.price)}</span><span class="stock ${p.availability==='剩少量'?'stock-low':''}">${esc(stockLabel(p))}</span><div class="product-actions"><button class="button" data-action="view" data-id="${p.product_id}">瀏覽商品 ${icon('arrow')}</button><button class="button primary" aria-label="將 ${esc(p.name)} 加入購物車" data-action="add" data-id="${p.product_id}" ${p.availability==='已售完'?'disabled':''}>${icon('bag')}</button></div></div></article>`).join('')}</div>
  ${!d.items.length?'<div class="empty">找不到符合條件的商品，請試試其他關鍵字。</div>':''}
  <div class="pagination"><span>第 ${state.pageNo} / ${pages} 頁 · 每頁 12 件</span><div class="buttons"><button class="button" data-action="prev" ${state.pageNo<=1?'disabled':''}>上一頁</button><button class="button" data-action="next" ${state.pageNo>=pages?'disabled':''}>下一頁 ${icon('arrow')}</button></div></div>`;
}
async function orders() {
  const d = await api('/api/store/orders?customer_id='+currentCustomer());
  return heading('每一筆訂單，都有依據。','訂單與庫存於同一筆交易中提交；最多顯示最近 100 筆。',refreshButton) +
    `<section class="panel"><div class="panel-head"><h2>訂單紀錄</h2><span class="badge">q01 public</span></div>${orderTable(d.items)}</section>`;
}
async function render() {
  const ticket = ++renderSerial;
  const page = location.hash.slice(1);
  state.page = names[page]?page:'products';
  document.title = names[state.page]+' · 拾物商店';
  $('#crumb').textContent = names[state.page];
  document.querySelectorAll('nav [data-page]').forEach(el=>{el.classList.toggle('active',el.dataset.page===state.page); if(el.dataset.page===state.page)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  $('#main').setAttribute('aria-busy','true');
  $('#main').innerHTML='<div class="loading">正在讀取資料…</div>';
  try {
    const html = await ({products,orders}[state.page])();
    if(ticket!==renderSerial)return;
    $('#main').innerHTML=html;
  } catch(e) { if(ticket===renderSerial)$('#main').innerHTML=heading('暫時無法讀取資料','請確認本機服務已啟動。',refreshButton)+`<div class="alert error">${esc(e.message)}</div>`; }
  finally { if(ticket===renderSerial)$('#main').removeAttribute('aria-busy'); }
}
async function addProduct(p) {
  if(attempt)return toast('上一筆結帳結果尚未確認，請先開啟購物車以原識別碼重試。');
  const old = cart.find(x=>x.product_id===p.product_id);
  const limit=cartLimit(p);
  if(p.availability==='已售完' || limit===0)return toast('商品已售完。');
  if((old?.quantity||0)>=limit)return toast(`最多 ${limit} 件，庫存以結帳時為準。`);
  await setQuantity(p.product_id,(old?.quantity||0)+1);
  toast(`${p.name} 已加入購物車`);
}
async function viewProduct(id, button) {
  button.disabled=true;
  try {
    const p = await api(`/api/store/products/${id}/view`,{});
    state.products=state.products.map(x=>x.product_id===p.product_id?p:x);
    $('#product-dialog').innerHTML=`<div class="dialog-head"><h2 id="product-title">商品詳情</h2><button class="close" data-action="close" aria-label="關閉商品詳情">×</button></div><div class="dialog-body">${artwork(p)}<div class="detail-title"><h2>${esc(p.name)}</h2><strong>${money(p.price)}</strong></div><div class="detail-meta"><span>分類 ${esc(p.category)}</span><span class="${p.availability==='剩少量'?'stock-low':''}">${esc(stockLabel(p))}</span></div><p class="subtle">庫存為目前數字，結帳時由伺服器重新確認；庫存以結帳時為準。成交價亦由伺服器確認。</p><button class="button primary full" data-action="detail-add" ${p.availability==='已售完'?'disabled':''}>${icon('bag')}${p.availability==='已售完'?'已售完':'加入購物車'}</button></div>`;
    state.detail=p;
    $('#product-dialog').showModal();
  }catch(e){toast(e.message);}finally{button.disabled=false;}
}
function cartNotes(p){
  const notes=[];
  if(Number(p.added_price)!==Number(p.price))notes.push(`加入時 ${money(p.added_price)} → 目前 ${money(p.price)}`);
  if(p.stock===0)notes.push('已售完，請移除後再下單');
  else if(Number.isInteger(p.stock) && p.quantity>p.stock)notes.push(`庫存不足，目前剩 ${p.stock} 件`);
  return notes;
}
function blocked(p){return Number.isInteger(p.stock) && p.quantity>p.stock;}
function renderCart() {
  const total = cartTotalCents()/100;
  const customer = currentCustomer();
  $('#current-customer').disabled=!!attempt;
  $('#cart-dialog').innerHTML=`<div class="dialog-head"><h2 id="cart-title">你的購物車 <span class="subtle">/ ${cart.length} 件商品</span></h2><button class="close" data-action="close" aria-label="關閉購物車">×</button></div><div class="dialog-body">${!cart.length?`<div class="empty">${icon('bag')}<h3>購物車還在等待好物。</h3><p>先去逛逛，把喜歡的商品帶回來。</p><button class="button" data-action="shop">瀏覽商品 ${icon('arrow')}</button></div>${state.cartError?`<p class="checkout-note">${esc(state.cartError)}</p>`:''}`:cart.map(p=>`<div class="cart-item"><div><h3>${esc(p.name)}</h3><small>${money(p.price)} / 件 · 最多 ${cartLimit(p)} 件</small>${cartNotes(p).map(n=>`<p class="checkout-note">${esc(n)}</p>`).join('')}<div class="quantity"><button data-action="minus" data-id="${p.product_id}" aria-label="減少 ${esc(p.name)} 數量" ${p.quantity<=1?'disabled':''}>−</button><input type="number" min="1" max="${Math.max(cartLimit(p),1)}" ${cartLimit(p)===0?'disabled':''} value="${p.quantity}" data-quantity="${p.product_id}" aria-label="${esc(p.name)} 數量"><button data-action="plus" data-id="${p.product_id}" aria-label="增加 ${esc(p.name)} 數量" ${p.quantity>=cartLimit(p)?'disabled':''}>+</button></div></div><div><strong>${money(Number(p.price)*p.quantity)}</strong><p><button class="remove" data-action="remove" data-id="${p.product_id}">移除商品</button></p></div></div>`).join('')}${cart.length?`${state.cartError?`<p class="checkout-note">${esc(state.cartError)}</p>`:''}<div class="total-row"><span>預估總額</span><span>${money(total)}</span></div><p class="subtle">目前顧客：${esc(state.bootstrap.customers.find(c=>String(c.customer_id)===customer)?.name)}（購物車存在資料庫，換分頁或換瀏覽器仍在）</p><p class="checkout-note">送出後將建立真實展示訂單並扣庫存，未串接金流。最終金額以伺服器當下價格為準；庫存不足時整筆取消。</p><button class="button primary full" id="checkout-button" data-action="checkout" ${!attempt&&cart.some(blocked)?'disabled':''}>確認下單 ${icon('arrow')}</button><div id="checkout-error" class="checkout-error" role="alert"></div>`:''}</div>`;
  if(attempt) {
    $('#cart-dialog').querySelectorAll('input,select,[data-action=plus],[data-action=minus],[data-action=remove]').forEach(el=>el.disabled=true);
    $('#checkout-error').textContent='上一筆結帳結果尚未確認。請以原內容重試，系統會查回同一筆訂單，不會重複扣庫存。';
    $('#checkout-button').textContent='確認上一筆結帳結果';
  }
}
// 以分加總，避免浮點誤差；expected_total 以字串「元.角分」送出，與伺服器 numeric(10,2) 精確比對。
function cartTotalCents(){return cart.reduce((s,p)=>s+Math.round(Number(p.price)*100)*p.quantity,0);}
function centsText(c){return `${Math.floor(c/100)}.${String(c%100).padStart(2,'0')}`;}
async function checkout(button) {
  if(state.checkoutBusy)return;
  state.checkoutBusy=true;button.disabled=true;button.textContent='正在確認庫存與建立訂單…';
  const customer=currentCustomer();
  try{sessionStorage.setItem('q01-customer',customer);}catch(_){}
  if(!attempt){
    attempt={request_id:crypto.randomUUID(),customer_id:Number(customer),items:cart.map(p=>({product_id:p.product_id,quantity:p.quantity})),expected_total:centsText(cartTotalCents())};
    attemptView=cart.map(p=>({...p}));
    savePending();
  }
  $('#current-customer').disabled=true;
  $('#cart-dialog').querySelectorAll('input,select,button').forEach(el=>el.disabled=true);
  try {
    const order=await api('/api/store/orders',attempt);
    // 已下單的商品由伺服器在同一筆交易中從購物車刪除；重新載入後只剩其他商品
    attempt=null;attemptView=null;savePending();$('#current-customer').disabled=false;$('#cart-dialog').close();showOrder(order,true);
    try{await loadCart();}catch(_){cart=[];showCount();}
    await render();
  }catch(e){
    if([400,409,415].includes(e.status)){attempt=null;attemptView=null;savePending();$('#current-customer').disabled=false;}
    // 409（價格變動、庫存不足等）：訂單未成立、購物車不變，重新載入現價／庫存，由使用者確認後再決定是否重新下單。
    let refreshed=false;
    if(e.status===409){state.checkoutBusy=false;state.cartError='';try{await loadCart();refreshed=true;}catch(_){}}
    renderCart();
    $('#checkout-error').textContent=e.message+(attempt?' 請以原內容重試，避免建立重複訂單。':'')+(refreshed?' 購物車已更新為目前價格與庫存，請確認後再決定是否重新下單。':'');
  }
  finally{state.checkoutBusy=false;}
}
async function viewOrder(id){
  const customer=currentCustomer(),ticket=renderSerial;
  const order=await api('/api/store/orders/'+id+'?customer_id='+customer);
  if(customer===currentCustomer()&&ticket===renderSerial)showOrder(order);
}
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-action]');if(!b)return;
  const action=b.dataset.action,id=Number(b.dataset.id);
  if(state.checkoutBusy && b.closest('#cart-dialog'))return;
  try {
    if(action==='close')b.closest('dialog').close();
    if(action==='refresh')await render();
    if(action==='cart'){
      if(!state.bootstrap)return toast('商店尚未連線，請先重新整理。');
      state.cartError='';renderCart();$('#cart-dialog').showModal();
      try{await loadCart();}catch(err){state.cartError='無法從伺服器載入購物車，目前顯示上次取得的內容；成交價仍以伺服器為準。';}
      if(!state.checkoutBusy)renderCart();
    }
    if(action==='shop'){$('#cart-dialog').close();location.hash='products';}
    if(action==='view')await viewProduct(id,b);
    if(action==='add')await addProduct(state.products.find(p=>p.product_id===id));
    if(action==='detail-add')await addProduct(state.detail);
    if(['plus','minus','remove'].includes(action)){
      if(attempt)return toast('請先確認上一筆結帳結果。');
      const p=cart.find(x=>x.product_id===id);if(!p)return;
      const next=action==='remove'?0:action==='plus'?Math.min(p.quantity+1,cartLimit(p)):Math.max(p.quantity-1,1);
      try{if(next!==p.quantity)await setQuantity(id,next);}finally{renderCart();}
    }
    if(action==='prev'){state.pageNo--;await render();}
    if(action==='next'){state.pageNo++;await render();}
    if(action==='checkout')await checkout(b);
    if(action==='order')await viewOrder(id);
  } catch(err){toast(err.message);}
});
document.addEventListener('submit',e=>{
  if(e.target.id==='product-filter'){e.preventDefault();const d=new FormData(e.target);state.query=d.get('q');state.category=d.get('category');state.pageNo=1;render();}
});
document.addEventListener('change',async e=>{
  if(e.target.matches('[data-quantity]')){
    if(attempt || state.checkoutBusy){renderCart();return;}
    const p=cart.find(x=>x.product_id===Number(e.target.dataset.quantity));const n=Number(e.target.value);
    try{if(p){const q=Math.max(1,Math.min(cartLimit(p),Number.isFinite(n)?Math.trunc(n):1));if(q!==p.quantity)await setQuantity(p.product_id,q);}}
    catch(err){toast(err.message);}
    renderCart();
  }
  if(e.target.id==='current-customer'){
    // 切換顧客＝切換購物車（每位顧客一台，存在資料庫）
    try{sessionStorage.setItem('q01-customer',e.target.value);}catch(_){}
    $('#order-dialog').close();cart=[];showCount();
    try{await loadCart();}catch(err){toast(err.message);}
    render();
  }
});
$('#cart-dialog').addEventListener('cancel',e=>{if(state.checkoutBusy)e.preventDefault();});
window.addEventListener('hashchange',render);
async function start(){
  showCount();
  try{
    state.bootstrap=await api('/api/store/bootstrap');
    let remembered=null;try{remembered=sessionStorage.getItem('q01-customer');}catch(_){}
    const saved=String(attempt?.customer_id || remembered || state.bootstrap.customers[0]?.customer_id || '');
    $('#current-customer').innerHTML=state.bootstrap.customers.map(c=>`<option value="${c.customer_id}" ${String(c.customer_id)===saved?'selected':''}>${esc(c.name)}</option>`).join('');
    $('#current-customer').value=saved;
    $('#current-customer').disabled=!!attempt;
    try{await loadCart();}catch(_){}
    await render();
  }
  catch(e){$('#main').innerHTML=heading('商店尚未連線','請先啟動本機展示服務。')+`<div class="alert error">${esc(e.message)}</div>`;}
}
start();
