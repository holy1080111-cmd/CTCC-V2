"""Self-contained browser page for the offline, untrusted snapshot analysis API."""

from app.dashboard.snapshot_receipt import SNAPSHOT_RECEIPT_SCRIPT

LOCAL_ANALYSIS_HTML = (
    r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CTCC 本機快照分析</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, "Noto Sans TC", sans-serif; }
    body { margin: 0; background: #071019; color: #ecf4ff; }
    main { width: min(850px, calc(100% - 32px)); margin: 32px auto; }
    h1 { margin-bottom: 8px; }
    p { line-height: 1.65; color: #bfd0e2; }
    a { color: #8dbfff; }
    .panel { background: #101b27; border: 1px solid #30445b; border-radius: 14px;
             padding: 20px; margin: 18px 0; }
    .warning { border-color: #b88b38; color: #ffe0a0; }
    label { display: block; margin: 16px 0 6px; font-weight: 600; }
    input, select { display: block; max-width: 100%; width: 100%; padding: 10px;
            border: 1px solid #526982; border-radius: 8px; background: #08131e;
            color: #ecf4ff; }
    input[type="file"] { padding: 9px; }
    input[type="checkbox"] { display: inline; width: auto; margin-right: 8px; }
    .fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
              column-gap: 16px; }
    button { margin-top: 16px; padding: 10px 17px; border: 1px solid #78acfa;
             border-radius: 8px; background: #224978; color: white; cursor: pointer; }
    button:disabled { cursor: wait; opacity: 0.6; }
    #status { min-height: 1.6em; }
    pre { overflow: auto; max-height: 65vh; white-space: pre-wrap; word-break: break-word;
          padding: 16px; border: 1px solid #30445b; border-radius: 8px;
          background: #08131e; color: #e6f0ff; }
    nav { display: flex; flex-wrap: wrap; gap: 18px; }
  </style>
</head>
<body>
<main>
  <nav aria-label="本機工具">
    <a href="#paper-section">本機 Paper 模擬</a>
    <a href="/readiness">本機服務狀態</a>
  </nav>
  <h1>匯入市場快照分析</h1>
  <p>選取已儲存的 MarketSnapshot JSON。此頁只呼叫同站的本機分析 API，
     重新計算 4H、1H、15m、5m 分析；不連線取得市場資料，也不送出訂單。</p>
  <div class="panel warning">
    匯入檔案的來源未經驗證，分析結果不能作為 Demo 或 Live 下單資格。
    檔案不是當前行情；缺少可信時間與來源證據時，交易狀態維持拒絕。
  </div>
  <div class="panel">
    <label for="token">本機 CTCC API Token</label>
    <input id="token" type="password" autocomplete="off" spellcheck="false">
    <label for="snapshot">MarketSnapshot JSON（最多 1 MiB）與選填的匯出核對單</label>
    <input id="snapshot" type="file" accept=".json,application/json" multiple aria-describedby="snapshotHint">
    <p id="snapshotHint">有核對單時，在同一個視窗選取兩個檔案。</p>
    <button id="analyze" type="button">開始本機分析</button>
    <p id="status" role="status" aria-live="polite">尚未分析。</p>
  </div>
  <section class="panel" aria-label="分析結果">
    <h2>分析結果</h2>
    <pre id="result" hidden></pre>
  </section>
  <section id="paper-section" class="panel" aria-label="本機 Paper 帳本">
    <h2>本機 Paper 帳本</h2>
    <p>只讀取本機模擬帳本與狀態，不代表 OKX 帳戶、持倉或交易資格。
       請在上方重新輸入 Token 後讀取。</p>
    <button id="paperRead" type="button">讀取 Paper 帳本</button>
    <p id="paperStatus" role="status" aria-live="polite">尚未讀取。</p>
    <pre id="paperResult" hidden></pre>
  </section>
  <section class="panel" aria-label="手動 Paper 模擬操作">
    <h2>手動 Paper 模擬操作</h2>
    <p>以下操作只改變本機模擬帳本，未經 G1–G12 或風控資格驗證，
       不會送到 OKX。每次操作都需重新輸入上方 Token 並勾選確認。</p>
    <div class="fields">
      <div><label for="paperSymbol">商品</label>
        <input id="paperSymbol" type="text" maxlength="40" placeholder="BTC-USDT-SWAP"></div>
      <div><label for="paperSide">方向</label>
        <select id="paperSide"><option value="long">多</option><option value="short">空</option></select></div>
      <div><label for="paperMode">委託類型</label>
        <select id="paperMode"><option value="market">市價模擬</option><option value="limit">限價模擬</option></select></div>
      <div><label for="paperQuantity">數量</label>
        <input id="paperQuantity" type="text" inputmode="decimal" maxlength="40"></div>
      <div><label for="paperReference">進場參考價</label>
        <input id="paperReference" type="text" inputmode="decimal" maxlength="40"></div>
      <div id="paperLimitWrap" hidden><label for="paperLimit">限價</label>
        <input id="paperLimit" type="text" inputmode="decimal" maxlength="40"></div>
      <div><label for="paperStop">停損價</label>
        <input id="paperStop" type="text" inputmode="decimal" maxlength="40"></div>
      <div><label for="paperTarget">停利價</label>
        <input id="paperTarget" type="text" inputmode="decimal" maxlength="40"></div>
    </div>
    <label><input id="paperConfirm" type="checkbox">我確認這是本機 Paper 模擬操作</label>
    <button id="paperOrder" type="button" disabled>送出模擬訂單</button>
    <p>手動報價可能觸發本機限價單成交、停利或停損。請先核對帳本。</p>
    <div class="fields">
      <div><label for="paperTickSymbol">報價商品</label>
        <input id="paperTickSymbol" type="text" maxlength="40" placeholder="BTC-USDT-SWAP"></div>
      <div><label for="paperTickPrice">模擬報價</label>
        <input id="paperTickPrice" type="text" inputmode="decimal" maxlength="40"></div>
    </div>
    <button id="paperTick" type="button" disabled>送出模擬報價</button>
    <p>從已讀取的帳本選擇要取消的待成交單或要平倉的部位。</p>
    <div class="fields">
      <div><label for="paperPendingOrder">待成交單</label>
        <select id="paperPendingOrder" disabled></select>
        <button id="paperCancel" type="button" disabled>取消模擬訂單</button></div>
      <div><label for="paperOpenPosition">未平倉部位</label>
        <select id="paperOpenPosition" disabled></select>
        <label for="paperClosePrice">平倉參考價</label>
        <input id="paperClosePrice" type="text" inputmode="decimal" maxlength="40">
        <button id="paperClose" type="button" disabled>手動模擬平倉</button></div>
    </div>
    <p id="paperActionStatus" role="status" aria-live="polite">尚未操作。</p>
    <pre id="paperActionResult" hidden></pre>
    <div id="paperRecovery" hidden>
      <p id="paperRecoveryResult" role="status" aria-live="polite"></p>
      <label><input id="paperRecoveryConfirm" type="checkbox">我已核對重啟後的 Paper 帳本</label>
      <button id="paperResume" type="button" disabled>完成核對並恢復操作</button>
    </div>
  </section>
  <p>Token 僅在本次送出時使用，不儲存在瀏覽器；此頁的畫面與程式碼都由本機提供。</p>
</main>
<script>
"use strict";
"""
    + SNAPSHOT_RECEIPT_SCRIPT
    + r"""
const MAX_SNAPSHOT_BYTES = 1024 * 1024;
const PAPER_RESTART_MESSAGE = "本機 Paper 寫入結果未明，已暫停所有模擬操作。請重啟同一個本機服務，再重新讀取帳本核對。";
const tokenInput = document.getElementById("token");
const fileInput = document.getElementById("snapshot");
const button = document.getElementById("analyze");
const status = document.getElementById("status");
const result = document.getElementById("result");
const paperButton = document.getElementById("paperRead");
const paperStatus = document.getElementById("paperStatus");
const paperResult = document.getElementById("paperResult");
const paperMode = document.getElementById("paperMode");
const paperLimitWrap = document.getElementById("paperLimitWrap");
const paperOrderButton = document.getElementById("paperOrder");
const paperTickButton = document.getElementById("paperTick");
const paperConfirm = document.getElementById("paperConfirm");
const paperActionStatus = document.getElementById("paperActionStatus");
const paperActionResult = document.getElementById("paperActionResult");
const pendingSelect = document.getElementById("paperPendingOrder");
const openSelect = document.getElementById("paperOpenPosition");
const cancelButton = document.getElementById("paperCancel");
const closeButton = document.getElementById("paperClose");
const recovery = document.getElementById("paperRecovery");
const recoveryResult = document.getElementById("paperRecoveryResult");
const recoveryConfirm = document.getElementById("paperRecoveryConfirm");
const resumeButton = document.getElementById("paperResume");
const PAPER_FENCE = "ctcc.paper.pending-operation.v1";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
let lastPaper = null;
let recoveryRead = null;
let paperBusy = false;

paperMode.addEventListener("change", () => {
  paperLimitWrap.hidden = paperMode.value !== "limit";
});

function showFailure(message) {
  result.textContent = "";
  result.hidden = true;
  status.textContent = message;
}

function acceptedOfflineResult(value) {
  return value !== null && typeof value === "object" &&
    value.schema_version === "ctcc.offline_snapshot_analysis.v1" &&
    /^[a-f0-9]{64}$/.test(value.input_sha256) &&
    value.source_authenticity_verified === false &&
    value.execution_authority === false &&
    value.analysis !== null && typeof value.analysis === "object" &&
    value.analysis.trade_ready === false &&
    Array.isArray(value.analysis.blockers) &&
    value.analysis.blockers.includes("imported_snapshot_unverified");
}

button.addEventListener("click", async () => {
  result.textContent = "";
  result.hidden = true;
  const token = tokenInput.value.trim();
  tokenInput.value = "";
  const chosen = selectSnapshotFiles(fileInput.files);
  if (!token) {
    showFailure("請輸入本機 API Token。");
    return;
  }
  if (!chosen || chosen.snapshot.size === 0 || chosen.snapshot.size > MAX_SNAPSHOT_BYTES) {
    showFailure("請選取一個 1 MiB 以下的非空快照。核對單最多一份。");
    return;
  }
  button.disabled = true;
  fileInput.disabled = true;
  status.textContent = "本機分析中…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const checked = await checkSnapshotReceipt(chosen.snapshot, chosen.receipt).catch(() => null);
    if (!checked) {
      showFailure("快照與核對單檢查未通過，未送出分析。");
      return;
    }
    const {file, digest} = checked;
    const response = await fetch("/api/analysis/snapshot", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CTCC-Token": token
      },
      body: file,
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      signal: controller.signal
    });
    if (response.status === 401 || response.status === 403) {
      showFailure("本機 API Token 驗證失敗，請重新輸入。");
      return;
    }
    if (response.status === 413) {
      showFailure("檔案超過本機分析容量上限。");
      return;
    }
    if (response.status === 422) {
      showFailure("快照格式或資料檢查未通過。");
      return;
    }
    if (!response.ok) {
      showFailure("本機分析暫時無法完成。");
      return;
    }
    const value = await response.json();
    if (!acceptedOfflineResult(value) || (digest && digest !== value.input_sha256)) {
      showFailure("分析結果格式或檔案雜湊不符，未顯示資料。");
      return;
    }
    result.textContent = JSON.stringify(value, null, 2);
    result.hidden = false;
    status.textContent = snapshotAnalysisFinished(digest);
  } catch {
    showFailure("本機分析連線中斷或逾時。");
  } finally {
    clearTimeout(timer);
    button.disabled = false;
    fileInput.disabled = false;
  }
});


function paperMarker() {
  try {
    const raw = localStorage.getItem(PAPER_FENCE);
    if (raw === null) return null;
    if (raw.length > 512) return false;
    const item = JSON.parse(raw);
    if (!item || Object.keys(item).length !== 5 ||
        !UUID.test(item.operation) || !UUID.test(item.runtime) || !DIGEST.test(item.installation) ||
        !["order", "tick", "cancel", "close"].includes(item.kind) ||
        (item.kind === "tick" ? item.target !== null : !UUID.test(item.target))) return false;
    return item;
  } catch {
    return false;
  }
}

function renderPaperControls() {
  const marker = paperMarker();
  const disabled = paperBusy || marker !== null || !navigator.locks;
  paperOrderButton.disabled = disabled;
  paperTickButton.disabled = disabled;
  pendingSelect.disabled = disabled || !pendingSelect.value;
  openSelect.disabled = disabled || !openSelect.value;
  cancelButton.disabled = pendingSelect.disabled;
  closeButton.disabled = openSelect.disabled;
  paperButton.disabled = paperBusy;
  recovery.hidden = marker === null;
  resumeButton.disabled = paperBusy || !recoveryRead || !recoveryConfirm.checked;
  if (marker === false || !navigator.locks) {
    paperActionStatus.textContent = "瀏覽器無法確認本機操作記錄。請保留記錄並改用支援的瀏覽器。";
  } else if (marker !== null && !paperBusy && !recoveryRead) {
    paperActionStatus.textContent = PAPER_RESTART_MESSAGE;
  }
}

function setInventory(select, rows, status) {
  const matches = rows.filter((row) => row && row.status === status && UUID.test(row.id));
  const unique = new Set(matches.map((row) => row.id));
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "請選擇";
  select.replaceChildren(placeholder);
  if (unique.size !== matches.length) return;
  for (const row of matches) {
    const option = document.createElement("option");
    option.value = row.id;
    option.textContent = String(row.symbol) + " / " + String(row.side) + " / " + row.id;
    select.append(option);
  }
  // A fresh inventory always requires a fresh explicit selection.
  select.value = "";
}

function clearRecovery() {
  recoveryRead = null;
  recoveryConfirm.checked = false;
  recoveryResult.textContent = "";
  resumeButton.disabled = true;
}

function recoveryDescription(marker, state) {
  if (marker.kind === "tick") return "請核對報價是否已改變訂單、部位和帳本。";
  if (marker.kind === "order") {
    const rows = state.orders.filter((order) => order?.client_order_id === "local-ui-" + marker.target);
    return rows.length === 1 ? "帳本找到原模擬訂單。" : "帳本尚無唯一的原模擬訂單。";
  }
  if (marker.kind === "cancel") {
    const rows = state.orders.filter((order) => order?.id === marker.target);
    return rows.length === 1 && rows[0].status === "cancelled"
      ? "帳本顯示該訂單已取消。" : "帳本未確認該訂單已取消。";
  }
  const rows = state.positions.filter((position) => position?.id === marker.target);
  return rows.length === 1 && rows[0].status === "closed" &&
    rows[0].close_reason === "manual_local:" + marker.operation
    ? "帳本找到本次手動平倉。" : "帳本未確認本次手動平倉。";
}

function paperFetch(path, options, signal) {
  return fetch(path, {
    ...options,
    credentials: "omit",
    cache: "no-store",
    redirect: "error",
    referrerPolicy: "no-referrer",
    signal
  });
}

async function refreshPaper(token) {
  lastPaper = null;
  clearRecovery();
  paperResult.textContent = "";
  paperResult.hidden = true;
  setInventory(pendingSelect, [], "pending");
  setInventory(openSelect, [], "open");
  paperStatus.textContent = "讀取本機 Paper 帳本中…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await paperFetch("/api/paper/runtime/state", {
      method: "GET", headers: {"X-CTCC-Token": token}
    }, controller.signal);
    if (!response.ok) {
      paperStatus.textContent = response.status === 401 || response.status === 403
        ? "本機 API Token 驗證失敗，請重新輸入。"
        : "本機 Paper 帳本暫時無法讀取。";
      return null;
    }
    const value = await response.json();
    const state = value?.state;
    if (!UUID.test(value?.runtime_id) || !DIGEST.test(value?.installation_binding) ||
        typeof value.persistent !== "boolean" || typeof value.recovered !== "boolean" || !state?.account ||
        !Array.isArray(state.orders) || !Array.isArray(state.positions)) {
      paperStatus.textContent = "本機 Paper 帳本格式不符。";
      return null;
    }
    lastPaper = value;
    setInventory(pendingSelect, state.orders, "pending");
    setInventory(openSelect, state.positions, "open");
    paperResult.textContent = JSON.stringify({account: state.account, state}, null, 2);
    paperResult.hidden = false;
    paperStatus.textContent = "已讀取本機 Paper 模擬帳本；OKX 實際持倉仍未知。";
    const marker = paperMarker();
    if (marker && value.persistent && value.recovered && value.installation_binding === marker.installation &&
        value.runtime_id !== marker.runtime) {
      recoveryRead = {marker, runtime: value.runtime_id, state: JSON.stringify(state)};
      recoveryResult.textContent = recoveryDescription(marker, state) + " 核對後才可恢復操作。";
    }
    return value;
  } catch {
    paperStatus.textContent = "本機 Paper 帳本連線中斷。";
    return null;
  } finally {
    clearTimeout(timer);
    renderPaperControls();
  }
}

function consumeToken() {
  const token = tokenInput.value.trim();
  tokenInput.value = "";
  return token;
}

paperButton.addEventListener("click", async () => {
  if (paperBusy) return;
  const token = consumeToken();
  if (!token) {
    paperStatus.textContent = "請輸入本機 API Token。";
    return;
  }
  paperBusy = true;
  renderPaperControls();
  try {
    await refreshPaper(token);
  } finally {
    paperBusy = false;
    renderPaperControls();
  }
});

function positiveDecimal(id) {
  const value = document.getElementById(id).value.trim();
  return /^(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,12})?$/.test(value) &&
    Number(value) > 0 ? value : null;
}

function paperSymbol(id) {
  const value = document.getElementById(id).value.trim().toUpperCase();
  return /^[A-Z0-9]+-[A-Z0-9]+-SWAP$/.test(value) && value.length <= 40
    ? value : null;
}

async function paperPost(path, token, payload, runtimeId) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const request = {method: "POST", headers: {"X-CTCC-Token": token,
      "X-CTCC-Paper-Runtime": runtimeId}};
    // Cancel is the bodyless POST already required by the local transport.
    if (payload !== undefined) {
      request.headers["Content-Type"] = "application/json";
      request.body = JSON.stringify(payload);
    }
    const response = await paperFetch(path, request, controller.signal);
    return {
      status: response.status,
      value: response.ok || response.status === 409 ? await response.json() : null
    };
  } finally {
    clearTimeout(timer);
  }
}

function uniqueTarget(rows, id, status) {
  return UUID.test(id) && rows.filter((row) => row?.id === id && row.status === status).length === 1;
}

async function runPaperAction(kind, path, payload, expected, target = null) {
  if (paperBusy || paperMarker() !== null || !navigator.locks) return;
  if (!paperConfirm.checked) {
    paperActionStatus.textContent = "請先確認這是本機 Paper 模擬操作。";
    return;
  }
  paperConfirm.checked = false;
  const token = consumeToken();
  if (!token || !globalThis.crypto || typeof globalThis.crypto.randomUUID !== "function") {
    paperActionStatus.textContent = "請輸入本機 API Token，並使用支援的瀏覽器。";
    return;
  }
  const operation = globalThis.crypto.randomUUID();
  if (kind === "order") {
    payload.client_order_id = "local-ui-" + operation;
    target = operation;
  } else if (kind === "close") {
    payload.reason = "manual_local:" + operation;
  }
  paperBusy = true;
  paperActionResult.hidden = true;
  renderPaperControls();
  try {
    await navigator.locks.request(PAPER_FENCE, {ifAvailable: true}, async (lock) => {
      if (!lock || paperMarker() !== null) return;
      const runtime = await refreshPaper(token);
      if (!runtime || !runtime.persistent) {
        paperActionStatus.textContent = "需要已確認的持久 Paper 帳本，未送出操作。";
        return;
      }
      if ((kind === "cancel" && !uniqueTarget(runtime.state.orders, target, "pending")) ||
          (kind === "close" && !uniqueTarget(runtime.state.positions, target, "open"))) {
        paperActionStatus.textContent = "選擇的帳本項目已改變，未送出操作。";
        return;
      }
      const marker = {operation, runtime: runtime.runtime_id,
        installation: runtime.installation_binding, kind, target};
      const retained = JSON.stringify(marker);
      localStorage.setItem(PAPER_FENCE, retained);
      if (localStorage.getItem(PAPER_FENCE) !== retained) return;
      paperActionStatus.textContent = "送出本機模擬操作中…";
      const response = await paperPost(path, token, payload, runtime.runtime_id);
      if ([401, 403, 422].includes(response.status) ||
          (response.status === 409 && response.value?.detail === "paper_runtime_changed")) {
        if (localStorage.getItem(PAPER_FENCE) === retained) localStorage.removeItem(PAPER_FENCE);
        paperActionStatus.textContent = "本機驗證或資料檢查未通過，模擬操作未受理。";
        return;
      }
      if (!expected(response, payload)) return;
      if (localStorage.getItem(PAPER_FENCE) === retained) localStorage.removeItem(PAPER_FENCE);
      paperActionResult.textContent = JSON.stringify(response.value, null, 2);
      paperActionResult.hidden = false;
      paperActionStatus.textContent = "本機模擬操作已受理。";
      if (!await refreshPaper(token)) {
        paperActionStatus.textContent = "操作已受理，請重新讀取本機帳本。";
      }
    });
  } catch {
    paperActionStatus.textContent = "未取得本機操作結果。";
  } finally {
    paperBusy = false;
    renderPaperControls();
  }
}

paperOrderButton.addEventListener("click", async () => {
  const symbol = paperSymbol("paperSymbol");
  const quantity = positiveDecimal("paperQuantity");
  const reference = positiveDecimal("paperReference");
  const stop = positiveDecimal("paperStop");
  const priceTarget = positiveDecimal("paperTarget");
  const side = document.getElementById("paperSide").value;
  const orderType = paperMode.value;
  const limit = orderType === "limit" ? positiveDecimal("paperLimit") : null;
  if (!symbol || !quantity || !reference || !stop || !priceTarget ||
      !["long", "short"].includes(side) || !["market", "limit"].includes(orderType) ||
      (orderType === "limit" && !limit)) {
    paperActionStatus.textContent = "請填寫完整的商品與正數價格、數量。";
    return;
  }
  const payload = {symbol, side, quantity, reference_price: reference,
    stop_loss: stop, take_profit: priceTarget, order_type: orderType, strategy: "manual_local"};
  if (limit) payload.limit_price = limit;
  await runPaperAction("order", "/api/paper/orders", payload, ({status, value}, wanted) =>
    status === 201 && UUID.test(value?.id) && value.client_order_id === wanted.client_order_id &&
    value.symbol === symbol && value.side === side && value.order_type === orderType &&
    ["filled", "pending"].includes(value.status));
});

paperTickButton.addEventListener("click", async () => {
  const symbol = paperSymbol("paperTickSymbol");
  const price = positiveDecimal("paperTickPrice");
  if (!symbol || !price) {
    paperActionStatus.textContent = "請填寫報價商品與正數價格。";
    return;
  }
  await runPaperAction("tick", "/api/paper/ticks", {symbol, price}, ({status, value}) =>
    status === 200 && value?.symbol === symbol && value.account &&
    Array.isArray(value.filled_order_ids) && Array.isArray(value.closed_position_ids));
});

cancelButton.addEventListener("click", async () => {
  const id = pendingSelect.value;
  if (!lastPaper || !uniqueTarget(lastPaper.state.orders, id, "pending")) return;
  await runPaperAction("cancel", "/api/paper/orders/" + id + "/cancel", undefined, ({status, value}) =>
    status === 200 && value?.id === id && value.status === "cancelled", id);
});

closeButton.addEventListener("click", async () => {
  const id = openSelect.value;
  const price = positiveDecimal("paperClosePrice");
  if (!lastPaper || !uniqueTarget(lastPaper.state.positions, id, "open")) return;
  if (!price) {
    paperActionStatus.textContent = "請輸入正數平倉參考價。";
    return;
  }
  await runPaperAction("close", "/api/paper/positions/" + id + "/close", {price}, ({status, value}, wanted) =>
    status === 200 && value?.id === id && value.status === "closed" &&
    value.close_reason === wanted.reason, id);
});

resumeButton.addEventListener("click", async () => {
  if (!recoveryRead || !recoveryConfirm.checked || paperBusy || !navigator.locks) return;
  const token = consumeToken();
  if (!token) {
    paperActionStatus.textContent = "請重新輸入本機 API Token。";
    return;
  }
  const accepted = recoveryRead;
  const retained = JSON.stringify(accepted.marker);
  paperBusy = true;
  renderPaperControls();
  try {
    await navigator.locks.request(PAPER_FENCE, {ifAvailable: true}, async (lock) => {
      if (!lock || localStorage.getItem(PAPER_FENCE) !== retained) return;
      await refreshPaper(token);
      if (!recoveryRead || recoveryRead.runtime !== accepted.runtime || recoveryRead.state !== accepted.state ||
          JSON.stringify(recoveryRead.marker) !== retained || localStorage.getItem(PAPER_FENCE) !== retained) return;
      localStorage.removeItem(PAPER_FENCE);
      clearRecovery();
      paperActionStatus.textContent = "帳本已核對，已恢復本機模擬操作。";
    });
  } catch {
    paperActionStatus.textContent = "未能完成本機帳本核對。";
  } finally {
    paperBusy = false;
    renderPaperControls();
  }
});

pendingSelect.addEventListener("change", renderPaperControls);
openSelect.addEventListener("change", renderPaperControls);
recoveryConfirm.addEventListener("change", renderPaperControls);
window.addEventListener("storage", (event) => {
  if (event.key === PAPER_FENCE || event.key === null) {
    clearRecovery();
    renderPaperControls();
  }
});
renderPaperControls();

</script>
</body>
</html>
"""
)
