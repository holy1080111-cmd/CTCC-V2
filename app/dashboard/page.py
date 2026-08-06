DASHBOARD_HTML = r"""
<!doctype html>
<html lang="zh-Hant">
<head>
    <meta charset="utf-8">
    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >
    <title>CTCC v1.6 Control Center</title>

    <style>
        :root {
            color-scheme: dark;
            --bg: #071019;
            --panel: #101b27;
            --panel-2: #152334;
            --border: #26384d;
            --text: #ecf4ff;
            --muted: #9eb0c5;
            --good: #55d69e;
            --warn: #f0bf63;
            --bad: #ff727d;
            --accent: #65a9ff;
        }

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            background:
                radial-gradient(
                    circle at top right,
                    #102846,
                    transparent 38%
                ),
                var(--bg);
            color: var(--text);
            font-family:
                Inter,
                "Noto Sans TC",
                system-ui,
                sans-serif;
        }

        button {
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 10px 14px;
            background: var(--panel-2);
            color: var(--text);
            cursor: pointer;
        }

        button:hover {
            border-color: var(--accent);
        }

        .shell {
            width: min(1480px, calc(100% - 28px));
            margin: 0 auto;
            padding: 22px 0 50px;
        }

        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            margin-bottom: 20px;
        }

        .topbar h1 {
            margin: 0;
            font-size: clamp(24px, 4vw, 38px);
        }

        .subtitle {
            margin-top: 5px;
            color: var(--muted);
        }

        .actions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            justify-content: flex-end;
        }

        .notice {
            display: none;
            margin-bottom: 16px;
            padding: 12px 14px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: var(--panel);
        }

        .notice.visible {
            display: block;
        }

        .notice.error {
            border-color: var(--bad);
            color: #ffd7da;
        }

        .notice.success {
            border-color: var(--good);
            color: #d6ffec;
        }

        .grid {
            display: grid;
            grid-template-columns:
                repeat(auto-fit, minmax(210px, 1fr));
            gap: 12px;
        }

        .card,
        .section {
            border: 1px solid var(--border);
            border-radius: 16px;
            background: rgba(16, 27, 39, 0.94);
            box-shadow: 0 14px 40px rgba(0, 0, 0, 0.16);
        }

        .card {
            min-height: 126px;
            padding: 16px;
        }

        .card-label {
            color: var(--muted);
            font-size: 13px;
        }

        .card-value {
            margin-top: 12px;
            font-size: 25px;
            font-weight: 700;
            word-break: break-word;
        }

        .card-note {
            margin-top: 7px;
            color: var(--muted);
            font-size: 12px;
        }

        .good {
            color: var(--good);
        }

        .warn {
            color: var(--warn);
        }

        .bad {
            color: var(--bad);
        }

        .section {
            margin-top: 16px;
            padding: 17px;
            overflow: hidden;
        }

        .section-head {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            margin-bottom: 13px;
        }

        .section h2 {
            margin: 0;
            font-size: 18px;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            border: 1px solid var(--border);
            border-radius: 999px;
            padding: 5px 9px;
            font-size: 12px;
            color: var(--muted);
        }

        .table-wrap {
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            min-width: 900px;
        }

        th,
        td {
            padding: 11px 10px;
            border-bottom: 1px solid var(--border);
            text-align: left;
            font-size: 13px;
            white-space: nowrap;
        }

        th {
            color: var(--muted);
            font-weight: 500;
        }

        .empty {
            color: var(--muted);
            padding: 16px 0 5px;
        }

        .event {
            display: grid;
            grid-template-columns: 160px 120px 1fr;
            gap: 12px;
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
        }

        .event:last-child {
            border-bottom: 0;
        }

        .event-time,
        .event-message {
            color: var(--muted);
            font-size: 13px;
        }

        .event-code {
            font-size: 13px;
            word-break: break-word;
        }

        details {
            margin-top: 16px;
        }

        summary {
            cursor: pointer;
            color: var(--muted);
        }

        pre {
            max-height: 420px;
            overflow: auto;
            padding: 14px;
            border: 1px solid var(--border);
            border-radius: 12px;
            background: #071019;
            color: #c9dbed;
            font-size: 12px;
        }

        .footer {
            margin-top: 22px;
            color: var(--muted);
            text-align: center;
            font-size: 12px;
        }

        @media (max-width: 760px) {
            .topbar {
                align-items: flex-start;
                flex-direction: column;
            }

            .actions {
                justify-content: flex-start;
            }

            .event {
                grid-template-columns: 1fr;
                gap: 4px;
            }
        }
    </style>
</head>

<body>
<div class="shell">
    <header class="topbar">
        <div>
            <h1>CTCC Control Center</h1>
            <div class="subtitle">
                v1.6 · OKX Demo · Read-only Dashboard
            </div>
        </div>

        <div class="actions">
            <button id="tokenButton">設定 API Token</button>
            <button id="refreshButton">立即更新</button>
            <button id="autoButton">停止自動更新</button>
        </div>
    </header>

    <div id="notice" class="notice"></div>

    <section class="grid">
        <article class="card">
            <div class="card-label">OKX Demo 總權益</div>
            <div id="equity" class="card-value">—</div>
            <div class="card-note">USDT</div>
        </article>

        <article class="card">
            <div class="card-label">今日損益</div>
            <div id="dailyPnl" class="card-value">—</div>
            <div id="dailyPnlNote" class="card-note">—</div>
        </article>

        <article class="card">
            <div class="card-label">實際持倉</div>
            <div id="positionCount" class="card-value">—</div>
            <div id="protectionNote" class="card-note">—</div>
        </article>

        <article class="card">
            <div class="card-label">Automation</div>
            <div id="automationState" class="card-value">—</div>
            <div id="automationNote" class="card-note">—</div>
        </article>

        <article class="card">
            <div class="card-label">安全鎖</div>
            <div id="safetyState" class="card-value">—</div>
            <div id="lockReasons" class="card-note">—</div>
        </article>

        <article class="card">
            <div class="card-label">Reliability Ready</div>
            <div id="reliabilityReady" class="card-value">—</div>
            <div id="reliabilityNote" class="card-note">—</div>
        </article>

        <article class="card">
            <div class="card-label">資料完整性</div>
            <div id="dataIntegrityState" class="card-value">尚未更新</div>
            <div id="dataIntegrityNote" class="card-note">
                等待第一次成功更新
            </div>
        </article>
    </section>

    <section class="section">
        <div class="section-head">
            <h2>目前持倉</h2>
            <span id="positionBadge" class="badge">讀取中</span>
        </div>

        <div class="table-wrap">
            <table>
                <thead>
                <tr>
                    <th>商品</th>
                    <th>方向</th>
                    <th>數量</th>
                    <th>模式</th>
                    <th>槓桿</th>
                    <th>進場價</th>
                    <th>標記價</th>
                    <th>初始保證金</th>
                    <th>浮動盈虧</th>
                    <th>盈虧率</th>
                    <th>保護狀態</th>
                    <th>強平價</th>
                </tr>
                </thead>
                <tbody id="positionRows"></tbody>
            </table>
        </div>

        <div id="positionEmpty" class="empty">
            尚未取得持倉資料。
        </div>
    </section>

    <section class="section">
        <div class="section-head">
            <h2>策略與績效</h2>
            <span id="performanceBadge" class="badge">讀取中</span>
        </div>

        <div class="grid">
            <article class="card">
                <div class="card-label">有效交易日</div>
                <div id="activeDays" class="card-value">—</div>
            </article>

            <article class="card">
                <div class="card-label">已實現交易</div>
                <div id="realizedTrades" class="card-value">—</div>
            </article>

            <article class="card">
                <div class="card-label">Profit Factor</div>
                <div id="profitFactor" class="card-value">—</div>
            </article>

            <article class="card">
                <div class="card-label">最大回撤</div>
                <div id="maxDrawdown" class="card-value">—</div>
            </article>
        </div>

        <details>
            <summary>查看原始績效資料</summary>
            <pre id="performanceRaw">尚未載入</pre>
        </details>
    </section>

    <section class="section">
        <div class="section-head">
            <h2>最近安全與監控事件</h2>
            <span id="eventBadge" class="badge">讀取中</span>
        </div>

        <div id="events"></div>
    </section>

    <div class="footer">
        此頁面只呼叫 GET 類型的只讀 API，不包含交易操作。
        API Token 僅保存於目前瀏覽器分頁的 sessionStorage。
    </div>
</div>

<script>
"use strict";

const TOKEN_KEY = "ctcc_dashboard_token";
let refreshTimer = null;
let autoRefresh = true;
let refreshInProgress = false;
let refreshCycleId = 0;
let latestRenderedCycleId = 0;

let lastSuccessfulRefreshAt = null;
let lastCycleSuccessCount = 0;
let lastCycleFailureCount = 0;
let lastCycleSpreadMs = null;
let lastContractError = null;

const endpointMetadata = new Map();

const REQUEST_TIMEOUT_MS = 12000;
const DATA_STALE_AFTER_MS = 90000;
const DATA_CONSISTENCY_WINDOW_MS = 5000;

const SUPPORTED_SNAPSHOT_CONTRACT_VERSION = "1.0";
const MAX_SNAPSHOT_FUTURE_SKEW_MS = 30000;

const EXPECTED_SNAPSHOT_SOURCES = Object.freeze([
    "balance",
    "positions",
    "algo_orders",
    "automation",
    "performance",
    "validation",
    "events"
]);

const byId = (id) => document.getElementById(id);

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function unwrapArray(payload) {
    if (Array.isArray(payload)) {
        return payload;
    }

    const keys = [
        "value",
        "items",
        "records",
        "events",
        "positions",
        "orders",
        "results"
    ];

    for (const key of keys) {
        if (Array.isArray(payload?.[key])) {
            return payload[key];
        }
    }

    return [];
}

function unwrapObject(payload) {
    if (!payload || Array.isArray(payload)) {
        return payload ?? {};
    }

    for (const key of ["value", "data", "balance", "summary"]) {
        const candidate = payload[key];

        if (
            candidate &&
            typeof candidate === "object" &&
            !Array.isArray(candidate)
        ) {
            return candidate;
        }
    }

    return payload;
}

function deepFind(value, names, depth = 0) {
    if (
        value === null ||
        value === undefined ||
        depth > 6
    ) {
        return undefined;
    }

    if (typeof value !== "object") {
        return undefined;
    }

    for (const name of names) {
        if (
            Object.prototype.hasOwnProperty.call(value, name) &&
            value[name] !== null &&
            value[name] !== undefined
        ) {
            return value[name];
        }
    }

    for (const child of Object.values(value)) {
        const found = deepFind(child, names, depth + 1);

        if (found !== undefined) {
            return found;
        }
    }

    return undefined;
}

function formatNumber(value, digits = 2) {
    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return "—";
    }

    const number = Number(value);

    if (!Number.isFinite(number)) {
        return escapeHtml(value);
    }

    return number.toLocaleString(
        "zh-TW",
        {
            maximumFractionDigits: digits,
            minimumFractionDigits: 0
        }
    );
}

function formatPercent(value, alreadyPercent = false) {
    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return "—";
    }

    const number = Number(value);

    if (!Number.isFinite(number)) {
        return escapeHtml(value);
    }

    const percent = alreadyPercent ? number : number * 100;
    return `${formatNumber(percent, 4)}%`;
}

function setTone(element, value) {
    element.classList.remove("good", "warn", "bad");

    const number = Number(value);

    if (!Number.isFinite(number)) {
        return;
    }

    if (number > 0) {
        element.classList.add("good");
    }

    if (number < 0) {
        element.classList.add("bad");
    }
}

function showNotice(message, type = "") {
    const notice = byId("notice");
    notice.textContent = message;
    notice.className = `notice visible ${type}`.trim();
}

function clearNotice() {
    byId("notice").className = "notice";
}

function requireToken() {
    const existing = sessionStorage.getItem(TOKEN_KEY);

    if (existing) {
        return existing;
    }

    const entered = window.prompt(
        "請輸入 CTCC API_TOKEN。\nToken 只保存在目前分頁。"
    );

    if (!entered || !entered.trim()) {
        throw new Error("尚未設定 API Token");
    }

    sessionStorage.setItem(TOKEN_KEY, entered.trim());
    return entered.trim();
}

async function apiGet(path, token) {
    const controller = new AbortController();
    const requestStartedAt = performance.now();

    const timeoutId = window.setTimeout(
        () => controller.abort(),
        REQUEST_TIMEOUT_MS
    );

    try {
        const response = await fetch(
            path,
            {
                method: "GET",
                cache: "no-store",
                signal: controller.signal,
                headers: {
                    "X-CTCC-Token": token
                }
            }
        );

        const responseText = await response.text();
        let payload = null;

        if (responseText) {
            try {
                payload = JSON.parse(responseText);
            } catch {
                payload = responseText;
            }
        }

        if (!response.ok) {
            if (
                response.status === 401 ||
                response.status === 403
            ) {
                sessionStorage.removeItem(TOKEN_KEY);
            }

            throw new Error(
                `${path} 回傳 ${response.status}: ${
                    typeof payload === "string"
                        ? payload
                        : JSON.stringify(payload)
                }`
            );
        }

        endpointMetadata.set(
            path,
            {
                lastSuccessAt: Date.now(),
                durationMs: Math.round(
                    performance.now() - requestStartedAt
                ),
                lastFailureAt: null,
                lastError: null
            }
        );

        return payload;
    } catch (error) {
        const previousMetadata =
            endpointMetadata.get(path) || {};

        endpointMetadata.set(
            path,
            {
                ...previousMetadata,
                lastFailureAt: Date.now(),
                lastError: error?.message || String(error)
            }
        );

        if (error?.name === "AbortError") {
            throw new Error(
                `${path} 請求超時（${
                    REQUEST_TIMEOUT_MS / 1000
                } 秒）`
            );
        }

        throw error;
    } finally {
        window.clearTimeout(timeoutId);
    }
}

function renderAutomation(automation) {
    const running = Boolean(automation.running);
    const armed = Boolean(automation.armed);
    const emergency = Boolean(automation.emergency_stop);
    const locked = Boolean(automation.locked);
    const writes = Boolean(automation.demo_writes_enabled);

    const automationElement = byId("automationState");
    const safetyElement = byId("safetyState");

    automationElement.textContent = running
        ? "運行中"
        : armed
            ? "已 Armed"
            : "已停止";

    automationElement.className =
        `card-value ${running || armed ? "warn" : "good"}`;

    byId("automationNote").textContent =
        `Writes=${writes} · Active=${
            automation.active_instrument_id || "無"
        }`;

    safetyElement.textContent = emergency
        ? "Emergency Stop"
        : locked
            ? "Locked"
            : "正常";

    safetyElement.className =
        `card-value ${emergency || locked ? "bad" : "good"}`;

    const reasons = Array.isArray(automation.lock_reasons)
        ? automation.lock_reasons.join(", ")
        : "";

    byId("lockReasons").textContent =
        reasons || "沒有鎖定原因";

    const dailyPnl = automation.daily_pnl;
    const pnlElement = byId("dailyPnl");

    pnlElement.textContent =
        `${formatNumber(dailyPnl, 4)} USDT`;

    setTone(pnlElement, dailyPnl);

    byId("dailyPnlNote").textContent =
        `今日交易 ${automation.trades_today ?? "—"} 筆`;
}

function renderBalance(balancePayload) {
    const balance = unwrapObject(balancePayload);

    const equity = deepFind(
        balance,
        [
            "total_equity",
            "totalEq",
            "equity",
            "eq"
        ]
    );

    byId("equity").textContent =
        formatNumber(equity, 4);
}

function renderPositions(
    positionPayload,
    algoPayload,
    protectionDataAvailable = true
) {
    const positions = unwrapArray(positionPayload).filter(
        (position) => Number(position?.size ?? 0) !== 0
    );

    const algos = protectionDataAvailable
        ? unwrapArray(algoPayload)
        : [];

    const rows = byId("positionRows");
    const empty = byId("positionEmpty");

    rows.innerHTML = "";

    byId("positionCount").textContent =
        String(positions.length);

    byId("positionBadge").textContent =
        `${positions.length} 筆持倉`;

    if (positions.length === 0) {
        empty.style.display = "block";
        empty.textContent = "目前沒有 OKX Demo 持倉。";

        byId("protectionNote").textContent =
            protectionDataAvailable
                ? "目前無持倉，不需保護單"
                : "保護單資料暫時無法讀取";

        byId("protectionNote").className =
            `card-note ${
                protectionDataAvailable ? "good" : "warn"
            }`;

        return;
    }

    empty.style.display = "none";

    let protectedCount = 0;

    for (const position of positions) {
        const raw = position.raw || {};

        const instrument =
            position.instrument_id ||
            position.instId ||
            "—";

        const protectedPosition =
            protectionDataAvailable &&
            algos.some(
                (order) =>
                    (
                        order.instrument_id ||
                        order.instId
                    ) === instrument
            );

        if (protectedPosition) {
            protectedCount += 1;
        }

        let direction =
            position.position_side ||
            position.posSide ||
            "net";

        const size = Number(position.size ?? 0);

        if (direction === "net") {
            direction = size >= 0
                ? "Long"
                : "Short";
        }

        const protectionText =
            !protectionDataAvailable
                ? "未知"
                : protectedPosition
                    ? "有保護"
                    : "無保護";

        const protectionTone =
            !protectionDataAvailable
                ? "warn"
                : protectedPosition
                    ? "good"
                    : "bad";

        const row = document.createElement("tr");

        row.innerHTML = `
            <td>${escapeHtml(instrument)}</td>
            <td>${escapeHtml(direction)}</td>
            <td>${formatNumber(position.size, 8)}</td>
            <td>${escapeHtml(position.margin_mode || "—")}</td>
            <td>${escapeHtml(position.leverage ?? "—")}x</td>
            <td>${formatNumber(position.average_price, 4)}</td>
            <td>${formatNumber(position.mark_price, 4)}</td>
            <td>${formatNumber(raw.imr, 6)}</td>
            <td>${formatNumber(position.unrealized_pnl, 6)}</td>
            <td>${formatPercent(raw.uplRatio)}</td>
            <td class="${protectionTone}">
                ${protectionText}
            </td>
            <td>${formatNumber(position.liquidation_price, 4)}</td>
        `;

        rows.appendChild(row);
    }

    if (!protectionDataAvailable) {
        byId("protectionNote").textContent =
            "保護單 API 讀取失敗，狀態顯示為未知";

        byId("protectionNote").className =
            "card-note warn";

        return;
    }

    const allProtected =
        protectedCount === positions.length;

    byId("protectionNote").textContent =
        `${protectedCount}/${positions.length} 筆有 Algo 保護`;

    byId("protectionNote").className =
        `card-note ${allProtected ? "good" : "bad"}`;
}

function renderPerformance(summaryPayload, validationPayload) {
    const combined = {
        summary: summaryPayload,
        validation: validationPayload
    };

    const reliability = deepFind(
        combined,
        [
            "reliability_ready",
            "reliabilityReady",
            "ready"
        ]
    );

    const activeDays = deepFind(
        combined,
        [
            "active_days",
            "activeDays"
        ]
    );

    const realizedTrades = deepFind(
        combined,
        [
            "realized_trades",
            "realizedTrades",
            "trade_count",
            "closed_trades"
        ]
    );

    const profitFactor = deepFind(
        combined,
        [
            "profit_factor",
            "profitFactor"
        ]
    );

    const maxDrawdown = deepFind(
        combined,
        [
            "max_drawdown_pct",
            "maxDrawdownPct",
            "max_drawdown"
        ]
    );

    const ready = reliability === true;

    byId("reliabilityReady").textContent =
        reliability === undefined
            ? "資料不足"
            : ready
                ? "Ready"
                : "Not Ready";

    byId("reliabilityReady").className =
        `card-value ${ready ? "good" : "warn"}`;

    byId("reliabilityNote").textContent =
        ready
            ? "已符合可靠性門檻"
            : "仍在收集有效交易資料";

    byId("activeDays").textContent =
        formatNumber(activeDays, 0);

    byId("realizedTrades").textContent =
        formatNumber(realizedTrades, 0);

    byId("profitFactor").textContent =
        formatNumber(profitFactor, 4);

    byId("maxDrawdown").textContent =
        maxDrawdown === undefined
            ? "—"
            : formatPercent(
                maxDrawdown,
                Number(maxDrawdown) > 1
            );

    byId("performanceBadge").textContent =
        ready ? "Reliability Ready" : "資料累積中";

    byId("performanceRaw").textContent =
        JSON.stringify(combined, null, 2);
}

function renderEvents(eventPayload) {
    const events = unwrapArray(eventPayload)
        .slice()
        .sort(
            (a, b) =>
                new Date(b.observed_at || 0) -
                new Date(a.observed_at || 0)
        )
        .slice(0, 12);

    const container = byId("events");
    container.innerHTML = "";

    byId("eventBadge").textContent =
        `${events.length} 筆最近事件`;

    if (events.length === 0) {
        container.innerHTML =
            '<div class="empty">沒有事件資料。</div>';
        return;
    }

    for (const event of events) {
        const severity =
            String(event.severity || "info").toLowerCase();

        const tone =
            severity === "critical" || severity === "error"
                ? "bad"
                : severity === "warning"
                    ? "warn"
                    : "good";

        const observedAt = event.observed_at
            ? new Date(event.observed_at).toLocaleString("zh-TW")
            : "—";

        const item = document.createElement("div");
        item.className = "event";

        item.innerHTML = `
            <div class="event-time">
                ${escapeHtml(observedAt)}
            </div>

            <div class="event-code ${tone}">
                ${escapeHtml(event.code || severity)}
            </div>

            <div>
                <div>
                    ${escapeHtml(event.message || "—")}
                </div>
                <div class="event-message">
                    ${escapeHtml(
                        event.details
                            ? JSON.stringify(event.details)
                            : ""
                    )}
                </div>
            </div>
        `;

        container.appendChild(item);
    }
}

function markBalanceUnavailable() {
    byId("equity").textContent = "讀取失敗";
    byId("equity").className = "card-value bad";
}

function markAutomationUnavailable() {
    byId("automationState").textContent = "讀取失敗";
    byId("automationState").className = "card-value bad";
    byId("automationNote").textContent =
        "Automation 狀態暫時無法取得";

    byId("safetyState").textContent = "未知";
    byId("safetyState").className = "card-value warn";
    byId("lockReasons").textContent =
        "安全狀態尚未確認";
}

function markPositionsUnavailable() {
    byId("positionRows").innerHTML = "";
    byId("positionCount").textContent = "—";
    byId("positionBadge").textContent = "讀取失敗";
    byId("positionEmpty").style.display = "block";
    byId("positionEmpty").textContent =
        "持倉 API 暫時無法取得。";

    byId("protectionNote").textContent =
        "持倉與保護狀態尚未確認";

    byId("protectionNote").className =
        "card-note warn";
}

function markPerformanceUnavailable(error) {
    byId("reliabilityReady").textContent = "讀取失敗";
    byId("reliabilityReady").className =
        "card-value warn";

    byId("reliabilityNote").textContent =
        "績效資料暫時無法取得";

    byId("performanceBadge").textContent =
        "讀取失敗";

    byId("performanceRaw").textContent =
        error || "績效 API 暫時無法取得";
}

function markEventsUnavailable() {
    byId("eventBadge").textContent = "讀取失敗";

    byId("events").innerHTML =
        '<div class="empty">監控事件暫時無法取得。</div>';
}

function formatDuration(milliseconds) {
    if (!Number.isFinite(milliseconds)) {
        return "—";
    }

    if (milliseconds < 1000) {
        return `${Math.round(milliseconds)}ms`;
    }

    return `${(milliseconds / 1000).toFixed(1)}秒`;
}

function renderDataIntegrityStatus() {
    const stateElement = byId("dataIntegrityState");
    const noteElement = byId("dataIntegrityNote");

    stateElement.className = "card-value";

    if (lastContractError) {
        stateElement.textContent = "Contract Error";
        stateElement.classList.add("bad");

        noteElement.textContent =
            `?????${lastContractError}`;

        return;
    }

    if (!lastSuccessfulRefreshAt) {
        stateElement.textContent = "尚未更新";
        stateElement.classList.add("warn");
        noteElement.textContent = "等待第一次成功快照";
        return;
    }

    const ageMs = Math.max(
        0,
        Date.now() - lastSuccessfulRefreshAt
    );

    let label = "Fresh";
    let tone = "good";

    if (
        lastCycleSpreadMs !== null &&
        lastCycleSpreadMs > DATA_CONSISTENCY_WINDOW_MS
    ) {
        label = "Time Drift";
        tone = "warn";
    }

    if (lastCycleFailureCount > 0) {
        label = "Partial";
        tone = "warn";
    }

    if (ageMs > DATA_STALE_AFTER_MS) {
        label = "Stale";
        tone = "bad";
    }

    stateElement.textContent = label;
    stateElement.classList.add(tone);

    const updatedAt =
        new Date(
            lastSuccessfulRefreshAt
        ).toLocaleString("zh-TW");

    const spreadText =
        lastCycleSpreadMs === null
            ? "—"
            : formatDuration(lastCycleSpreadMs);

    noteElement.textContent =
        `最後快照：${updatedAt} · ` +
        `資料年齡：${formatDuration(ageMs)} · ` +
        `成功來源 ${lastCycleSuccessCount} · ` +
        `失敗來源 ${lastCycleFailureCount} · ` +
        `來源時間差 ${spreadText}`;
}


class SnapshotContractError extends Error {
    constructor(code) {
        super(`Snapshot ???????${code}`);

        this.name = "SnapshotContractError";
        this.code = code;
    }
}

function failSnapshotContract(code) {
    throw new SnapshotContractError(code);
}

function isPlainRecord(value) {
    return (
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value)
    );
}

function hasTimezoneSuffix(value) {
    return (
        typeof value === "string" &&
        /(Z|[+-]\d{2}:\d{2})$/.test(value)
    );
}

function validateSnapshotContract(snapshot) {
    if (!isPlainRecord(snapshot)) {
        failSnapshotContract(
            "snapshot_not_object"
        );
    }

    if (
        snapshot.contract_version !==
        SUPPORTED_SNAPSHOT_CONTRACT_VERSION
    ) {
        failSnapshotContract(
            "unsupported_contract_version"
        );
    }

    const snapshotIdPattern =
        /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

    if (
        typeof snapshot.snapshot_id !== "string" ||
        !snapshotIdPattern.test(
            snapshot.snapshot_id
        )
    ) {
        failSnapshotContract(
            "invalid_snapshot_id"
        );
    }

    if (!hasTimezoneSuffix(snapshot.generated_at)) {
        failSnapshotContract(
            "generated_at_timezone_missing"
        );
    }

    const generatedAtMs =
        Date.parse(snapshot.generated_at);

    if (!Number.isFinite(generatedAtMs)) {
        failSnapshotContract(
            "invalid_generated_at"
        );
    }

    if (
        generatedAtMs >
        Date.now() + MAX_SNAPSHOT_FUTURE_SKEW_MS
    ) {
        failSnapshotContract(
            "generated_at_too_far_in_future"
        );
    }

    const snapshotDuration =
        Number(snapshot.duration_ms);

    if (
        !Number.isFinite(snapshotDuration) ||
        snapshotDuration < 0
    ) {
        failSnapshotContract(
            "invalid_snapshot_duration"
        );
    }

    if (typeof snapshot.complete !== "boolean") {
        failSnapshotContract(
            "invalid_complete_flag"
        );
    }

    if (!isPlainRecord(snapshot.source_status)) {
        failSnapshotContract(
            "source_status_not_object"
        );
    }

    const actualSources =
        Object.keys(
            snapshot.source_status
        ).sort();

    const expectedSources =
        [...EXPECTED_SNAPSHOT_SOURCES].sort();

    if (
        actualSources.length !==
        expectedSources.length
    ) {
        failSnapshotContract(
            "source_set_mismatch"
        );
    }

    for (
        let index = 0;
        index < expectedSources.length;
        index += 1
    ) {
        if (
            actualSources[index] !==
            expectedSources[index]
        ) {
            failSnapshotContract(
                "source_set_mismatch"
            );
        }
    }

    for (
        const sourceName
        of EXPECTED_SNAPSHOT_SOURCES
    ) {
        const status =
            snapshot.source_status[sourceName];

        if (!isPlainRecord(status)) {
            failSnapshotContract(
                `invalid_source_status:${sourceName}`
            );
        }

        if (typeof status.ok !== "boolean") {
            failSnapshotContract(
                `invalid_source_ok:${sourceName}`
            );
        }

        const durationMs =
            Number(status.duration_ms);

        if (
            !Number.isFinite(durationMs) ||
            durationMs < 0
        ) {
            failSnapshotContract(
                `invalid_source_duration:${sourceName}`
            );
        }

        if (
            typeof status.timed_out !==
            "boolean"
        ) {
            failSnapshotContract(
                `invalid_source_timeout_flag:${sourceName}`
            );
        }

        if (
            !hasTimezoneSuffix(status.started_at) ||
            !hasTimezoneSuffix(status.completed_at)
        ) {
            failSnapshotContract(
                `source_timezone_missing:${sourceName}`
            );
        }

        const startedAtMs =
            Date.parse(status.started_at);

        const completedAtMs =
            Date.parse(status.completed_at);

        if (
            !Number.isFinite(startedAtMs) ||
            !Number.isFinite(completedAtMs)
        ) {
            failSnapshotContract(
                `invalid_source_timestamp:${sourceName}`
            );
        }

        if (completedAtMs < startedAtMs) {
            failSnapshotContract(
                `source_completed_before_started:${sourceName}`
            );
        }

        if (
            completedAtMs >
            generatedAtMs +
            MAX_SNAPSHOT_FUTURE_SKEW_MS
        ) {
            failSnapshotContract(
                `source_completed_after_snapshot:${sourceName}`
            );
        }

        if (status.ok) {
            if (status.timed_out) {
                failSnapshotContract(
                    `successful_source_timed_out:${sourceName}`
                );
            }

            if (
                status.error_code !== null &&
                status.error_code !== undefined
            ) {
                failSnapshotContract(
                    `successful_source_has_error:${sourceName}`
                );
            }
        }

        if (!status.ok) {
            if (
                typeof status.error_code !== "string" ||
                !status.error_code.trim()
            ) {
                failSnapshotContract(
                    `failed_source_missing_error:${sourceName}`
                );
            }
        }

        if (
            status.timed_out &&
            status.error_code !== "source_timeout"
        ) {
            failSnapshotContract(
                `invalid_timeout_error_code:${sourceName}`
            );
        }
    }

    const calculatedComplete =
        EXPECTED_SNAPSHOT_SOURCES.every(
            (sourceName) =>
                snapshot.source_status[
                    sourceName
                ].ok === true
        );

    if (
        snapshot.complete !==
        calculatedComplete
    ) {
        failSnapshotContract(
            "complete_source_status_mismatch"
        );
    }

    const scalarSources = [
        "balance",
        "automation",
        "performance",
        "validation"
    ];

    for (const sourceName of scalarSources) {
        const status =
            snapshot.source_status[sourceName];

        const value = snapshot[sourceName];

        if (
            status.ok &&
            !isPlainRecord(value)
        ) {
            failSnapshotContract(
                `successful_source_missing_value:${sourceName}`
            );
        }

        if (
            !status.ok &&
            value !== null
        ) {
            failSnapshotContract(
                `failed_source_contains_value:${sourceName}`
            );
        }
    }

    const listSources = [
        "positions",
        "algo_orders",
        "events"
    ];

    for (const sourceName of listSources) {
        const status =
            snapshot.source_status[sourceName];

        const value = snapshot[sourceName];

        if (!Array.isArray(value)) {
            failSnapshotContract(
                `list_source_not_array:${sourceName}`
            );
        }

        if (
            !status.ok &&
            value.length > 0
        ) {
            failSnapshotContract(
                `failed_source_contains_items:${sourceName}`
            );
        }
    }

    return snapshot;
}

function sourceSucceeded(snapshot, sourceName) {
    return (
        snapshot?.source_status?.[sourceName]?.ok === true
    );
}

function sourceErrorCode(snapshot, sourceName) {
    return (
        snapshot?.source_status?.[sourceName]?.error_code ||
        "source_unavailable"
    );
}

function updateDataIntegrityFromSnapshot(snapshot) {
    const statuses = Object.values(
        snapshot?.source_status || {}
    );

    const successfulStatuses = statuses.filter(
        (status) => status?.ok === true
    );

    const failedStatuses = statuses.filter(
        (status) => status?.ok !== true
    );

    const durations = statuses
        .map(
            (status) => Number(status?.duration_ms)
        )
        .filter(Number.isFinite);

    lastCycleSuccessCount = successfulStatuses.length;
    lastCycleFailureCount = failedStatuses.length;

    if (durations.length > 0) {
        lastCycleSpreadMs =
            Math.max(...durations) -
            Math.min(...durations);
    } else {
        lastCycleSpreadMs = null;
    }

    const generatedAt = Date.parse(
        snapshot?.generated_at || ""
    );

    lastSuccessfulRefreshAt =
        Number.isFinite(generatedAt)
            ? generatedAt
            : Date.now();

    renderDataIntegrityStatus();
}

async function refreshAll() {
    if (refreshInProgress) {
        return;
    }

    refreshInProgress = true;

    const cycleId = ++refreshCycleId;

    clearNotice();

    byId("refreshButton").disabled = true;
    byId("refreshButton").textContent = "更新中…";

    try {
        const token = requireToken();

        const snapshot = await apiGet(
            "/api/dashboard/snapshot",
            token
        );

        validateSnapshotContract(snapshot);
        lastContractError = null;

        if (cycleId < latestRenderedCycleId) {
            console.warn(
                "忽略較舊的 Snapshot 更新週期：",
                cycleId
            );

            return;
        }

        latestRenderedCycleId = cycleId;

        const failures = [];

        if (
            sourceSucceeded(snapshot, "balance") &&
            snapshot.balance
        ) {
            renderBalance(snapshot.balance);
        } else {
            markBalanceUnavailable();

            failures.push(
                `balance:${
                    sourceErrorCode(snapshot, "balance")
                }`
            );
        }

        if (
            sourceSucceeded(snapshot, "automation") &&
            snapshot.automation
        ) {
            renderAutomation(snapshot.automation);
        } else {
            markAutomationUnavailable();

            failures.push(
                `automation:${
                    sourceErrorCode(snapshot, "automation")
                }`
            );
        }

        if (
            sourceSucceeded(snapshot, "positions")
        ) {
            const algoAvailable =
                sourceSucceeded(
                    snapshot,
                    "algo_orders"
                );

            renderPositions(
                snapshot.positions || [],
                snapshot.algo_orders || [],
                algoAvailable
            );

            if (!algoAvailable) {
                failures.push(
                    `algo_orders:${
                        sourceErrorCode(
                            snapshot,
                            "algo_orders"
                        )
                    }`
                );
            }
        } else {
            markPositionsUnavailable();

            failures.push(
                `positions:${
                    sourceErrorCode(snapshot, "positions")
                }`
            );
        }

        const performanceAvailable =
            sourceSucceeded(
                snapshot,
                "performance"
            );

        const validationAvailable =
            sourceSucceeded(
                snapshot,
                "validation"
            );

        if (
            performanceAvailable ||
            validationAvailable
        ) {
            renderPerformance(
                performanceAvailable
                    ? snapshot.performance
                    : {},
                validationAvailable
                    ? snapshot.validation
                    : {}
            );

            if (!performanceAvailable) {
                failures.push(
                    `performance:${
                        sourceErrorCode(
                            snapshot,
                            "performance"
                        )
                    }`
                );
            }

            if (!validationAvailable) {
                failures.push(
                    `validation:${
                        sourceErrorCode(
                            snapshot,
                            "validation"
                        )
                    }`
                );
            }
        } else {
            markPerformanceUnavailable(
                "Performance 與 Validation 來源皆失敗"
            );

            failures.push(
                `performance:${
                    sourceErrorCode(
                        snapshot,
                        "performance"
                    )
                }`
            );

            failures.push(
                `validation:${
                    sourceErrorCode(
                        snapshot,
                        "validation"
                    )
                }`
            );
        }

        if (
            sourceSucceeded(snapshot, "events")
        ) {
            renderEvents(snapshot.events || []);
        } else {
            markEventsUnavailable();

            failures.push(
                `events:${
                    sourceErrorCode(snapshot, "events")
                }`
            );
        }

        updateDataIntegrityFromSnapshot(snapshot);

        const snapshotId =
            snapshot.snapshot_id || "unknown";

        const backendDuration =
            formatDuration(
                Number(snapshot.duration_ms)
            );

        if (
            snapshot.complete === true &&
            failures.length === 0
        ) {
            showNotice(
                `快照已更新 · ID ${snapshotId} · ` +
                `後端耗時 ${backendDuration}`,
                "success"
            );
        } else {
            showNotice(
                `快照已更新，但有 ${
                    failures.length
                } 個來源異常 · ID ${snapshotId}`,
                "error"
            );

            console.warn(
                "Snapshot partial failures:",
                failures
            );
        }
    } catch (error) {
        console.error(error);

        if (
            error?.name ===
            "SnapshotContractError"
        ) {
            lastContractError =
                error.code ||
                "unknown_contract_error";
        } else {
            lastContractError = null;
        }

        lastCycleSuccessCount = 0;
        lastCycleFailureCount = 1;
        lastCycleSpreadMs = null;

        renderDataIntegrityStatus();

        showNotice(
            error.message || String(error),
            "error"
        );
    } finally {
        refreshInProgress = false;

        byId("refreshButton").disabled = false;
        byId("refreshButton").textContent = "立即更新";
    }
}

function setToken() {
    const entered = window.prompt(
        "輸入新的 CTCC API_TOKEN。\nToken 不會寫入伺服器或硬碟。"
    );

    if (!entered || !entered.trim()) {
        return;
    }

    sessionStorage.setItem(TOKEN_KEY, entered.trim());
    refreshAll();
}

function toggleAutoRefresh() {
    autoRefresh = !autoRefresh;

    byId("autoButton").textContent =
        autoRefresh
            ? "停止自動更新"
            : "啟用自動更新";

    if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
    }

    if (autoRefresh) {
        refreshTimer = setInterval(refreshAll, 30000);
    }
}

byId("tokenButton").addEventListener("click", setToken);
byId("refreshButton").addEventListener("click", refreshAll);
byId("autoButton").addEventListener("click", toggleAutoRefresh);

refreshTimer = setInterval(refreshAll, 30000);

if (sessionStorage.getItem(TOKEN_KEY)) {
    refreshAll();
} else {
    showNotice(
        "請先按「設定 API Token」，再載入儀表板資料。",
        "warn"
    );
}
</script>
</body>
</html>
"""
