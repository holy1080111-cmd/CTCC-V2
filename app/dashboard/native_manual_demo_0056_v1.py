"""Local manual Demo controls; no browser state can grant execution authority."""

import base64
import hashlib
import html
import json
import re
from decimal import Decimal
from typing import get_args

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from app.config.settings import get_settings
from app.demo_automation.native_requests_v2 import (
    CONFIGURE_LEVERAGE_CONFIRMATION_V1,
    NativeDemoStrategyV2,
)
from app.domain.demo_automation import ARM_PHRASE, EMERGENCY_STOP_PHRASE, EXECUTE_PHRASE
from app.exchange.okx.symbols import REVIEWED_DEMO_INSTRUMENT_IDS
from app.trade_qualification.demo_control_clear_v2 import CLEAR_CONFIRMATION_V2
from app.trade_qualification.demo_policy_counter_initialization_v1 import (
    COUNTER_CONFIRMATION_V1,
)

_STRATEGY_LABELS = (
    "趨勢回調",
    "突破延續",
    "流動性掃蕩反轉",
    "FVG 回補",
    "訂單區回測",
    "區間反轉",
    "結構反轉",
    "波動擴張",
)
_LEVERAGE = (3, 5, 8, 10, 20)

_STYLE = """
:root{color-scheme:light;font-family:system-ui,"Microsoft JhengHei",sans-serif;color:#152c36;background:#eef3f4}
*{box-sizing:border-box}body{margin:0}main{max-width:1080px;margin:auto;padding:32px 22px 60px}
header{display:flex;justify-content:space-between;gap:20px;align-items:start}h1{font-size:30px;margin:8px 0}h2{font-size:19px;margin:0 0 16px}p{line-height:1.65}small,.muted{color:#5a6d75}
.eyebrow{font-size:12px;letter-spacing:.1em;font-weight:700;color:#087d72}.pill{border:1px solid #b6c9cc;border-radius:999px;padding:6px 12px;white-space:nowrap;font-size:13px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{background:white;border:1px solid #d4e0e2;border-radius:15px;padding:24px;margin-top:18px;box-shadow:0 3px 14px #193b4110}.wide{grid-column:1/-1}
.fields{display:grid;grid-template-columns:1fr 1fr;gap:14px}label{display:block;font-size:14px;font-weight:600;margin-bottom:14px}input,select{display:block;width:100%;padding:11px 12px;margin-top:7px;border:1px solid #b6c9cc;border-radius:8px;background:#fff;color:#152c36;font:inherit;font-weight:400}
input:focus,select:focus,button:focus-visible{outline:3px solid #88cfc6;outline-offset:2px}button{border:0;border-radius:8px;padding:11px 15px;font:inherit;font-size:14px;font-weight:650;background:#087d72;color:white;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}.secondary{background:#e7eff0;color:#28474f}.danger{background:#ad2e36}.row{display:flex;gap:9px;flex-wrap:wrap;align-items:center}.note{font-size:13px;color:#5a6d75}.warning{color:#8c2830;background:#fff1f1;border:1px solid #ecc4c7;border-radius:9px;padding:13px;line-height:1.6}.warning[hidden]{display:none}
code{display:block;overflow-wrap:anywhere;background:#eef4f5;padding:8px 10px;border-radius:6px;font-size:12px;margin:7px 0 10px;user-select:text}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace,monospace;max-height:340px;overflow:auto;background:#f4f7f8;padding:12px;border-radius:8px}details{margin-top:13px}summary{cursor:pointer;font-size:13px;color:#41616b}.status-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}.status-grid div{background:#f3f7f8;padding:13px;border-radius:9px}.status-grid span{display:block;font-size:12px;color:#5a6d75}.status-grid strong{display:block;margin-top:7px;font-size:16px}.step{color:#087d72;margin-right:8px}.separator{border-top:1px solid #e2e9eb;margin:20px 0}.safety{border-top:4px solid #ad2e36}.phrase{font-family:ui-monospace,monospace;font-size:12px}
@media(max-width:720px){main{padding:20px 14px 40px}.grid,.fields{grid-template-columns:1fr}.status-grid{grid-template-columns:1fr 1fr}header{display:block}.pill{display:inline-block;margin-top:8px}.card{padding:19px}h1{font-size:26px}}
"""

_SCRIPT = r"""
(() => {
  'use strict';
  const config = __CONFIG__;
  const el = id => document.getElementById(id);
  let status = null;
  let busy = false;
  let safetyBusy = false;
  let uncertain = false;
  const positive = ['clearStop', 'initializeCounter', 'configureLeverage', 'armExecute'];
  const token = () => el('token').value.trim();
  const exact = (id, phrase) => el(id).value === phrase;
  const ready = () => status && status.recovered === true && status.armed === false;
  function update() {
    const auth = token().length >= 32;
    for (const id of positive) el(id).disabled = !auth || !ready() || busy || uncertain;
    el('armExecute').disabled = el('armExecute').disabled || !config.execution_enabled || status.emergency_stop !== false || status.locked !== false;
    el('configureLeverage').disabled = el('configureLeverage').disabled || !config.execution_enabled || status.emergency_stop !== false || status.locked !== false;
    el('reconcile').disabled = !auth || busy || !/^[a-f0-9]{64}$/.test(el('reservation').value);
    el('stop').disabled = !auth || safetyBusy;
    el('emergencyStop').disabled = !auth || safetyBusy;
    el('refresh').disabled = !auth;
    el('history').disabled = !auth;
    el('uncertain').hidden = !uncertain;
    el('progress').textContent = busy ? '處理中，請保留此頁。停止與緊急停止仍可使用。' : '等待手動操作';
  }
  function redacted(value, secret) {
    return JSON.parse(JSON.stringify(value, (key, item) => {
      if (/token|secret|passphrase|password|api.?key|authorization/i.test(key)) return '[已隱藏]';
      return typeof item === 'string' && secret ? item.split(secret).join('[已隱藏]') : item;
    }));
  }
  function safeText(value) { return JSON.stringify(redacted(value,token()),null,2); }
  function show(target, value) { el(target).textContent = safeText(value); }
  function latch() { uncertain = true; update(); }
  function checkedStatus(value) {
    if (!value || ['recovered', 'armed', 'emergency_stop', 'locked'].some(key => typeof value[key] !== 'boolean')) throw Error('回覆格式不完整，狀態維持未知。');
    return value;
  }
  function renderStatus(value) {
    status = checkedStatus(value);
    for (const [id, key, yes, no] of [
      ['recoveredState','recovered','已恢復','尚未恢復'],
      ['armState','armed','已 Arm','未 Arm'],
      ['stopState','emergency_stop','已停止','未觸發'],
      ['lockState','locked','已鎖定','未鎖定']
    ]) el(id).textContent = status[key] ? yes : no;
    el('statusMessage').textContent = '已讀取本機狀態；每次執行仍需完整來源、風控與保護驗證。';
    show('statusDetail', value);
    update();
  }
  function forgetStatus() {
    status = null;
    for (const id of ['recoveredState','armState','stopState','lockState']) el(id).textContent = '未知';
    el('statusMessage').textContent = '尚未讀取狀態。';
    el('statusDetail').textContent = '';
    update();
  }
  async function request(path, method = 'GET', body) {
    const auth = token();
    if (auth.length < 32) throw Error('請先輸入本機 CTCC 操作權杖。');
    const options = {method, headers:{'X-CTCC-Token':auth, 'Accept':'application/json'}, credentials:'omit', redirect:'error', mode:'same-origin', cache:'no-store', referrerPolicy:'no-referrer'};
    if (body !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    let response;
    try { response = await fetch(path, options); }
    catch (_) { if (method === 'POST') latch(); throw Error(method === 'POST' ? '連線中斷，結果不確定。已鎖定新增曝險；請停止並對帳，請勿重送。' : '無法讀取狀態，請檢查本機服務。'); }
    if (!response.ok) {
      if (method === 'POST' && ![401, 403, 422, 423].includes(response.status)) latch();
      throw Error([401,403].includes(response.status) ? '權杖未通過驗證，請重新輸入。' : '伺服器拒絕或無法確認此操作（HTTP ' + response.status + '）。請查看紀錄並對帳，請勿自動重送。');
    }
    try {
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.toLowerCase().includes('application/json')) throw Error();
      const raw = await response.text();
      if (raw.length > 131072) throw Error();
      const value = JSON.parse(raw);
      if (!value || typeof value !== 'object') throw Error();
      return redacted(value,auth);
    } catch (_) { if (method === 'POST') latch(); throw Error('回覆格式無法確認。新增曝險維持鎖定，請對帳。'); }
  }
  function baseOrder() {
    const instrument_id = el('instrument').value;
    const strategy = el('strategy').value;
    const contracts = el('contracts').value;
    const leverage = Number(el('leverage').value);
    if (!config.symbols.includes(instrument_id) || !config.strategies.includes(strategy) || !config.leverage.includes(leverage) || leverage > config.max_leverage || !/^(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,20})?$/.test(contracts) || !/[1-9]/.test(contracts)) throw Error('請檢查商品、策略、合約張數與槓桿上限。');
    const units = value => { const [whole, part = ''] = value.split('.'); return BigInt(whole + part.padEnd(20, '0')); };
    if (units(contracts) > units(config.max_contracts)) throw Error('張數超過本機設定上限，請調整後再確認。');
    return {instrument_id, strategy, contracts, leverage};
  }
  function clearPhrase(id, phrase) {
    if (!exact(id, phrase)) throw Error('請完整輸入畫面上的確認文字。');
    const value = el(id).value;
    el(id).value = '';
    return value;
  }
  function accepted(action, value, body) {
    const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
    const invocation = typeof value.invocation_id === 'string' && value.invocation_id.length > 0 && value.invocation_id.length <= 128;
    if (action === 'arm-execute') {
      const zero = value.status === 'zero_fill_flat_settled' && value.classification === 'terminal_zero_fill_flat_settled';
      const filled = value.status === 'filled_flat_settled' && value.classification === 'own_entry_and_close_filled_flat_settled' && digest(value.forensics_outcome_id);
      if (invocation && value.action === 'arm_execute' && (zero || filled) && digest(value.closure_outcome_id) && digest(value.cleanup_outcome_id) && value.execution_complete === true && value.risk_hold_retained === false) return;
      latch();
      throw Error('執行結果仍需對帳或核對。已保留回覆並鎖定新增曝險，請勿重送。');
    }
    if (action === 'configure-leverage') {
      if (invocation && value.action === 'configure_leverage' && ['settled','no_change'].includes(value.classification) && digest(value.configuration_outcome_id) && digest(value.receipt_sha256) && value.execution_authority === false && value.order_retry_authority === false) return;
    } else if (action === 'reconcile') {
      if (invocation && value.status === 'reconciliation_data' && value.reservation_id === body.reservation_id && digest(value.outcome_id) && digest(value.receipt_sha256) && typeof value.risk_hold_retained === 'boolean' && value.execution_authority === false && value.order_retry_authority === false && value.execution_complete === false) return;
    } else if (invocation && value.action === (action === 'clear-stop' ? 'clear_stop' : 'initialize_policy_counter') && value.status === 'operator_command_recorded_disarmed' && value.execution_complete === false) return;
    latch();
    throw Error('伺服器回覆與操作不符，結果不確定；請對帳。');
  }
  async function action(name, makeBody) {
    if (busy || (uncertain && name !== 'reconcile')) return;
    if (name !== 'reconcile' && (!ready() || (['arm-execute','configure-leverage'].includes(name) && (!config.execution_enabled || status.emergency_stop !== false || status.locked !== false)))) {
      el('resultMessage').textContent = '請先重新讀取已恢復且未 Arm 的狀態，並完成必要設定。'; return;
    }
    let body;
    try { body = makeBody(); }
    catch (error) { el('resultMessage').textContent = error.message; return; }
    busy = true; update();
    el('resultMessage').textContent = '已送出一次操作，等待伺服器回覆；不會自動重試。';
    try {
      const value = await request('/api/demo-automation/native/' + name, 'POST', body);
      show('result', value);
      accepted(name, value, body);
      el('resultMessage').textContent = name === 'reconcile' ? '已取得對帳回覆。此頁不會據此自動解除不確定鎖定或重送。' : '已收到伺服器確認回覆。繼續操作前請重新讀取狀態。';
      forgetStatus();
    } catch (error) { el('resultMessage').textContent = error.message; }
    finally { busy = false; update(); }
  }
  async function safety(emergency) {
    if (safetyBusy) return;
    let body;
    try { if (emergency) body = {confirmation:clearPhrase('emergencyPhrase',config.phrases.emergency)}; }
    catch (error) { el('safetyMessage').textContent = error.message; return; }
    safetyBusy = true; update();
    try {
      const value = await request('/api/demo-automation/' + (emergency ? 'emergency-stop' : 'stop'), 'POST', body);
      checkedStatus(value);
      if (value.armed !== false || (emergency && value.emergency_stop !== true)) { latch(); throw Error('停止狀態尚未確認。請保留紀錄並對帳。'); }
      show('safetyResult',value);
      renderStatus(value);
      el('safetyMessage').textContent = emergency ? '伺服器已確認緊急停止。保護與對帳紀錄仍保留。' : '伺服器已確認停止；如有未決結果，仍須對帳。';
    } catch (error) { latch(); el('safetyMessage').textContent = error.message; }
    finally { safetyBusy = false; update(); }
  }
  el('token').addEventListener('input',forgetStatus);
  el('forget').addEventListener('click',() => { el('token').value = ''; forgetStatus(); });
  el('reservation').addEventListener('input',update);
  el('refresh').addEventListener('click',async () => {
    try { renderStatus(await request('/api/demo-automation/status')); }
    catch (error) { forgetStatus(); el('statusMessage').textContent = error.message; }
  });
  el('history').addEventListener('click',async () => {
    try { show('result',await request('/api/demo-automation/history?limit=20')); el('resultMessage').textContent = '已讀取近期紀錄。'; }
    catch (error) { el('resultMessage').textContent = error.message; }
  });
  el('stop').addEventListener('click',() => safety(false));
  el('emergencyStop').addEventListener('click',() => safety(true));
  el('clearStop').addEventListener('click',() => action('clear-stop',() => ({confirmation:clearPhrase('clearPhrase',config.phrases.clear)})));
  el('initializeCounter').addEventListener('click',() => action('initialize-policy-counter',() => ({confirmation:clearPhrase('counterPhrase',config.phrases.counter)})));
  el('configureLeverage').addEventListener('click',() => action('configure-leverage',() => {
    const order = baseOrder();
    return {instrument_id:order.instrument_id,strategy:order.strategy,contracts:order.contracts,leverage_ceiling:order.leverage,confirmation:clearPhrase('leveragePhrase',config.phrases.leverage)};
  }));
  el('armExecute').addEventListener('click',() => action('arm-execute',() => {
    const order = baseOrder();
    if (!exact('armPhrase',config.phrases.arm) || !exact('executePhrase',config.phrases.execute)) throw Error('請完整輸入兩項 Arm 與執行確認文字。');
    return {...order,arm_confirmation:clearPhrase('armPhrase',config.phrases.arm),execute_confirmation:clearPhrase('executePhrase',config.phrases.execute)};
  }));
  el('reconcile').addEventListener('click',() => action('reconcile',() => {
    if (!/^[a-f0-9]{64}$/.test(el('reservation').value)) throw Error('請輸入完整 64 字元預留 ID。');
    return {reservation_id:el('reservation').value};
  }));
  update();
})();
"""


def _configuration(settings):
    symbols = settings.okx_demo_allowed_symbol_list
    maximum = settings.okx_demo_max_leverage
    quantity = settings.okx_demo_max_order_size_contracts
    switches = (
        settings.okx_demo_allow_order_writes,
        settings.okx_demo_auto_execution,
        settings.okx_ws_enabled,
    )
    if (
        type(symbols) is not list
        or not 0 < len(symbols) <= 20
        or len(set(symbols)) != len(symbols)
        or any(
            type(s) is not str
            or s not in REVIEWED_DEMO_INSTRUMENT_IDS
            or re.fullmatch(r"[A-Z0-9]{1,16}-USDT-SWAP", s) is None
            for s in symbols
        )
        or type(maximum) is not int
        or not 1 <= maximum <= 125
        or type(quantity) is not Decimal
        or not quantity.is_finite()
        or not 0 < quantity < Decimal("1e20")
        or quantity.as_tuple().exponent < -20
        or len(quantity.as_tuple().digits) > 40
        or any(type(s) is not bool for s in switches)
    ):
        raise ValueError("native_manual_dashboard_configuration_refused")
    return {
        "symbols": symbols,
        "strategies": list(get_args(NativeDemoStrategyV2)),
        "leverage": list(_LEVERAGE),
        "max_leverage": maximum,
        "max_contracts": format(quantity, "f"),
        "execution_enabled": all(switches) and maximum >= 3,
        "phrases": {
            "arm": ARM_PHRASE,
            "execute": EXECUTE_PHRASE,
            "emergency": EMERGENCY_STOP_PHRASE,
            "clear": CLEAR_CONFIRMATION_V2,
            "counter": COUNTER_CONFIRMATION_V1,
            "leverage": CONFIGURE_LEVERAGE_CONFIRMATION_V1,
        },
    }


def _phrase(label, field, phrase):
    return f'<label>{html.escape(label)}<code>{html.escape(phrase)}</code><input id="{field}" class="phrase" autocomplete="off" spellcheck="false" aria-label="{html.escape(label)}確認文字" placeholder="請完整輸入上方文字"></label>'


def render_manual_demo_dashboard_0056_v1(settings):
    config = _configuration(settings)
    script = _SCRIPT.replace(
        "__CONFIG__", json.dumps(config, ensure_ascii=True, separators=(",", ":"))
    )
    symbols = "".join(
        f'<option value="{html.escape(s)}">{html.escape(s)}</option>'
        for s in config["symbols"]
    )
    strategies = "".join(
        f'<option value="{name}">{label}</option>'
        for name, label in zip(config["strategies"], _STRATEGY_LABELS, strict=True)
    )
    leverage = "".join(
        f'<option value="{value}"{" disabled" if value > config["max_leverage"] else ""}>{value}x{"（超過設定上限）" if value > config["max_leverage"] else ""}</option>'
        for value in _LEVERAGE
    )
    mode_note = (
        "每次僅執行一個完整驗證流程；槓桿為上限，實際風險由伺服器重新計算。"
        if config["execution_enabled"]
        else "目前設定未開啟完整交易開關，僅可讀取狀態、停止與執行恢復程序。"
    )
    body = f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CTCC · Demo 手動控制</title><style>{_STYLE}</style></head><body><main>
<header><div><div class="eyebrow">CTCC V2 · LOCAL CONTROL</div><h1>Demo 手動控制</h1><p class="muted">讀取狀態、完成恢復，再手動執行一次。所有交易仍須通過完整驗證。</p></div><span class="pill">OKX 模擬交易</span></header>
<section class="card"><h2><span class="step">01</span>連線與目前狀態</h2><label>本機 CTCC 操作權杖<input id="token" type="password" autocomplete="off" spellcheck="false" placeholder="輸入本機私人設定中的操作權杖"></label><div class="row"><button id="refresh" disabled>讀取狀態</button><button id="history" class="secondary" disabled>近期紀錄</button><button id="forget" class="secondary">清除權杖</button><small>只保留於本頁記憶體，關閉或重新載入後需重輸。</small></div><div class="status-grid"><div><span>啟動恢復</span><strong id="recoveredState">未知</strong></div><div><span>Arm</span><strong id="armState">未知</strong></div><div><span>緊急停止</span><strong id="stopState">未知</strong></div><div><span>鎖定狀態</span><strong id="lockState">未知</strong></div></div><p id="statusMessage" class="note" role="status">尚未讀取狀態。</p><details><summary>查看本機狀態回覆</summary><pre id="statusDetail"></pre></details></section>
<div id="uncertain" class="warning" hidden role="alert">操作結果不確定：此頁已鎖定新增曝險。請保留紀錄，使用預留 ID 對帳；請勿重送。對帳回覆不會自動解除此鎖定。</div>
<div class="grid"><section class="card"><h2><span class="step">02</span>恢復與初始化</h2><p class="note">首次使用先初始化風控計數器，再核對並清除停止；已初始化者請保留原紀錄，不需重做。清除停止不會自動 Arm。</p>{_phrase("初始化風控計數器", "counterPhrase", COUNTER_CONFIRMATION_V1)}<button id="initializeCounter" disabled>初始化一次</button><div class="separator"></div>{_phrase("清除停止", "clearPhrase", CLEAR_CONFIRMATION_V2)}<button id="clearStop" disabled>核對並清除停止</button></section>
<section class="card safety"><h2>安全控制</h2><p class="note">長時間執行中仍可操作。停止不會靜默平倉或取消既有保護。</p><button id="stop" class="secondary" disabled>停止並撤銷本機 Arm</button><div class="separator"></div>{_phrase("緊急停止", "emergencyPhrase", EMERGENCY_STOP_PHRASE)}<button id="emergencyStop" class="danger" disabled>緊急停止</button><p id="safetyMessage" class="note" role="status"></p><details><summary>查看停止回覆</summary><pre id="safetyResult"></pre></details></section>
<section class="card wide"><h2><span class="step">03</span>商品與風險設定</h2><p class="note">{mode_note} 本機設定的槓桿上限：{config["max_leverage"]}x，合約張數上限：{config["max_contracts"]}。</p><div class="fields"><label>永續合約<select id="instrument">{symbols}</select></label><label>進場策略<select id="strategy">{strategies}</select></label><label>合約張數<input id="contracts" inputmode="decimal" autocomplete="off" placeholder="依商品合約規格填入，例：1"></label><label>槓桿上限<select id="leverage">{leverage}</select></label></div>{_phrase("空倉時設定槓桿", "leveragePhrase", CONFIGURE_LEVERAGE_CONFIRMATION_V1)}<button id="configureLeverage" disabled>核對空倉並設定槓桿</button></section>
<section class="card"><h2><span class="step">04</span>完整驗證並執行一次</h2><p class="note">系統會重新檢查進場條件、圖表證據、最新行情、帳戶風險與保護。條件不成立即停止；不會改動原交易條件或自動重送。</p>{_phrase("本次 Arm", "armPhrase", ARM_PHRASE)}{_phrase("本次執行", "executePhrase", EXECUTE_PHRASE)}<button id="armExecute" disabled>驗證並執行一次 Demo</button><p id="progress" class="note" role="status">等待手動操作</p></section>
<section class="card"><h2>未決結果對帳</h2><p class="note">使用原預留紀錄中的 ID，重新讀取並核對既有結果。此操作不會重新送單。</p><label>預留 ID<input id="reservation" class="phrase" autocomplete="off" spellcheck="false" maxlength="64" placeholder="請從原操作紀錄複製完整 64 字元 ID"></label><button id="reconcile" disabled>對帳此預留紀錄</button><p class="note">若沒有 ID，保留此頁回覆與本機紀錄，先保持停止狀態。</p></section>
<section class="card wide"><h2>操作結果</h2><p id="resultMessage" role="status">尚未送出操作。</p><pre id="result" aria-label="操作回覆"></pre></section></div></main><script>{script}</script></body></html>"""
    digest = lambda text: base64.b64encode(
        hashlib.sha256(text.encode("utf-8")).digest()
    ).decode("ascii")
    return HTMLResponse(
        body,
        headers={
            "Content-Security-Policy": f"default-src 'none'; script-src 'sha256-{digest(script)}'; style-src 'sha256-{digest(_STYLE)}'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'",
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Cross-Origin-Resource-Policy": "same-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        },
    )


async def manual_demo_dashboard_0056_v1():
    try:
        return render_manual_demo_dashboard_0056_v1(get_settings())
    except Exception:  # noqa: BLE001 -- no configured setting or secret may enter an error page.
        raise HTTPException(
            status_code=503, detail="native_manual_dashboard_configuration_unavailable"
        ) from None
