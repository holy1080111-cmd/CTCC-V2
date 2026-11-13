"""Self-contained browser page for the offline, untrusted snapshot analysis API."""

LOCAL_ANALYSIS_HTML = r"""<!doctype html>
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
    <label for="snapshot">MarketSnapshot JSON 檔案（最多 1 MiB）</label>
    <input id="snapshot" type="file" accept=".json,application/json">
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
    <button id="paperOrder" type="button">送出模擬訂單</button>
    <p>手動報價可能觸發本機限價單成交、停利或停損。請先核對帳本。</p>
    <div class="fields">
      <div><label for="paperTickSymbol">報價商品</label>
        <input id="paperTickSymbol" type="text" maxlength="40" placeholder="BTC-USDT-SWAP"></div>
      <div><label for="paperTickPrice">模擬報價</label>
        <input id="paperTickPrice" type="text" inputmode="decimal" maxlength="40"></div>
    </div>
    <button id="paperTick" type="button">送出模擬報價</button>
    <p id="paperActionStatus" role="status" aria-live="polite">尚未操作。</p>
    <pre id="paperActionResult" hidden></pre>
  </section>
  <p>Token 僅在本次送出時使用，不儲存在瀏覽器；此頁的畫面與程式碼都由本機提供。</p>
</main>
<script>
"use strict";
const MAX_SNAPSHOT_BYTES = 1024 * 1024;
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
let pendingPaperOrder = null;

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
  const file = fileInput.files && fileInput.files[0];
  if (!token) {
    showFailure("請輸入本機 API Token。");
    return;
  }
  if (!file || file.size === 0 || file.size > MAX_SNAPSHOT_BYTES) {
    showFailure("請選取 1 MiB 以下的非空 JSON 檔案。");
    return;
  }
  button.disabled = true;
  status.textContent = "本機分析中…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
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
    if (!acceptedOfflineResult(value)) {
      showFailure("分析結果格式不符，未顯示資料。");
      return;
    }
    result.textContent = JSON.stringify(value, null, 2);
    result.hidden = false;
    status.textContent = "分析完成。匯入來源仍未驗證，交易資格維持拒絕。";
  } catch {
    showFailure("本機分析連線中斷或逾時。");
  } finally {
    clearTimeout(timer);
    button.disabled = false;
  }
});

async function refreshPaper(token) {
  paperResult.textContent = "";
  paperResult.hidden = true;
  paperStatus.textContent = "讀取本機 Paper 帳本中…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const values = [];
    for (const path of ["/api/paper/account", "/api/paper/state"]) {
      const response = await fetch(path, {
        method: "GET",
        headers: {"X-CTCC-Token": token},
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: controller.signal
      });
      if (!response.ok) {
        paperStatus.textContent = response.status === 401 || response.status === 403
          ? "本機 API Token 驗證失敗，請重新輸入。"
          : "本機 Paper 帳本暫時無法讀取。";
        return false;
      }
      values.push(await response.json());
    }
    if (!values[0] || !values[1] || !Array.isArray(values[1].orders)) {
      paperStatus.textContent = "本機 Paper 帳本格式不符。";
      return false;
    }
    paperResult.textContent = JSON.stringify({account: values[0], state: values[1]}, null, 2);
    paperResult.hidden = false;
    paperStatus.textContent = "已讀取本機 Paper 模擬帳本；OKX 實際持倉仍未知。";
    if (pendingPaperOrder) {
      const matches = values[1].orders.filter(
        (order) => order.client_order_id === pendingPaperOrder.id
      );
      if (matches.length === 1) {
        paperActionResult.textContent = JSON.stringify(matches[0], null, 2);
        paperActionResult.hidden = false;
        paperActionStatus.textContent = "已在本機帳本找到同一模擬請求識別碼。";
        pendingPaperOrder = null;
      }
    }
    return true;
  } catch {
    paperStatus.textContent = "本機 Paper 帳本連線中斷。";
    return false;
  } finally {
    clearTimeout(timer);
  }
}

paperButton.addEventListener("click", async () => {
  const token = tokenInput.value.trim();
  tokenInput.value = "";
  if (!token) {
    paperStatus.textContent = "請輸入本機 API Token。";
    return;
  }
  paperButton.disabled = true;
  try {
    await refreshPaper(token);
  } finally {
    paperButton.disabled = false;
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

function setPaperActionBusy(busy) {
  paperOrderButton.disabled = busy;
  paperTickButton.disabled = busy;
}

async function paperPost(path, token, payload) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CTCC-Token": token
      },
      body: JSON.stringify(payload),
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      signal: controller.signal
    });
    return {
      status: response.status,
      ok: response.ok,
      value: response.ok ? await response.json() : null
    };
  } finally {
    clearTimeout(timer);
  }
}

function showUncertainPaperOrder(message) {
  pendingPaperOrder.uncertain = true;
  paperActionStatus.textContent = message;
  paperActionResult.textContent = JSON.stringify(
    {client_order_id: pendingPaperOrder.id}, null, 2
  );
  paperActionResult.hidden = false;
}

paperOrderButton.addEventListener("click", async () => {
  if (!pendingPaperOrder) {
    paperActionResult.textContent = "";
    paperActionResult.hidden = true;
  }
  if (!paperConfirm.checked) {
    paperActionStatus.textContent = "請先確認這是本機 Paper 模擬操作。";
    return;
  }
  const symbol = paperSymbol("paperSymbol");
  const quantity = positiveDecimal("paperQuantity");
  const reference = positiveDecimal("paperReference");
  const stop = positiveDecimal("paperStop");
  const target = positiveDecimal("paperTarget");
  const limit = paperMode.value === "limit" ? positiveDecimal("paperLimit") : null;
  if (!symbol || !quantity || !reference || !stop || !target ||
      (paperMode.value === "limit" && !limit)) {
    paperActionStatus.textContent = "請填寫完整的商品與正數價格、數量。";
    return;
  }
  const payload = {
    symbol: symbol,
    side: document.getElementById("paperSide").value,
    quantity: quantity,
    reference_price: reference,
    stop_loss: stop,
    take_profit: target,
    order_type: paperMode.value,
    strategy: "manual_local"
  };
  if (paperMode.value === "limit") {
    payload.limit_price = limit;
  }
  const fingerprint = JSON.stringify(payload);
  if (pendingPaperOrder && pendingPaperOrder.fingerprint !== fingerprint) {
    showUncertainPaperOrder("上一筆模擬請求結果未明；請先讀取帳本核對，不可變更內容重送。");
    return;
  }
  const token = tokenInput.value.trim();
  tokenInput.value = "";
  if (!token) {
    paperActionStatus.textContent = "請輸入本機 API Token。";
    return;
  }
  if (!pendingPaperOrder) {
    if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== "function") {
      paperActionStatus.textContent = "無法產生本機模擬請求識別碼，未送出。";
      return;
    }
    pendingPaperOrder = {
      id: `local-ui-${globalThis.crypto.randomUUID()}`,
      fingerprint: fingerprint,
      uncertain: false
    };
  }
  paperConfirm.checked = false;
  setPaperActionBusy(true);
  paperActionStatus.textContent = "送出本機模擬請求中…";
  try {
    const response = await paperPost("/api/paper/orders", token, {
      ...payload, client_order_id: pendingPaperOrder.id
    });
    if (response.status === 401 || response.status === 403) {
      if (pendingPaperOrder.uncertain) {
        showUncertainPaperOrder("本次 Token 驗證失敗；先前模擬請求仍未確認，請讀取帳本。");
      } else {
        pendingPaperOrder = null;
        paperActionStatus.textContent = "本機 API Token 驗證失敗；模擬請求未授權。";
      }
      return;
    }
    if (response.status === 422) {
      if (pendingPaperOrder.uncertain) {
        showUncertainPaperOrder("本次模擬請求資料遭拒；先前請求仍未確認，請讀取帳本。");
      } else {
        pendingPaperOrder = null;
        paperActionStatus.textContent = "模擬訂單資料未通過檢查，請修正後重新確認。";
      }
      return;
    }
    if (response.status !== 201) {
      showUncertainPaperOrder("模擬訂單結果未明；請讀取帳本核對，勿以新識別碼重送。");
      return;
    }
    const value = response.value;
    if (!value || value.client_order_id !== pendingPaperOrder.id ||
        value.symbol !== symbol || value.side !== payload.side ||
        value.order_type !== payload.order_type || typeof value.id !== "string") {
      showUncertainPaperOrder("模擬訂單回應不符；請讀取帳本核對，勿重送。");
      return;
    }
    paperActionResult.textContent = JSON.stringify(value, null, 2);
    paperActionResult.hidden = false;
    pendingPaperOrder = null;
    paperActionStatus.textContent = "本機模擬訂單已受理；正在重新讀取帳本。";
    if (!await refreshPaper(token)) {
      paperActionStatus.textContent = "模擬訂單已受理，但帳本重新讀取失敗。";
    }
  } catch {
    showUncertainPaperOrder("模擬訂單結果未明；請讀取帳本核對，勿以新識別碼重送。");
  } finally {
    setPaperActionBusy(false);
  }
});

paperTickButton.addEventListener("click", async () => {
  if (!pendingPaperOrder) {
    paperActionResult.textContent = "";
    paperActionResult.hidden = true;
  }
  if (!paperConfirm.checked) {
    paperActionStatus.textContent = "請先確認這是本機 Paper 模擬操作。";
    return;
  }
  if (pendingPaperOrder) {
    paperActionStatus.textContent = "上一筆模擬訂單結果未明；請先讀取帳本核對。";
    return;
  }
  const symbol = paperSymbol("paperTickSymbol");
  const price = positiveDecimal("paperTickPrice");
  if (!symbol || !price) {
    paperActionStatus.textContent = "請填寫報價商品與正數價格。";
    return;
  }
  const token = tokenInput.value.trim();
  tokenInput.value = "";
  if (!token) {
    paperActionStatus.textContent = "請輸入本機 API Token。";
    return;
  }
  paperConfirm.checked = false;
  setPaperActionBusy(true);
  paperActionStatus.textContent = "送出本機模擬報價中…";
  try {
    const response = await paperPost("/api/paper/ticks", token, {symbol, price});
    if (response.status === 401 || response.status === 403) {
      paperActionStatus.textContent = "本機 API Token 驗證失敗；模擬報價未授權。";
      return;
    }
    if (response.status === 422) {
      paperActionStatus.textContent = "模擬報價資料未通過檢查。";
      return;
    }
    if (!response.ok) {
      paperActionStatus.textContent = "模擬報價結果未明；請讀取帳本核對，勿立即重送。";
      return;
    }
    const value = response.value;
    if (!value || value.symbol !== symbol || !value.account) {
      paperActionStatus.textContent = "模擬報價回應不符；請讀取帳本核對。";
      return;
    }
    paperActionResult.textContent = JSON.stringify(value, null, 2);
    paperActionResult.hidden = false;
    paperActionStatus.textContent = "本機模擬報價已受理；正在重新讀取帳本。";
    if (!await refreshPaper(token)) {
      paperActionStatus.textContent = "模擬報價已受理，但帳本重新讀取失敗。";
    }
  } catch {
    paperActionStatus.textContent = "模擬報價結果未明；請讀取帳本核對，勿立即重送。";
  } finally {
    setPaperActionBusy(false);
  }
});
</script>
</body>
</html>
"""
