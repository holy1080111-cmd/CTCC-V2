"""Static local operator surface; it owns no qualification or execution power."""

from secrets import token_urlsafe

from starlette.responses import HTMLResponse


def render_live_manual_dashboard_v1():
    nonce = token_urlsafe(24)
    document = _HTML.replace("__NONCE__", nonce)
    return HTMLResponse(
        document,
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; connect-src 'self'; "
                f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


_HTML = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CTCC Live 人工操作</title>
<style nonce="__NONCE__">
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#101721;color:#e7edf5}
*{box-sizing:border-box}body{margin:0}main{max-width:1000px;margin:auto;padding:28px 20px 64px}
header{margin-bottom:26px}.tag{color:#b3c7df;font-size:14px}h1{font-size:30px;margin:8px 0}
h2{font-size:21px;margin:0 0 14px}p{line-height:1.7;color:#c1cddd}.card{background:#182332;
border:1px solid #35455a;border-radius:14px;padding:22px;margin:18px 0}.alert{border-color:#a87b33}
label{display:block;margin:14px 0 6px}input,select,button{font:inherit;border-radius:8px;
border:1px solid #52677e;padding:12px}input,select{color:#e7edf5;background:#0f1824;width:100%}
input[type=checkbox]{width:auto;margin-right:10px}button{cursor:pointer;color:#eff8ff;
background:#245b8e;margin:10px 8px 4px 0}button:disabled{opacity:.45;cursor:not-allowed}
.stop{background:#863b3b}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.mono{font-family:ui-monospace,monospace;overflow-wrap:anywhere;font-size:14px}
.phrase{border-left:3px solid #ddac58;padding:10px;background:#101b29;user-select:all}
.pill{display:inline-block;border:1px solid #63778e;border-radius:22px;padding:6px 12px}
.muted{color:#a7b8cd}#notice{min-height:1.7em}#journalRows>article{border-top:1px solid #43546a;
padding:18px 0}dl{display:grid;grid-template-columns:140px 1fr;gap:10px}dd{margin:0}
@media(max-width:650px){.grid{grid-template-columns:1fr}dl{grid-template-columns:1fr}h1{font-size:25px}}
</style></head><body><main>
<header><span class="tag">CTCC · 單次受控操作</span><h1>Live 人工操作</h1>
<p>工具啟動時保持停止。只有當次授權通過、資料完整、原交易重新驗證合格，才可能送出一筆受限訂單。</p>
<span id="state" class="pill" role="status">尚未檢查設定</span></header>
<section class="card"><h2>1. 連線與檢查</h2>
<label for="token">本機工具存取密碼</label>
<input id="token" type="password" autocomplete="off" spellcheck="false">
<p class="muted">只在此視窗暫時使用。請勿填入 OKX API 密鑰。離開視窗後不保留。</p>
<button id="check">檢查設定並確認停止</button><button id="refresh">更新操作狀態</button>
<button id="readJournal">讀取待處理與交易紀錄</button><p id="notice" aria-live="polite"></p></section>
<section class="card"><h2>2. 準備單次授權</h2>
<p>準備階段會讀取帳戶及市場資料。此按鈕不會 Arm 或下單。</p>
<div class="grid"><div><label for="instrument">交易合約</label><select id="instrument">
<option>BTC-USDT-SWAP</option><option>ETH-USDT-SWAP</option></select></div>
<div><label for="strategy">進場策略</label><select id="strategy">
<option value="trend_pullback">趨勢回調</option><option value="breakout_continuation">突破延續</option>
<option value="liquidity_sweep_reversal">流動性掃蕩反轉</option><option value="fvg_return">FVG 回測</option>
<option value="order_block_return">訂單塊回測</option><option value="range_reversal">區間反轉</option>
<option value="structure_reversal">結構反轉</option><option value="volatility_expansion">波動擴張</option>
</select></div><div><label for="direction">方向</label><select id="direction">
<option value="long">做多</option><option value="short">做空</option></select></div></div>
<label><input type="checkbox" id="exitRequested">同時要求準備獨立的受控平倉授權</label>
<p class="muted">平倉另需輸入當次確認句。不勾選時不會為驗收自動平倉。</p>
<button id="prepare" disabled>準備授權單</button></section>
<section id="approval" class="card alert" hidden><h2>3. 當次真實資金確認</h2>
<p>以下確認會允許真實資金操作。先核對帳戶、合約、方向及上限，再逐項輸入當次確認句。</p>
<dl><dt>帳戶</dt><dd id="account"></dd><dt>合約與方向</dt><dd id="trade"></dd>
<dt>上限</dt><dd id="caps"></dd><dt>現有槓桿</dt><dd id="leverage"></dd>
<dt>授權到期</dt><dd id="expires"></dd></dl>
<label for="armPhrase">真實資金單次 Arm</label><p id="armExpected" class="phrase mono"></p>
<input id="armPhrase" autocomplete="off" spellcheck="false">
<label for="clearPhrase">解除當次停止狀態</label><p id="clearExpected" class="phrase mono"></p>
<input id="clearPhrase" autocomplete="off" spellcheck="false">
<div id="exitConfirmation" hidden><label for="exitPhrase">獨立受控平倉</label>
<p id="exitScope">確認後 120 秒內，僅允許一次受控平倉；到期不會自動延長或重送。</p>
<p id="exitExpected" class="phrase mono"></p><input id="exitPhrase" autocomplete="off" spellcheck="false"></div>
<button id="approve" disabled>確認上述條件並執行一次</button>
<p class="muted">確認句不會自動填入。等待、拒絕或不確定結果均不會自動補送。</p></section>
<section class="card"><h2>操作結果</h2><p id="result">尚未執行。Micro Live 驗收尚未完成。</p>
<button id="stop" class="stop" disabled>停止本次操作</button>
<p class="muted">停止會撤銷後續送單權限。既有成交仍需對帳；停止按鈕不會自行平倉或取消保護。</p></section>
<section class="card"><h2>交易與待處理紀錄</h2><p id="journalState">尚未讀取。</p>
<p class="muted">紀錄保留該筆操作當時的對帳結果。新的操作會重新核對帳戶與行情。</p>
<div id="journalRows"></div></section>
</main><script nonce="__NONCE__">
'use strict';
const el=id=>document.getElementById(id);
let current=null,timer=null,busy=false;
const names={NOT_CHECKED:'尚未檢查',CHECKING_PRIVATE_SETUP:'正在檢查私人設定',
NOT_CONFIGURED:'尚未完成 Live 私人設定',PRIVATE_SETUP_OR_STOP_UNCONFIRMED:'設定或停止狀態尚未確認',
STOPPED_DEFAULT_OFF:'已確認停止，Live 預設關閉',PREPARING_READ_ONLY:'正在讀取並準備授權',
AWAITING_HUMAN_ARM:'等待當次人工確認',RUNNING_ONCE:'單次驗證與執行中',STOP_REQUESTED:'已要求停止',
RECONCILING_READ_ONLY:'正在重新對帳',ZERO_FILL_FLAT:'未成交，已確認無曝險',
PROTECTED_HOLD:'已有保護，仍保留風險占用',UNCERTAIN:'結果不確定，停止新增曝險',
RECONCILED_FLAT:'已保存無曝險對帳結果',WAIT:'條件或資料不符，未送出訂單',
CANCELLED:'發布後條件已變更，已取消',EXPIRED:'人工確認逾時，已取消',
STOPPED_BEFORE_ARM:'已停止，未送出訂單'};
const label=value=>names[value]||'狀態尚未確認';
function clearPhrases(){for(const id of ['armPhrase','clearPhrase','exitPhrase'])el(id).value='';}
function notice(text){el('notice').textContent=text;}
async function api(path,body){
 const token=el('token').value;if(!token)throw new Error('請先輸入本機工具存取密碼。');
 const response=await fetch('/api/live-tool/'+path,{method:body===undefined?'GET':'POST',
  cache:'no-store',credentials:'omit',redirect:'error',
  headers:{'X-CTCC-Token':token,...(body===undefined?{}:{'Content-Type':'application/json'})},
  ...(body===undefined?{}:{body:JSON.stringify(body)})});
 if(!response.ok)throw new Error(response.status===401?'存取密碼不正確。':
  response.status===503?'本機存取設定尚未就緒。':'操作未確認，請更新狀態或重新對帳。');
 return await response.json();
}
function refreshButtons(){
 const active=!!(current&&current.job&&['PREPARING_READ_ONLY','AWAITING_HUMAN_ARM',
  'RUNNING_ONCE','STOP_REQUESTED','RECONCILING_READ_ONLY'].includes(current.job.status));
 el('prepare').disabled=busy||active||!current?.startup_inhibit_confirmed;
 el('check').disabled=busy||active;el('stop').disabled=busy||!active;
 const c=current?.job?.challenge;
 el('approve').disabled=busy||!c||current.job.status!=='AWAITING_HUMAN_ARM'||
  el('armPhrase').value!==c.arm_phrase||el('clearPhrase').value!==c.clear_stop_phrase||
  el('exitPhrase').value!==c.controlled_exit_phrase;
}
function render(value){
 const previous=current?.job?.challenge?.challenge_id;current=value;
 const job=value.job,c=job?.challenge;el('state').textContent=label(job?.status||value.setup_status);
 if(previous!==c?.challenge_id)clearPhrases();
 el('approval').hidden=!c||job.status!=='AWAITING_HUMAN_ARM';
 if(c){el('account').textContent=c.account_label;el('trade').textContent=c.instrument_id+' · '+
  (c.direction==='long'?'做多':'做空');
  el('caps').textContent='單筆風險 '+c.caps[0]+' USDT；總風險 '+c.caps[1]+
   ' USDT；保證金 '+c.caps[2]+' USDT；名義本金 '+c.caps[3]+' USDT';
  el('leverage').textContent=String(c.existing_leverage)+'x';
  el('expires').textContent=c.challenge_expires_at+' · 最多一筆，Arm '+c.arm_seconds+' 秒';
  el('armExpected').textContent=c.arm_phrase;el('clearExpected').textContent=c.clear_stop_phrase;
  el('exitExpected').textContent=c.controlled_exit_phrase;
  el('exitScope').textContent='確認後 '+c.controlled_exit_seconds+' 秒內，僅允許一次受控平倉；到期不會自動延長或重送。';
  el('exitConfirmation').hidden=!c.controlled_exit_requested;}
 el('result').textContent=job?label(job.status)+(job.result?
  '。保護：'+(job.result.protection_verified===true?'已確認':'未確認')+
  '；停止狀態：'+(job.result.emergency_stop===true?'已停止':'尚未確認'):''):
  '尚未執行。Micro Live 驗收尚未完成。';
 refreshButtons();if(timer)clearTimeout(timer);
 if(job&&['PREPARING_READ_ONLY','AWAITING_HUMAN_ARM','RUNNING_ONCE','STOP_REQUESTED',
  'RECONCILING_READ_ONLY'].includes(job.status))timer=setTimeout(poll,1500);
}
async function poll(){try{render(await api('status'));}catch(e){notice(e.message);}}
async function action(fn){if(busy)return;busy=true;refreshButtons();
 try{await fn();notice('已更新。');}catch(e){notice(e.message);}
 finally{busy=false;refreshButtons();}}
el('check').addEventListener('click',()=>action(async()=>render(await api('check-setup',{}))));
el('refresh').addEventListener('click',()=>action(poll));
el('prepare').addEventListener('click',()=>action(async()=>render(await api('prepare',{
 instrument_id:el('instrument').value,strategy:el('strategy').value,direction:el('direction').value,
 controlled_exit_requested:el('exitRequested').checked}))));
el('approve').addEventListener('click',()=>action(async()=>{
 const job=current.job,body={operation_id:job.operation_id,challenge_id:job.challenge.challenge_id,
  arm_phrase:el('armPhrase').value,clear_stop_phrase:el('clearPhrase').value,
  controlled_exit_phrase:el('exitPhrase').value};clearPhrases();render(await api('approve',body));}));
el('stop').addEventListener('click',()=>action(async()=>{
 clearPhrases();render(await api('stop',{operation_id:current.job.operation_id}));}));
for(const id of ['armPhrase','clearPhrase','exitPhrase'])el(id).addEventListener('input',refreshButtons);
el('readJournal').addEventListener('click',()=>action(async()=>{
 const data=await api('journal');el('journalRows').replaceChildren();
 el('journalState').textContent=data.items.length?'已讀取 '+data.items.length+' 筆紀錄。':'已確認目前沒有紀錄。';
 for(const row of data.items){const article=document.createElement('article'),title=document.createElement('p'),
  identity=document.createElement('p'),button=document.createElement('button');
  title.textContent=label(row.status);identity.className='mono';identity.textContent=row.reservation_id;
  button.textContent='重新讀取並對帳';button.addEventListener('click',()=>action(async()=>
   render(await api('reconcile',{reservation_id:row.reservation_id}))));
  article.append(title,identity,button);el('journalRows').append(article);}
}));
window.addEventListener('pagehide',()=>{clearPhrases();el('token').value='';if(timer)clearTimeout(timer);});
</script></body></html>"""
