"""Standalone, self-contained page for untrusted offline snapshot analysis."""

from app.dashboard.snapshot_receipt import SNAPSHOT_RECEIPT_SCRIPT

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

OFFLINE_SNAPSHOT_ONLY_HTML = (
    r"""<!doctype html>
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
    dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
         gap: 12px; }
    dt { color: #bfd0e2; font-size: .9rem; }
    dd { margin: 5px 0 0; font-weight: 650; overflow-wrap: anywhere; }
    table { width: 100%; border-collapse: collapse; margin: 12px 0 22px; }
    th, td { text-align: left; border-bottom: 1px solid #30445b; padding: 10px 4px; }
    summary { cursor: pointer; color: #8dbfff; }
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
    <label for="snapshot">MarketSnapshot JSON（最多 1 MiB）與選填的匯出核對單</label>
    <input id="snapshot" type="file" accept=".json,application/json" multiple aria-describedby="snapshotHint">
    <p id="snapshotHint">有核對單時，在同一個視窗選取兩個檔案。</p>
    <button id="analyze" type="button">開始離線分析</button>
    <p id="status" role="status" aria-live="polite">尚未分析。</p>
  </section>
  <section id="analysisSection" hidden aria-labelledby="analysisHeading">
    <h2 id="analysisHeading">分析結果</h2>
    <p>快照時間（UTC） <time id="captureTime"></time></p>
    <dl>
      <div><dt>合約</dt><dd id="contract"></dd></div>
      <div><dt>快照價格</dt><dd id="capturePrice"></dd></div>
      <div><dt>市場型態</dt><dd id="regime"></dd></div>
      <div><dt>綜合方向</dt><dd id="bias"></dd></div>
    </dl>
    <table aria-label="四個週期的分析">
      <thead><tr><th scope="col">週期</th><th scope="col">方向</th><th scope="col">波動</th><th scope="col">資料檢查</th></tr></thead>
      <tbody id="timeframes"></tbody>
    </table>
    <details><summary>完整 JSON 結果</summary><pre id="result"></pre></details>
  </section>
  <p>Token 僅用於本次請求，不儲存在瀏覽器。此頁不載入外部字型、程式或圖片。</p>
</main>
<script>
"use strict";
"""
    + SNAPSHOT_RECEIPT_SCRIPT
    + r"""
const MAX_SNAPSHOT_BYTES = 1024 * 1024;
const tokenInput = document.getElementById("token");
const fileInput = document.getElementById("snapshot");
const button = document.getElementById("analyze");
const status = document.getElementById("status");
const analysisSection = document.getElementById("analysisSection");
const result = document.getElementById("result");
const intervals = ["4H", "1H", "15m", "5m"];
const directionLabels = {long: "偏多", short: "偏空", neutral: "中性"};
const volatilityLabels = {low: "低", normal: "正常", high: "高", extreme: "極高"};
const regimeLabels = {insufficient_data: "資料不足", unstable_transition: "不穩定轉折",
  high_volatility: "高波動", bull_trend: "多頭趨勢", bear_trend: "空頭趨勢",
  compression: "波動收斂", range_or_transition: "盤整或轉折"};

function clearResult() {
  analysisSection.hidden = true;
  result.textContent = "";
}

function fail(message) {
  clearResult();
  status.textContent = message;
}

function accepted(value) {
  const analysis = value?.analysis;
  return value !== null && typeof value === "object" && analysis &&
    value.schema_version === "ctcc.offline_snapshot_analysis.v1" &&
    /^[a-f0-9]{64}$/.test(value.input_sha256) &&
    value.source_authenticity_verified === false &&
    value.execution_authority === false &&
    analysis.trade_ready === false &&
    Array.isArray(analysis.blockers) &&
    analysis.blockers.includes("imported_snapshot_unverified") &&
    typeof analysis.instrument_id === "string" &&
    typeof analysis.price === "string" &&
    typeof analysis.regime === "string" &&
    typeof analysis.generated_at === "string" &&
    analysis.generated_at.endsWith("Z") &&
    Object.hasOwn(directionLabels, analysis.overall_bias) &&
    intervals.every(name => {
      const row = analysis.timeframe_analyses?.[name];
      return row && row.timeframe === name &&
        Object.hasOwn(directionLabels, row.directional_bias) &&
        Object.hasOwn(volatilityLabels, row.volatility) &&
        typeof row.data_quality_ok === "boolean";
    });
}

function showAnalysis(value) {
  const analysis = value.analysis;
  const captureTime = document.getElementById("captureTime");
  captureTime.dateTime = analysis.generated_at;
  captureTime.textContent = analysis.generated_at.slice(0, -1).replace("T", " ");
  document.getElementById("contract").textContent = analysis.instrument_id;
  document.getElementById("capturePrice").textContent = analysis.price;
  document.getElementById("regime").textContent =
    Object.hasOwn(regimeLabels, analysis.regime) ? regimeLabels[analysis.regime] : analysis.regime;
  document.getElementById("bias").textContent = directionLabels[analysis.overall_bias];
  const rows = intervals.map(name => {
    const reading = analysis.timeframe_analyses[name];
    const row = document.createElement("tr");
    const title = document.createElement("th");
    title.scope = "row";
    title.textContent = name;
    row.append(title);
    for (const label of [directionLabels[reading.directional_bias],
                         volatilityLabels[reading.volatility],
                         reading.data_quality_ok ? "通過" : "有缺漏或異常"]) {
      const cell = document.createElement("td");
      cell.textContent = label;
      row.append(cell);
    }
    return row;
  });
  document.getElementById("timeframes").replaceChildren(...rows);
  result.textContent = JSON.stringify(value, null, 2);
  analysisSection.hidden = false;
}

button.addEventListener("click", async () => {
  clearResult();
  const token = tokenInput.value.trim();
  tokenInput.value = "";
  const chosen = selectSnapshotFiles(fileInput.files);
  if (!token) { fail("請輸入本機 API Token。"); return; }
  if (!chosen || chosen.snapshot.size === 0 || chosen.snapshot.size > MAX_SNAPSHOT_BYTES) {
    fail("請選取一個 1 MiB 以下的非空快照。核對單最多一份。");
    return;
  }
  button.disabled = true;
  fileInput.disabled = true;
  status.textContent = "離線分析中…";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const checked = await checkSnapshotReceipt(chosen.snapshot, chosen.receipt).catch(() => null);
    if (!checked) { fail("快照與核對單檢查未通過，未送出分析。"); return; }
    const {file, digest} = checked;
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
    if (!accepted(value) || (digest && digest !== value.input_sha256)) {
      fail("分析結果格式或檔案雜湊不符，未顯示資料。"); return;
    }
    showAnalysis(value);
    status.textContent = snapshotAnalysisFinished(digest);
  } catch {
    fail("本機分析連線中斷或逾時。");
  } finally {
    clearTimeout(timer);
    button.disabled = false;
    fileInput.disabled = false;
  }
});
</script>
</body>
</html>
"""
)
