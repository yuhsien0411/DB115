'use strict';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = n => `<svg aria-hidden="true"><use href="#i-${n}"/></svg>`;
const num = v => v == null || v === '' ? '—' : Number(v).toLocaleString('zh-TW', {maximumFractionDigits: 2});
const money = v => `NT$ ${Number(v).toLocaleString('zh-TW', {minimumFractionDigits:2, maximumFractionDigits:2})}`;
const time = v => v ? new Date(v).toLocaleString('zh-TW', {hour12:false}) : '尚無回寫';
// orders.order_date 是 date（YYYY-MM-DD），直接顯示，避免 new Date() 以 UTC 解析後跨日
const day = v => v ? String(v) : '—';
// seed 有 completed／shipped／cancelled；網站與下單實驗經 place_order() 成立的是 pending
const STATUS = {pending:'已成立', completed:'已完成', shipped:'已出貨', cancelled:'已取消'};
const statusText = v => STATUS[v] || String(v ?? '—');
let toastTimer;
function toast(text) { $('#toast').textContent = text; $('#toast').classList.add('visible'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').classList.remove('visible'), 3800); }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const body = await response.json();
  if (!response.ok) { const error = new Error(body.error || '連線失敗，請稍後重試。'); error.status=response.status; throw error; }
  return body;
}
function heading(title, description, action='') { return `<div class="page-heading"><div><div class="eyebrow">SHIWU / ${state.page.toUpperCase()}</div><h1>${title}</h1><p class="subtle">${description}</p></div>${action}</div>`; }
const refreshButton = `<button class="button" data-action="refresh">${icon('refresh')}重新整理</button>`;
function stat(label, value, note, ico) { return `<div class="stat"><div class="stat-top">${label}<span class="stat-icon">${icon(ico)}</span></div><div class="stat-value">${value}</div><div class="stat-note">${note}</div></div>`; }
function artwork(p) {
  const cat = p.category;
  let css = 'books', drawing = '<path d="M5 6h7c3 0 4 2 4 2s1-2 4-2h7v23h-7c-3 0-4 2-4 2s-1-2-4-2H5V6Z"/><path d="M16 8v23M9 12h3M9 16h3M20 12h3M20 16h3"/>';
  if(cat==='3C') { css='tech'; drawing='<rect x="5" y="5" width="22" height="17" rx="2"/><path d="M16 22v5M10 28h12M8 19h16"/>'; }
  if(cat==='服飾') { css='clothes'; drawing='<path d="m11 5-7 5 4 6 3-2v14h10V14l3 2 4-6-7-5c-1 5-9 5-10 0Z"/>'; }
  if(cat==='家電') { css='home'; drawing='<rect x="9" y="3" width="15" height="26" rx="3"/><path d="M9 13h15M12 7v3M12 17v4"/>'; }
  if(!['書籍','3C','服飾','家電'].includes(cat)) { css='home'; drawing='<path d="M6 13h20v15H6zM4 9h24v5H4zM16 9v19M16 9C6 10 7 0 12 4l4 5Zm0 0c10 1 9-9 4-5l-4 5Z"/>'; }
  return `<div class="product-art ${css}"><span class="category">${esc(cat)}</span><svg viewBox="0 0 32 32" aria-hidden="true">${drawing}</svg></div>`;
}
function orderTable(items, recent=false) {
  if(!items.length) return `<div class="empty">${icon('list')}<h3>尚無訂單紀錄</h3><p>完成結帳後，訂單就會出現在這裡。</p></div>`;
  return `<div class="table-wrap"><table><thead><tr><th>訂單編號</th><th>顧客</th><th>成立日期</th><th>訂單金額</th><th>狀態</th><th></th></tr></thead><tbody>${items.slice(0,recent?5:100).map(o => `<tr><td>#${String(o.order_id).padStart(5,'0')}</td><td>${esc(o.customer_name)}</td><td>${esc(day(o.order_date))}</td><td>${money(o.amount)}</td><td><span class="badge">${esc(statusText(o.status))}</span></td><td><button class="table-link" data-action="order" data-id="${o.order_id}">查看明細 →</button></td></tr>`).join('')}</tbody></table></div>`;
}
function showOrder(o, success=false) {
  $('#order-dialog').innerHTML=`<div class="dialog-head"><h2 id="order-title">訂單 #${String(o.order_id).padStart(5,'0')}</h2><button class="close" data-action="close" aria-label="關閉訂單明細">×</button></div><div class="dialog-body">${success?`<div class="order-success">✓ 訂單已成立，商品庫存已在同一筆交易中扣除。</div>`:''}<p class="subtle">顧客 ${esc(o.customer_name)}<br>成立日期 ${esc(day(o.order_date))}<br>狀態 ${esc(statusText(o.status))}${o.status==='pending'?'（未付款）':''}</p><table class="order-lines"><thead><tr><th>商品</th><th>數量</th><th>成交單價</th></tr></thead><tbody>${o.items.map(p=>`<tr><td>${esc(p.name)}</td><td>${p.quantity}</td><td>${money(p.unit_price)}</td></tr>`).join('')}</tbody></table><div class="total-row"><span>訂單總額</span><span>${money(o.amount)}</span></div><p class="subtle">成交單價已保存於訂單明細，之後的商品調價不影響這筆訂單。</p></div>`;
  $('#order-dialog').showModal();
}
