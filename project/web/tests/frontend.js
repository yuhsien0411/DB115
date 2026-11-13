// 純 JavaScript + 簡化 DOM，可由 Node 或既有 V8 執行；不取代瀏覽器驗收。
async function runFrontendChecks(sources) {
  const checks=[];
  const product={product_id:1,name:'測試商品',category:'書籍',price:13,availability:'剩少量',stock:3};
  const order={order_id:1,customer_id:1,customer_name:'測試顧客',order_date:'2026-10-24',status:'pending',amount:13,items:[{name:product.name,quantity:1,unit_price:13}]};
  const bootstrap={categories:['書籍'],customers:[{customer_id:1,name:'測試顧客'},{customer_id:2,name:'另一顧客'}]};
  function environment(kind,savedPending,options={}){
    const elements=new Map(),events=new Map(),memory=new Map(),sent=[],requests=[],intervals=new Map();
    // D38：購物車在伺服器（cart_items）；瀏覽器只存結果未確認的結帳。server 可在多個 environment 共用，模擬換瀏覽器。
    const server=options.server||{carts:new Map()};
    const cartOf=c=>{if(!server.carts.has(c))server.carts.set(c,new Map());return server.carts.get(c);};
    if(savedPending)memory.set('q01-checkout',JSON.stringify(savedPending));
    for(const [k,v] of Object.entries(options.memory||{}))memory.set(k,v);
    if(options.autoRefresh)memory.set('q01_admin_autorefresh','true');
    let currentProduct=product,intervalId=0,blockedPath='',releaseFetch=null,heldPath='',heldFetch=null;
    let dashboardViews=0,inventoryStock=2,reducedMotion=false;
    const element=selector=>{
      if(!elements.has(selector)){
        const attributes=new Map(),classes=new Set();let html='',liveValues=[];
        const el={
          textContent:'',value:'1',disabled:false,open:false,htmlWrites:0,
          classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),toggle(c,on){if(on)classes.add(c);else classes.delete(c);}},
          setAttribute:(k,v)=>attributes.set(k,v),getAttribute:k=>attributes.get(k),removeAttribute:k=>attributes.delete(k),
          addEventListener(){},querySelectorAll:s=>s==='[data-live-value]'?liveValues:[],
          close(){this.open=false;},showModal(){this.open=true;}
        };
        Object.defineProperty(el,'innerHTML',{get:()=>html,set(value){
          html=value;el.htmlWrites++;
          liveValues=[...html.matchAll(/<span data-live-value="([^"]+)">([^<]*)<\/span>/g)].map(([,key,text])=>{
            const marked=new Set();return {dataset:{liveValue:key},textContent:text,classList:{add:c=>marked.add(c),contains:c=>marked.has(c)}};
          });
        }});
        elements.set(selector,el);
      }return elements.get(selector);
    };
    const doc={hidden:false,activeElement:null,querySelector:s=>{
      if(['#autorefresh-toggle','#autorefresh-status'].includes(s) && !element('#main').innerHTML.includes(`id="${s.slice(1)}"`))return null;
      return element(s);
    },querySelectorAll(){return [];},addEventListener:(k,fn)=>events.set(k,fn),title:''};
    const storage={getItem:k=>{if(options.storageReadThrows)throw Error('儲存讀取遭封鎖');return memory.get(k)??null;},setItem:(k,v)=>{if(options.storageWriteThrows)throw Error('儲存寫入遭封鎖');memory.set(k,v);},removeItem:k=>{if(options.storageWriteThrows)throw Error('儲存寫入遭封鎖');memory.delete(k);}};
    const location={hash:kind==='store'?'#products':'#overview'};
    let failCheckout=true,conflictCheckout=false,delayedOrder=null,releaseOrder=null;
    const fetch=async(path,opts)=>{
      requests.push(path);let body;
      if(!path.startsWith('/api/'+kind+'/'))throw Error('跨區 API：'+path);
      if(heldFetch && path.includes(heldPath))await heldFetch;
      if(blockedPath && path.includes(blockedPath))throw Error('測試更新失敗');
      const cartBody=c=>({customer_id:c,items:[...cartOf(c)].map(([,x])=>({...currentProduct,quantity:x.quantity,added_price:x.added_price})),total:0});
      if(path.startsWith('/api/store/cart?'))body=cartBody(Number(path.match(/customer_id=(\d+)/)[1]));
      else if(path==='/api/store/cart'&&opts?.method==='POST'){
        const d=JSON.parse(opts.body),c=cartOf(d.customer_id);
        if(Number.isInteger(currentProduct.stock)&&d.quantity>currentProduct.stock)return {ok:false,status:409,json:async()=>({error:`${currentProduct.name} 庫存不足（目前剩 ${currentProduct.stock} 件），無法放入 ${d.quantity} 件。`})};
        if(d.quantity===0)c.delete(d.product_id);else c.set(d.product_id,{quantity:d.quantity,added_price:c.get(d.product_id)?.added_price??currentProduct.price});
        body=cartBody(d.customer_id);
      }
      else if(path.endsWith('/dashboard'))body={products:1,stock:2,pg_views:dashboardViews,orders:0,revenue:0,top:[{...product,view_count:0}],counter:'pg'};
      else if(path.includes('/products/lookup?'))body={items:[currentProduct]};
      else if(path.includes('/products?'))body={items:kind==='store'?[currentProduct]:[{...product,stock:inventoryStock,view_count:0,sold:1}],total:kind==='store'?1:36,page:Number(path.match(/[?&]page=(\d+)/)?.[1]||1)};
      else if(path.endsWith('/view'))body=currentProduct;
      else if(path.endsWith('/experiments'))body=options.experiments??{runs:[],failures:[]};
      else if(path==='/api/store/orders'&&opts?.method==='POST'){
        sent.push(JSON.parse(opts.body));
        if(conflictCheckout)return {ok:false,status:409,json:async()=>({error:'商品價格已變動：測試'})};
        if(failCheckout)return {ok:false,status:503,json:async()=>({error:'測試連線中斷'})};
        const d=JSON.parse(opts.body);for(const i of d.items)cartOf(d.customer_id).delete(i.product_id);  // 伺服器在同一交易清掉已下單商品
        body=order;
      }else if(path.includes('/orders/')){if(delayedOrder)await delayedOrder;body=order;}
      else if(path.includes('/orders'))body={items:[order]};
      else if(path.endsWith('/bootstrap'))body=bootstrap;
      else throw Error('未測試路由：'+path);
      return {ok:true,status:200,json:async()=>body};
    };
    class Query{constructor(data){this.data=data;}toString(){return Object.entries(this.data).map(([k,v])=>encodeURIComponent(k)+'='+encodeURIComponent(v)).join('&');}}
    const code=sources.common+'\n'+sources[kind].replace(/\nstart\(\);\s*$/,'');
    const returns=kind==='store'?'state,start,render,addProduct,renderCart,checkout,viewProduct,viewOrder,money,getCart:()=>cart,getAttempt:()=>attempt':'state,start,render,money';
    const win={scrollX:0,scrollY:0,scrollCalls:[],addEventListener(){},scrollTo({left,top}){this.scrollCalls.push([left,top]);this.scrollX=left;this.scrollY=top;},
      matchMedia:()=>({matches:reducedMotion}),
      setInterval(fn,ms){if(ms!==2000)throw Error('自動更新間隔不是 2 秒');const id=++intervalId;intervals.set(id,fn);return id;},clearInterval:id=>intervals.delete(id)};
    const app=new Function('document','window','localStorage','sessionStorage','fetch','location','crypto','setTimeout','clearTimeout','URLSearchParams',code+'\nreturn {'+returns+'};')(
      doc,win,storage,storage,fetch,location,{randomUUID:()=> '11111111-1111-4111-8111-111111111111'},()=>0,()=>{},Query);
    return {app,element,location,events,requests,sent,storage,doc,win,intervals,server,
      tick:async()=>{for(const fn of intervals.values())await fn();},setBlockedPath:p=>{blockedPath=p;},
      holdFetch(path){heldPath=path;heldFetch=new Promise(resolve=>{releaseFetch=resolve;});},releaseFetch(){releaseFetch();heldFetch=null;},
      setViews:n=>{dashboardViews=n;},setStock:n=>{inventoryStock=n;},setReducedMotion:v=>{reducedMotion=v;},
      setProduct:p=>{currentProduct=p;},setFail:v=>{failCheckout=v;},setConflict:v=>{conflictCheckout=v;},holdOrder(){delayedOrder=new Promise(resolve=>{releaseOrder=resolve;});},releaseOrder(){releaseOrder();delayedOrder=null;}};
  }
  const store=environment('store');await store.app.start();
  if(store.app.money(1234)!=='NT$ 1,234.00')throw Error('金額應固定兩位小數');
  if(store.app.money('1234.5')!=='NT$ 1,234.50')throw Error('字串金額格式錯誤');
  for(const page of ['products','orders']){
    store.location.hash='#'+page;await store.app.render();
    const html=store.element('#main').innerHTML;
    if(html.includes('暫時無法讀取資料'))throw Error(page+': '+html);
    if(/營收|營運總覽|瀏覽 \d+ 次|view_count|live_count|sold|counter|Redis|PostgreSQL|對照實驗/.test(html))throw Error('前台洩漏後台內容');
    if(page==='products' && (!html.includes('剩少量 · 剩 3 件') || !html.includes('stock-low') || !html.includes('庫存以結帳時為準')))throw Error('商品卡缺少剩餘數／少量醒目色／庫存提示');
    checks.push('前台 '+page+' 樣板通過');
  }
  store.element('#current-customer').value='2';
  await store.events.get('change')({target:{id:'current-customer',value:'2',matches:()=>false}});
  await store.app.render();
  if(!store.requests.includes('/api/store/orders?customer_id=2'))throw Error('切換顧客未限定訂單');
  store.element('#current-customer').value='1';
  await store.app.viewProduct(1,{disabled:false});
  if(!store.element('#product-dialog').innerHTML.includes('剩少量 · 剩 3 件') || !store.element('#product-dialog').innerHTML.includes('stock-low') || !store.element('#product-dialog').innerHTML.includes('庫存為目前數字'))throw Error('詳情缺少庫存數／少量醒目色／即時性說明');
  const stockCart=environment('store',null,{memory:{'q01-cart':JSON.stringify({items:[{product_id:1,name:'舊',price:13,quantity:2}]})}});await stockCart.app.start();
  if(stockCart.storage.getItem('q01-cart')!==null || stockCart.app.getCart().length)throw Error('D38 前的 localStorage 購物車未清除或被沿用');
  const click=(env,action)=>env.events.get('click')({target:{closest:()=>({dataset:{action,id:'1'},closest:()=>null})}});
  const quantity=(env,n)=>env.events.get('change')({target:{id:'quantity',value:String(n),dataset:{quantity:'1'},matches:()=>true}});
  await stockCart.app.addProduct(product);
  await click(stockCart,'plus');await click(stockCart,'plus');await click(stockCart,'plus');
  await stockCart.app.addProduct(product);stockCart.app.renderCart();
  if(stockCart.app.getCart()[0].quantity!==3 || stockCart.app.getCart()[0].stock!==3 || stockCart.server.carts.get(1).get(1).quantity!==3)throw Error('加入／+ 按鈕未限制庫存或未寫到伺服器');
  const cartHtml=stockCart.element('#cart-dialog').innerHTML;
  if(!cartHtml.includes('最多 3 件') || !cartHtml.includes('max="3"') || !/data-action="plus"[^>]*disabled/.test(cartHtml))throw Error('缺少 max／上限提示／停用 +');
  for(const [input,expected] of [[99,3],[0,1],[-2,1],[2.8,2],['bad',1]]){
    await quantity(stockCart,input);
    if(stockCart.app.getCart()[0].quantity!==expected || stockCart.server.carts.get(1).get(1).quantity!==expected)throw Error('手動輸入未夾在庫存上限內：'+input);
  }
  await quantity(stockCart,3);
  // 換一個瀏覽器（不同 localStorage、同一個伺服器）購物車仍在
  const restored=environment('store',null,{server:stockCart.server});await restored.app.start();
  if(restored.app.getCart()[0]?.quantity!==3 || !restored.requests.includes('/api/store/cart?customer_id=1') || restored.storage.getItem('q01-checkout')!==null)throw Error('換瀏覽器後購物車未由伺服器載入');
  // 另一位顧客的購物車是空的
  restored.element('#current-customer').value='2';
  await restored.events.get('change')({target:{id:'current-customer',value:'2',matches:()=>false}});
  if(restored.app.getCart().length || !restored.requests.includes('/api/store/cart?customer_id=2'))throw Error('切換顧客未切換購物車');
  stockCart.setProduct({...product,stock:1});await click(stockCart,'cart');
  if(stockCart.app.getCart()[0].stock!==1 || !stockCart.element('#cart-dialog').innerHTML.includes('庫存不足，目前剩 1 件') || !/data-action="checkout"[^>]*disabled/.test(stockCart.element('#cart-dialog').innerHTML))throw Error('庫存減少後未提示／仍可下單');
  await quantity(stockCart,1);
  if(stockCart.app.getCart()[0].quantity!==1 || /data-action="checkout"[^>]*disabled/.test(stockCart.element('#cart-dialog').innerHTML))throw Error('調回庫存內後仍不能下單');
  stockCart.setProduct({...product,stock:0,availability:'已售完'});
  await stockCart.app.render();
  if(!/data-action="add"[^>]*disabled/.test(stockCart.element('#main').innerHTML))throw Error('售完卡片仍可加入');
  await stockCart.app.viewProduct(1,{disabled:false});
  if(!/data-action="detail-add"[^>]*disabled/.test(stockCart.element('#product-dialog').innerHTML) || stockCart.element('#product-dialog').innerHTML.includes('剩 0 件'))throw Error('售完詳情狀態／按鈕錯誤');
  await click(stockCart,'cart');
  if(!stockCart.element('#cart-dialog').innerHTML.includes('已售完，請移除後再下單') || !/data-action="checkout"[^>]*disabled/.test(stockCart.element('#cart-dialog').innerHTML))throw Error('售完購物車未提示／仍可下單');
  await click(stockCart,'remove');
  if(stockCart.app.getCart().length || stockCart.server.carts.get(1).size)throw Error('移除商品未寫到伺服器');
  const savedAttempt={request_id:'11111111-1111-4111-8111-111111111111',customer_id:1,items:[{product_id:1,quantity:3}],expected_total:'39.00'};
  const retry=environment('store',{attempt:savedAttempt,view:[{...product,stock:1,quantity:3,added_price:13}]});await retry.app.start();
  if(retry.app.getCart()[0].quantity!==3 || JSON.stringify(retry.app.getAttempt())!==JSON.stringify(savedAttempt) || retry.requests.some(r=>r.startsWith('/api/store/cart')))throw Error('重新整理時改動未確認請求／購物車');
  const big=environment('store');big.setProduct({...product,stock:undefined});await big.app.start();await big.app.addProduct({...product,stock:undefined});
  await quantity(big,9999);await click(big,'plus');await click(big,'plus');await quantity(big,10001);big.app.renderCart();
  if(big.app.getCart()[0].quantity!==10000 || !big.element('#cart-dialog').innerHTML.includes('max="10000"'))throw Error('沒有庫存數時 10000 上限失效');
  const oldApi=environment('store');oldApi.setProduct({...product,stock:undefined});await oldApi.app.start();
  await oldApi.app.viewProduct(1,{disabled:false});
  if(/undefined|NaN/.test(oldApi.element('#main').innerHTML+oldApi.element('#product-dialog').innerHTML))throw Error('仍執行舊 API 時庫存顯示損壞');
  stockCart.setProduct({...product,stock:20,availability:'有貨'});await stockCart.app.render();await stockCart.app.viewProduct(1,{disabled:false});
  if(!stockCart.element('#main').innerHTML.includes('有貨 · 剩 20 件') || !stockCart.element('#product-dialog').innerHTML.includes('有貨 · 剩 20 件'))throw Error('有貨缺少剩餘數');
  checks.push('商品卡／詳情剩餘數、少量醒目、售完停用、購物車存伺服器（換瀏覽器仍在、依顧客區分）、上限／手動夾值、庫存不足與售完停用下單通過');
  const priceCart=environment('store');await priceCart.app.start();await priceCart.app.addProduct(product);
  priceCart.setProduct({...product,price:25});await click(priceCart,'cart');
  const priceHtml=priceCart.element('#cart-dialog').innerHTML;
  if(priceCart.app.getCart()[0].price!==25 || !priceHtml.includes('NT$ 25.00') || !priceHtml.includes('加入時 NT$ 13.00 → 目前 NT$ 25.00') || priceCart.requests.some(r=>r.endsWith('/view')))throw Error('開啟購物車未顯示現價與加入時價格');
  priceCart.setBlockedPath('/api/store/cart');await click(priceCart,'cart');
  if(!priceCart.element('#cart-dialog').innerHTML.includes('無法從伺服器載入購物車') || priceCart.app.getCart()[0].price!==25)throw Error('載入失敗時未提示或破壞購物車');
  checks.push('購物車開啟時由伺服器取現價、顯示加入時價格、唯讀不計數、失敗提示通過');
  const conflict=environment('store');await conflict.app.start();await conflict.app.addProduct(product);await conflict.app.addProduct(product);
  conflict.setProduct({...product,price:20});conflict.setFail(false);conflict.setConflict(true);
  conflict.app.renderCart();await conflict.app.checkout(conflict.element('#checkout-button'));
  const conflictHtml=conflict.element('#cart-dialog').innerHTML,conflictError=conflict.element('#checkout-error').textContent;
  if(conflict.sent[0].expected_total!=='26.00')throw Error('下單未送出購物車預估總額：'+conflict.sent[0].expected_total);
  if(conflict.app.getAttempt()!==null || conflict.app.getCart().length!==1 || conflict.app.getCart()[0].price!==20 || conflict.server.carts.get(1).size!==1)throw Error('金額不符未保留購物車並刷新現價');
  if(!conflictHtml.includes('加入時 NT$ 13.00 → 目前 NT$ 20.00') || !conflictHtml.includes('NT$ 40.00') || !conflictError.includes('重新下單') || conflict.app.state.checkoutBusy)throw Error('金額不符未提示使用者確認');
  conflict.setConflict(false);await conflict.app.checkout(conflict.element('#checkout-button'));
  if(conflict.sent.length!==2 || conflict.sent[1].expected_total!=='40.00' || conflict.sent[1].request_id===undefined || conflict.app.getCart().length || conflict.server.carts.get(1).size)throw Error('確認後重新下單失敗');
  checks.push('下單送出預估總額；金額不符時不成立、刷新現價並由使用者確認後重新下單；成功後伺服器購物車清空通過');
  store.element('#current-customer').value='1';
  await store.app.addProduct(product);store.app.renderCart();
  await store.app.checkout(store.element('#checkout-button'));
  if(!store.app.getAttempt()||!store.element('#current-customer').disabled || !store.storage.getItem('q01-checkout'))throw Error('結果不明時未鎖定顧客／請求或未保存');
  await store.app.addProduct(product);
  store.setProduct({...product,stock:0,availability:'已售完'});await store.app.viewProduct(1,{disabled:false});await click(store,'cart');
  if(store.app.getCart()[0].stock!==3)throw Error('結果不明時重新載入了待確認購物車');
  if(store.app.getCart()[0].quantity!==1 || store.server.carts.get(1).get(1).quantity!==1)throw Error('結果不明時仍允許更改商品');
  store.setFail(false);await store.app.checkout(store.element('#checkout-button'));
  if(store.app.getCart().length||store.app.getAttempt()!==null||store.storage.getItem('q01-checkout')!==null)throw Error('成功後未清空');
  if(store.sent.length!==2||JSON.stringify(store.sent[0])!==JSON.stringify(store.sent[1]))throw Error('重試內容改變');
  if(store.element('#current-customer').disabled)throw Error('成功後顧客仍被鎖定');
  store.holdOrder();store.element('#order-dialog').innerHTML='';
  const pending=store.app.viewOrder(1);
  store.element('#current-customer').value='2';
  await store.events.get('change')({target:{id:'current-customer',value:'2',matches:()=>false}});
  store.releaseOrder();await pending;
  if(store.element('#order-dialog').innerHTML!=='')throw Error('切換顧客後仍顯示先前顧客的延遲明細');
  checks.push('整數格式、顧客切換與延遲回應、詳情與購物車原請求重試通過');
  const admin=environment('admin');await admin.app.start();
  for(const page of ['overview','products','orders','counter','experiments','requirements']){
    admin.location.hash='#'+page;await admin.app.render();
    const html=admin.element('#main').innerHTML,eligible=['overview','products','orders','counter'].includes(page);
    if(html.includes('暫時無法讀取資料'))throw Error('後台 '+page+' 樣板失敗');
    if(html.includes('data-action="autorefresh"')!==eligible)throw Error('後台 '+page+' 自動更新按鈕範圍錯誤');
    if(eligible && !html.includes('aria-pressed="false"'))throw Error('自動更新預設未關閉');
    checks.push('後台 '+page+' 樣板與自動更新按鈕範圍通過');
  }
  const experiments=environment('admin',null,{experiments:{
    runs:['formal','smoke'].map(source=>({source,backend:'pg',scenario:'hot80',concurrency:'50',warmup_s:30,measured_s:60,success_ops_per_s:100,p95_ms:10,successful_ops:6000,error_ops:0,unknown_ops:0,run_id:source+'-run',notes:source+'-run-notes'})),
    failures:['formal','smoke'].map(source=>({source,flush_interval_s:1,acknowledged_N:10,pg_increment_P:9,pg_gap_N_minus_P:1,notes:source+'-failure-notes'}))
  }});
  experiments.location.hash='#experiments';await experiments.app.start();
  const sourceSelect=()=>experiments.element('#main').innerHTML.match(/<select id="experiment-source">([\s\S]*?)<\/select>/)?.[1]||'';
  if(experiments.app.state.source!=='formal' || !/<option value="formal" selected>正式量測<\/option>/.test(sourceSelect()) || /<option value="smoke" selected>/.test(sourceSelect()))throw Error('對照實驗未預設選取正式量測');
  const checkSource=source=>{
    const html=experiments.element('#main').innerHTML,other=source==='formal'?'smoke':'formal';
    if(!html.includes(source+'-run-notes') || !html.includes(source+'-failure-notes') || html.includes(other+'-run-notes') || html.includes(other+'-failure-notes'))throw Error('對照實驗來源未同步篩選量測與故障資料：'+source);
  };
  checkSource('formal');
  for(const source of ['smoke','formal']){
    experiments.events.get('change')({target:{id:'experiment-source',value:source}});await experiments.app.render();
    if(experiments.app.state.source!==source || !sourceSelect().includes(`value="${source}" selected`))throw Error('對照實驗來源下拉切換失敗：'+source);
    checkSource(source);
  }
  checks.push('對照實驗預設正式量測、下拉切換正式／煙霧與量測／故障資料來源篩選通過');
  const requirements=admin.element('#main').innerHTML.match(/<article class="panel requirement">[\s\S]*?<\/article>/g)||[];
  if(!requirements[1]?.includes('<span class="badge amber">主矩陣已完成，故障測試待補</span>') || !requirements[1].includes('<h3>02 / 題 01 對照實驗</h3>') || !requirements[1].includes('<p>兩方案 × 50／200／500 併發 × hot80／uniform1000（另加 hot5），每格 3 輪、暖機 30 秒＋正式 60 秒，已保留原始 log；Redis 故障少算量 T=1、5 各需 3 次，目前各 1 次。</p>'))throw Error('交付檢查 02 卡狀態、待完成色或說明不符');
  checks.push('交付檢查 02 卡標題、新狀態、完整說明與琥珀色標籤通過');
  const auto=environment('admin',null,{autoRefresh:true});await auto.app.start();
  if(auto.element('#autorefresh-toggle').getAttribute('aria-pressed')!=='true' || auto.intervals.size!==1)throw Error('未還原自動更新偏好／計時器');
  if(!/^每 2 秒更新 · \d{2}:\d{2}:\d{2}$/.test(auto.element('#autorefresh-status').textContent))throw Error('缺少更新頻率與最後成功時間');
  auto.setViews(9);auto.win.scrollX=12;auto.win.scrollY=480;await auto.tick();
  if(!auto.element('#main').innerHTML.includes('data-live-value="views">9</span>') || JSON.stringify(auto.win.scrollCalls.at(-1))!=='[12,480]')throw Error('未更新統計／保持捲動位置');
  if(!auto.element('#main').querySelectorAll('[data-live-value]').some(el=>el.dataset.liveValue==='views' && el.classList.contains('value-changed')))throw Error('變動數值未標示');
  auto.setReducedMotion(true);auto.setViews(10);await auto.tick();
  if(auto.element('#main').querySelectorAll('[data-live-value]').some(el=>el.classList.contains('value-changed')))throw Error('減少動態效果時仍標示動畫');
  auto.setReducedMotion(false);
  const noRequest=async(message)=>{const before=auto.requests.length;await auto.tick();if(auto.requests.length!==before)throw Error(message);};
  auto.doc.hidden=true;await noRequest('背景分頁仍更新');auto.doc.hidden=false;
  auto.element('#order-dialog').open=true;await noRequest('明細開啟仍更新');auto.element('#order-dialog').open=false;
  const batched=environment('admin',null,{experiments:{
    batches:['20261106','20261024'],
    runs:['20261106','20261024'].map(batch=>({source:'formal',batch,backend:'pg',scenario:'hot80',concurrency:'50',warmup_s:30,measured_s:60,success_ops_per_s:100,p95_ms:10,successful_ops:6000,error_ops:0,unknown_ops:0,run_id:'run-'+batch,notes:'notes-'+batch})),
    failures:['20261106','20261024'].map(batch=>({source:'formal',batch,flush_interval_s:1,acknowledged_N:10,pg_increment_P:9,pg_gap_N_minus_P:1,notes:'fail-'+batch}))
  }});
  batched.location.hash='#experiments';await batched.app.start();
  const onlyBatch=b=>{const html=batched.element('#main').innerHTML,o=b==='20261106'?'20261024':'20261106';
    if(!html.includes('notes-'+b)||!html.includes('fail-'+b)||html.includes('notes-'+o)||html.includes('fail-'+o)||!html.includes(`<option value="${b}" selected>`))throw Error('正式量測批次未分開顯示：'+b);};
  onlyBatch('20261106');
  batched.events.get('change')({target:{id:'experiment-batch',value:'20261024'}});await batched.app.render();onlyBatch('20261024');
  checks.push('正式量測依批次（results/<日期>/）分開顯示、預設最新一批、可切換通過');
  for(const page of ['experiments','requirements']){
    auto.location.hash='#'+page;await auto.app.render();await noRequest(page+' 計時器仍重畫');
  }
  auto.location.hash='#products';Object.assign(auto.app.state,{pageNo:2,query:'測試',category:'書籍',availability:'low',sort:'stock_asc'});await auto.app.render();
  auto.doc.activeElement={closest:s=>s==='#inventory-filter'?{}:null};await noRequest('庫存篩選輸入中仍更新');auto.doc.activeElement=null;
  auto.setStock(1);const beforeInventory=auto.requests.length;await auto.tick();
  if(auto.requests[beforeInventory]!=='/api/admin/products?page=2&q=%E6%B8%AC%E8%A9%A6&category=%E6%9B%B8%E7%B1%8D&availability=low&sort=stock_asc' || auto.app.state.pageNo!==2)throw Error('庫存更新遺失頁碼／篩選條件');
  if(!auto.element('#main').querySelectorAll('[data-live-value]').some(el=>el.dataset.liveValue==='stock-1' && el.classList.contains('value-changed')))throw Error('庫存變動未標示');
  auto.holdFetch('/products?');const pendingRefresh=auto.tick();await noRequest('前一次重畫未完成仍送出更新');
  const beforeFocus=auto.element('#main').innerHTML;auto.doc.activeElement={closest:()=>({})};auto.releaseFetch();await pendingRefresh;
  if(auto.element('#main').innerHTML!==beforeFocus)throw Error('請求途中開始輸入仍重畫');auto.doc.activeElement=null;
  auto.location.hash='#overview';await auto.app.render();
  const beforeFailure=auto.element('#main').innerHTML,beforeFailureWrites=auto.element('#main').htmlWrites,lastSuccess=auto.element('#autorefresh-status').textContent;
  auto.setViews(20);auto.setBlockedPath('/orders');await auto.tick();
  if(auto.element('#main').htmlWrites!==beforeFailureWrites || auto.element('#main').innerHTML!==beforeFailure || auto.element('#autorefresh-status').textContent!==lastSuccess+' · 更新失敗')throw Error('更新失敗未保留完整畫面／成功時間／錯誤提示');
  auto.setBlockedPath('');await auto.tick();
  if(auto.element('#autorefresh-status').textContent.includes('更新失敗') || !auto.element('#main').innerHTML.includes('data-live-value="views">20</span>'))throw Error('更新成功未復原');
  auto.holdFetch('/dashboard');const stale=auto.tick();auto.location.hash='#requirements';await auto.app.render();const requirementsHtml=auto.element('#main').innerHTML;auto.releaseFetch();await stale;
  if(auto.element('#main').innerHTML!==requirementsHtml)throw Error('舊頁延遲回應覆蓋新頁');
  auto.location.hash='#orders';await auto.app.render();await click(auto,'autorefresh');
  if(auto.intervals.size || auto.storage.getItem('q01_admin_autorefresh')!=='false' || auto.element('#autorefresh-toggle').getAttribute('aria-pressed')!=='false')throw Error('關閉自動更新未停止／保存偏好');
  admin.location.hash='#orders';await admin.app.render();await click(admin,'autorefresh');
  if(admin.storage.getItem('q01_admin_autorefresh')!=='true')throw Error('開啟偏好未保存');
  checks.push('2 秒計時、偏好保存、背景／明細／篩選／重畫中跳過、延遲回應、捲動／篩選／頁碼、失敗保留與復原、變動標示／減少動態效果通過');
  for(const options of [{storageReadThrows:true},{storageWriteThrows:true},{storageReadThrows:true,storageWriteThrows:true}]){
    const denied=environment('admin',null,options);await denied.app.start();
    if(denied.intervals.size || denied.element('#autorefresh-toggle').getAttribute('aria-pressed')!=='false')throw Error('localStorage 讀取例外未預設關閉');
    await click(denied,'autorefresh');await denied.tick();
    if(denied.intervals.size!==1 || denied.element('#autorefresh-toggle').getAttribute('aria-pressed')!=='true' || denied.element('#main').innerHTML.includes('暫時無法讀取資料'))throw Error('localStorage 例外使切換／更新損壞');
    await click(denied,'autorefresh');if(denied.intervals.size)throw Error('localStorage 例外使關閉損壞');
  }
  checks.push('localStorage 讀取／寫入／同時例外時預設關閉、仍可切換與更新通過');
  return checks;
}
if(typeof module!=='undefined'){
  module.exports={runFrontendChecks};
  if(require.main===module){
    const fs=require('node:fs'),path=require('node:path'),root=path.join(__dirname,'../static');
    runFrontendChecks({common:fs.readFileSync(path.join(root,'common.js'),'utf8'),store:fs.readFileSync(path.join(root,'store/store.js'),'utf8'),admin:fs.readFileSync(path.join(root,'admin/admin.js'),'utf8')})
      .then(checks=>console.log(checks.join('\n'))).catch(e=>{console.error(e);process.exitCode=1;});
  }
}
