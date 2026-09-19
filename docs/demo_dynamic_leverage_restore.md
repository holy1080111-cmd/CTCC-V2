# 動態槓桿恢復：使用者已選定，部署尚未切換

2026-09-12 使用者明確選擇恢復 **3／5／8／10／20 倍動態上限**，不是僅恢复
1／2／3 分級。這是依條件使用的上限階梯，不是所有部位強制使用 20 倍。

[保守 overlay 範例](demo_dynamic_leverage_conservative.env.example) 已準備。
它不是 active `.env`，沒有 arming／write／Live 權限或憑證。五級 structural
risk 均為 0.005（每筆 0.5%），portfolio stop-risk 維持 0.01（1%）；不恢復舊
1.5%–6% structural 風險或 10% portfolio 限額。現有 300 USDT margin bucket、
60% portfolio margin 設定、SL／TP、成本、淨 RR 和 20x 品質門檻不放寬。
2026-09-19 開發增量已把既有 `OKX_DEMO_PORTFOLIO_MAX_MARGIN_PCT` 硬閘套用到
bucket 分支，60% 設定不再被略過。槓桿提高仍可能用滿原本未用完的風險預算，
並非實際損失不變的保證。這是未部署的程式變更，不代表現有容器已切換。

## Aggregate margin hard gate

初始 sizing 同時取單筆 bucket、可用 USDT 與 aggregate 剩餘 margin 的上限。
aggregate 使用同一筆 reconciled USDT risk equity 與已追蹤金額、同次 run 的
預留金額計算；不累加以不同舊 equity 算出的百分比。合約 lot rounding 後再次
驗總額，恰等於 cap 可接受，超過或額度耗盡不得新增曝險。已存在的超限部位
觸發 EStop，不因部署而自動平倉或取消保護。

設定槓桿的 await 後重新 reconciliation，檢查 equity basis、可用餘額及未追蹤
曝險；ordinary pending 無法明確排除額外 margin 時拒絕。既定 contracts、entry、
SL、TP 均不為通過此重查而修改。最後 `before_submit` 同步邊界重新讀目前 cap
與本地持倉／uncertain 記錄，保留先前已知預留與較大新金額；cap 只可收緊，
改設定或 await 期間加入新曝險不能繞過。缺損／衝突本地 inventory 保持拒絕。

`test_demo_aggregate_margin.py` 覆蓋兩筆 300 USDT／1,000 USDT equity 恰達 60%、
第三筆拒絕、無關資產不擴大 USDT basis、晚期 equity／available balance 下降、
cap 收緊、新本地曝險與停用 score-risk flag 的繞過嘗試。這是既有 automation
margin 邊界的測試；可信完整帳戶 R5、原子 R6／R7 與新資格入口仍須各自驗收。

實際執行中的容器以獨立 subprocess 只建立 proposed Settings，已確認驗證通過；
與原 Settings 真正不同的僅七欄：max leverage、structural enable、五級 risk。
既有五級 cap defaults 本來就是 3／5／8／10／20，範例明確釘住它們。
legacy 1／1／1 與 automation 1 設定可以保留：structural 分支不採用它們。
此驗證未修改服務記憶體、磁碟 `.env`、容器、倉位或權限。

## 目前切換限制

只讀查詢確認原部署 `d3f206a59888ef3d72732fa30deaa8278ac72cc5` 仍 armed／running，
2026-09-12 06:36 UTC 前後有四筆已追蹤跨倉／ATR 部位，沒有觀測到 emergency／lock。
這不是完整私有帳戶分頁認證，也不能由追蹤數推論「沒有其他曝險」。

重要區別：既有 guard 只對 structural trade 要求 isolated；不能聲稱切換
全域 flag 會自動封鎖每筆旧 cross／ATR 倉。真正明确的重啟邊界是
`SafeDemoAutomation.recover()` 主動解除 armed，而 `arm()` 要求 exchange positions、
pending orders、pending algos 及 tracked trades 全部為空。不能直接恢復 persisted
armed、豁免這項檢查，或為了切換擅自平倉／調整既有槓桿／重寫保護。

因此此 overlay 目前僅是經驗證的**待套用設定**。未重啟原服務，沒有宣稱動態
模式已在運行，也不承諾未排程的背景自動切換。

最小 backport 已另存並推送為
[`eeec57dd97f6c528a31d184ef96e35cfe5188558`](https://github.com/holy1080111-cmd/CTCC-V2/commit/eeec57dd97f6c528a31d184ef96e35cfe5188558)，
分支 `develop/demo-dynamic-leverage-restore`，直接基於現有部署 `d3f206a`。
只包含 helper 的 None-only 修補、保守設定範例、rollout 文件、回歸測試與 manifest，
不混入新 qualification 管線。工作樹相關測試 187 項通過（42 新增、145 既有）；
不可變來源、CI 結果另記，測試通過並不代表已套用或恢復 armed。

## 安全套用順序

1. 在沒有 in-flight submit／不確定結果的邊界停止新進場；先保留原服務與
   account／order／protection 記錄，不取消保護、不清空帳本。
2. 以完整帳戶查核與本地 uncertain／reservation 聯集確認空倉、無 pending／
   algo／tracked／uncertain 曝險；維持原保護直到既有倉位自然或明確授權處理完畢。
3. 使用上述已移植 `risk_score is None` 的最小部署版本，完成其不可變來源驗收，
   不直接把整個未完成 qualification 分支部署。舊 `_twenty_x_reasons` 的 `risk_score or score` 是獨立
   downward-gate 缺陷；正常 `_risk_profile` 的零分會落到 low cap3，而非 20x，
   不宣稱已證實實際20x誤單。
4. 驗証完整提案 Settings、保存可回復配置、只改已選定槓桿／風險七欄與明示 caps；
   用已驗來源切換，確認新 runtime schema／服務健康／Live false。
5. 重新做原有 recovery／flat／arming 檢查；驗證 API 回傳五級上限、0.5%／1%
   risk limits、所有保護與舊狀態一致。設定成功不等於已授權略過新進場資格。

本文件是 rollout 計劃與狀態，不是新流程完整 Demo 驗收。
# 2026-09-19 aggregate-margin review addendum

The final automation margin callback rejects missing or zero local hold amounts and
retains the larger captured/current hold. Its fresh post-leverage account check
requires exact tracked contracts, side, leverage and margin mode plus matching
active protection; unknown ordinary/algo exposure blocks. A mismatch or tightened
final cap latches EStop without automatically closing exposure.

[OKX positions](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-positions),
reviewed 2026-09-19, defines cross `imr` in USD and isolated `margin` in `ccy`.
The guard requires the same raw row's positive `usdPx` for a currency conversion;
it never treats USD and USDT as equal or fills missing margin/FX with zero.
This legacy guard is separate from still-required trusted account-source,
qualification, durable reservation and intent acceptance.
