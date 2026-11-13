"""Standalone, self-contained page for untrusted offline snapshot analysis."""

OFFLINE_SNAPSHOT_ONLY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": (
        "default-src 'none'; connect-src 'self'; "
        "style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}

OFFLINE_SNAPSHOT_ONLY_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CTCC 離線快照分析</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, "Noto Sans TC", sans-serif; }
    body { margin: 0; background: #071019; color: #ecf4ff; }
    main { width: min(820px, calc(100% - 32px)); margin: 32px auto; }
    h1 { margin-bottom: 8px; }
    p { line-height: 1.65; color: #bfd0e2; }
    a { color: #8dbfff; }
    section { background: #101b27; border: 1px solid #30445b; border-radius: 14px;
              padding: 20px; margin: 18px 0; }
    .warning { border-color: #b88b38; color: #ffe0a0; }
    label { display: block; margin: 16px 0 6px; font-weight: 600; }
    input { display: block; max-width: 100%; width: 100%; padding: 10px;
            border: 1px solid #526982; border-radius: 8px; background: #08131e;
            color: #ecf4ff; }
    button { margin-top: 16px; padding: 10px 17px; border: 1px solid #78acfa;
             border-radius: 8px; background: #224978; color: white; cursor: pointer; }
    button:disabled { cursor: wait; opacity: 0.6; }
    pre { overflow: auto; max-height: 65vh; white-space: pre-wrap; word-break: break-word;
          padding: 16px; border: 1px solid #30445b; border-radius: 8px;
          background: #08131e; color: #e6f0ff; }
  </style>
</head>
<body>
<main>
  <a href="/readiness">查看本機狀態</a>
  <h1>CTCC 離線快照分析</h1>
  <p>匯入已儲存的 MarketSnapshot JSON。此工具只在本機重新計算分析，
     不連接交易所、資料庫或 Paper 帳本，也不送出訂單。</p>
  <section class="warning">
    匯入資料來源未經驗證，結果只供離線觀察；交易資格與執行權限維持關閉。
  </section>
  <section>
    <label for="token">本機 CTCC API Token</label>
    <input id="token" type="password" autocomplete="off" spellcheck="false">
    <label for="snapshot">MarketSnapshot JSON 檔案（最多 1 MiB）</label>
    <input id="snapshot" type="file" accept=".json,application/json">
    <button id="analyze" type="button">開始離線分析</button>
    <p id="status" role="status" aria-live="polite">尚未分析。</p>
  </section>
  <section aria-label="分析結果"><h2>分析結果</h2><pre id="result" hidden></pre></section>
  <p>Token 僅用於本次請求，不儲存在瀏覽器。此頁不載入外部字型、程式或圖片。</p>
</main>
<script>
"use strict";
const MAX_SNAPSHOT_BYTES = 1024 * 1024;
const tokenInput = document.getElementById("token");
const fileInput = document.getElementById("snapshot");
const button = document.getElementById("analyze");
const status = document.getElementById("status");
const result = document.getElementById("result");

function fail(message) {
  result.textContent = "";
  result.hidden = true;
  status.textContent = message;
}

function accepted(value) {
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
  if (!token) { fail("請輸入本機 API Token。"); return; }
  if (!file || file.size === 0 || file.size > MAX_SNAPSHOT_BYTES) {
    fail("請選取 1 MiB 以下的非空 JSON 檔案。");
    return;
  }
  button.disabled = true;
  status.textContent = "離線分析中…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch("/api/analysis/snapshot", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CTCC-Token": token},
      body: file,
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
      signal: controller.signal
    });
    if (response.status === 401 || response.status === 403) {
      fail("本機 API Token 驗證失敗，請重新輸入。");
      return;
    }
    if (response.status === 413) { fail("檔案超過容量上限。"); return; }
    if (response.status === 422) { fail("快照資料檢查未通過。"); return; }
    if (!response.ok) { fail("本機分析暫時無法完成。"); return; }
    const value = await response.json();
    if (!accepted(value)) { fail("分析結果格式不符，未顯示資料。"); return; }
    result.textContent = JSON.stringify(value, null, 2);
    result.hidden = false;
    status.textContent = "分析完成；匯入來源仍未驗證，交易資格維持拒絕。";
  } catch {
    fail("本機分析連線中斷或逾時。");
  } finally {
    clearTimeout(timer);
    button.disabled = false;
  }
});
</script>
</body>
</html>
"""
