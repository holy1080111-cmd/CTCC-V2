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
    input { display: block; max-width: 100%; width: 100%; padding: 10px;
            border: 1px solid #526982; border-radius: 8px; background: #08131e;
            color: #ecf4ff; }
    input[type="file"] { padding: 9px; }
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
    <a href="/dashboard">控制中心</a>
    <a href="/readiness">本機服務狀態</a>
    <a href="/docs#/paper">Paper 模擬 API 文件</a>
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
  <section class="panel" aria-label="本機 Paper 帳本">
    <h2>本機 Paper 帳本</h2>
    <p>只讀取本機模擬帳本與狀態，不代表 OKX 帳戶、持倉或交易資格。
       請在上方重新輸入 Token 後讀取。</p>
    <button id="paperRead" type="button">讀取 Paper 帳本</button>
    <p id="paperStatus" role="status" aria-live="polite">尚未讀取。</p>
    <pre id="paperResult" hidden></pre>
  </section>
  <p>Token 僅在本次送出時使用，不儲存在瀏覽器。API 文件頁可能依賴外網資源；
     此快照分析頁的畫面與程式碼都由本機提供。</p>
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

paperButton.addEventListener("click", async () => {
  paperResult.textContent = "";
  paperResult.hidden = true;
  const token = tokenInput.value.trim();
  tokenInput.value = "";
  if (!token) {
    paperStatus.textContent = "請輸入本機 API Token。";
    return;
  }
  paperButton.disabled = true;
  paperStatus.textContent = "讀取本機 Paper 帳本中…";
  try {
    const values = [];
    for (const path of ["/api/paper/account", "/api/paper/state"]) {
      const response = await fetch(path, {
        method: "GET",
        headers: {"X-CTCC-Token": token},
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer"
      });
      if (!response.ok) {
        paperStatus.textContent = response.status === 401 || response.status === 403
          ? "本機 API Token 驗證失敗，請重新輸入。"
          : "本機 Paper 帳本暫時無法讀取。";
        return;
      }
      values.push(await response.json());
    }
    paperResult.textContent = JSON.stringify({account: values[0], state: values[1]}, null, 2);
    paperResult.hidden = false;
    paperStatus.textContent = "已讀取本機 Paper 模擬帳本；OKX 實際持倉仍未知。";
  } catch {
    paperStatus.textContent = "本機 Paper 帳本連線中斷。";
  } finally {
    paperButton.disabled = false;
  }
});
</script>
</body>
</html>
"""
