# R5 Demo 帳戶來源計畫

狀態：**僅設計／未實作，2026-09-12。不是帳戶 collector 驗收，也不是 R5、R6 或 R7 完成。**

本文件只依本機程式契約與 OKX 官方文件盤點；沒有讀取憑證或帳戶資料、呼叫 private API、開啟寫入、建立風險保留或下單。API 名稱均為提案，尚不可 import。公開行情增量見 [公開資料收集](public_market_capture.md)；主進度見 [Execution Recheck 計畫](qualification_recheck_plan.md)。

## 1. 本輪可做的最小下一步

先建立 **Demo-only 原始回應與頁鏈契約**，用合成 bytes／MockTransport 驗證；不要直接把舊 mirror 轉成 `PortfolioRiskSnapshot`。

提議分三個明確邊界，依序驗收：

1. `DemoAccountCapturePlan`、`DemoAccountObservation`、`DemoAccountPacket`：strict/frozen、原始 bytes、固定 GET 範圍、同帳戶綁定、逐頁時間與完整性缺口；不可帶 credential 值。
2. `collect_demo_account_records(*, reader, clock, plan, expected_plan_sha256, barrier_completed_at)` 與 `verify_demo_account_records(...)`：前者未來才接經審查的 Demo read-only transport；後者重驗 plan pin、raw bytes、derived identity／query／頁鏈。成功只表示完成所要求的**擷取**，不是完整風控證據。
3. `materialize_demo_portfolio_snapshot(packet, *, identity_pin, ledger_checkpoint, instrument_specs, correlation_registry, protection_evidence, policy)`：必須有下文全部證據才可產生既有 `PortfolioRiskSnapshot`；否則回 immutable incomplete result 與具體原因。不得猜零值或偷偷放寬既有風控 evaluator。

每層 `execution_authority=False`；packet 的 raw/hash 自洽不等於 `source_authenticity_verified=True`。尚缺 trusted transport、完整性策略、持久帳本及實際來源驗收，故本文件不承諾第三層已可產出 complete snapshot。

## 2. 同帳戶與環境識別

`GET /api/v5/account/config` 的 `uid` 是本次請求帳戶，`mainUid` 是主帳戶識別；**不能只用 mainUid 合併兄弟子帳戶**。保留 `acctLv`、`posMode` 原值，未知或缺欄不得套用 `net_mode`。來源：[account configuration](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-account-configuration)。

未來 capture plan 必須外部 pin `environment='demo'`、`expected_uid`、預期主帳戶關係、結算／風險幣別及查詢範圍。Demo key 配合固定 `x-simulated-trading: 1`，不可 fallback Live；單有 header 不能證明所有回應屬於預期帳戶。[OKX API FAQ](https://www.okx.com/en-us/help/api-faq)

同一次 batch 使用不可中途切換的 signer／credential handle。可記非秘密的 session binding ID，不記 key、secret、passphrase、signature 或完整認證 request headers。config 原始 receipt 的 pin 必須出現在每一帳戶來源 receipt 的 binding 中；其他 endpoint 沒有回傳 uid 時，清楚標為「同一受控請求上下文綁定」，不得捏造 response uid。

前後 config 比對可發現帳戶／模式切換，但 **不是交易所全域 revision**，也不能證明期間沒有成交。環境＋uid 必須同時進入所有 ledger、peak、history seed、reservation 的 scope；不能以 singleton DB id、symbol、report_id 或 clOrdId 代替。

## 3. 必要 raw 來源與頁鏈

下表是待實作來源清單，不是已呼叫或已認證的端點。

| 用途 | 固定 read endpoint／必要保留內容 | 完整性邊界 |
| --- | --- | --- |
| 身分／模式 | `/api/v5/account/config`；uid、mainUid、acctLv、posMode、原始回應 | 只能綁定帳戶與模式，不能當帳本 revision。 |
| 同時點 anchor | `/api/v5/account/account-position-risk`；ts、balData、posData | 官方的同時點範圍是 accounts＋positions；不是 orders/history/local ledger 快照。 |
| 權益／可用保證金 | `/api/v5/account/balance`；頂層與各幣別 details、uTime | 保留幣別與更新時間；缺值不補 0，不把 USD 數字改標 USDT。 |
| 全部部位 | `/api/v5/account/positions`；posId、instId、posSide、pos、ccy、mgnMode、mark／margin 欄、cTime/uTime | 初版需全 scope inventory；不能僅查候選 symbol 後稱 account-wide。 |
| 普通未完成單 | `/api/v5/trade/orders-pending`；ordId、clOrdId、state、sz、accFillSz、side／posSide、時間 | 逐頁 ordId cursor；不得只取第一頁。 |
| 未觸發 algo 單 | `/api/v5/trade/orders-algo-pending`；algoId、algoClOrdId、ordType、完整保護與數量欄 | 分別收集 conditional、oco、trigger、move_order_stop 的 algoId 頁鏈；若不能覆蓋其他活動產品／機制，明示 incomplete。 |
| 成交／已結算變動 | `/api/v5/trade/fills-history`、`/api/v5/account/bills-archive`，視保留期補相對應近端 endpoint | 以 billId 頁鏈保存原始事件；損益、費用、funding 與非交易資金變動必須明確分類。 |
| 訂單對帳輔助 | `/api/v5/trade/orders-history`、`orders-history-archive`，必要時 exact order/algo detail | 不是完整已實現損益來源；不得用訂單建立時間代替成交／關倉時間。 |
| 合約／數量單位 | 經審查的 instruments 原始來源，必要時 account instruments／leverage-info | 所有已持有與待成交 instrument 都需規格；公開 ceiling 不等於帳戶當下可用額度。 |

同時點 anchor 可優先實作，但缺 `available_margin`、掛單、歷史、本機 pending、peak 等所需證據，**單獨不足以 materialize**。[Account and position risk](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-account-and-position-risk)

普通與 algo 訂單頁鏈分別使用其 endpoint 的識別碼，不能混用。Algo 的四類分開查，不能把既有 `conditional,oco` 呼叫視作已覆蓋全部類型。[Order list](https://app.okx.com/docs-v5/en/#order-book-trading-trade-get-order-list)、[Algo order list](https://app.okx.com/docs-v5/en/#order-book-trading-algo-trading-get-algo-order-list)

### 頁鏈與 history 規則

以下是 collector 的待驗證不變條件，不是把外部 ID 當連號：

- 每個 receipt 固定 endpoint、query、scope、page index、前頁 hash、cursor 欄名／值；下一頁 cursor 必須從前頁實際邊界推導。cursor 重複、不前進、頁內／跨頁衝突、達 row/page/byte 上限卻未完成、保留期不足均不能 complete。
- OKX `after/before` 排除 cursor，`begin/end` 包含時間邊界；窗口重疊須以原始識別碼精確去重，不能把衝突 row 丟掉。每個 endpoint 的終止條件需明定與測試，不能僅因收到一頁或筆數少就宣稱全歷史完整。[官方分頁規則](https://app.okx.com/docs-v5/trick_en/#pagination)
- Order history 的 begin/end 篩 cTime；舊建單但本週成交的 closing order 不能被排除。完全未成交取消單的保留限制，也不能當作「local uncertain 已不存在」的證明。[Order history](https://app.okx.com/docs-v5/en/#order-book-trading-trade-get-order-history-last-7-days)
- Fills-history 的 cursor 是 billId；保留生成時間 ts 與成交時間 fillTime，兩者不可換名。Bills 的餘額事件不能全當交易損益，也不得與 fills 費用／PnL 重複加總。[Fills history](https://app.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-months)、[Bills archive](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-3-months)
- `RealizedOutcome.sequence` 是經完整重建後的**本機帳本序號**，不是聲稱 billId／tradeId 連續。`outcome_id` 綁 environment＋uid＋原始事件集合；需明定部分平倉與一次 outcome 的分组及費用歸屬，避免一個交易拆成多次連敗或把 funding 事件當勝負結果。[官方對帳說明](https://app.okx.com/docs-v5/trick_en/#reconciliation-between-fill-and-positions)
- 必須獨立證明 history 起點前的連敗 seed、完整 UTC 日與 rolling seven-day 覆蓋。查詢範圍／最後一筆時間不等於 ingestion watermark；延遲事件與窗口回補的規則未驗證時，只能標 queried window，不能聲稱 verified history end。

## 4. 時鐘與原始來源契約

`DemoAccountObservation` 提議保留 method／固定 origin／endpoint／canonical query、identity binding、raw response bytes／size／SHA、canonical JSON／SHA、request_started_at／headers_received_at／body_completed_at、可選原始 source timestamps 及其語意，另含頁鏈定位。不要把認證 request timestamp 當 response 的資料時間。

每次必要 GET 都必須在本次 publication barrier 之後開始；UTC aware clock、單調請求次序、bounded request/batch deadline、有限 raw bytes/rows/pages；取消後清理，無隱藏重試／redirect／proxy。參數與 DTO 在任何 serializer、signer 或 I/O 前先檢查 exact 型別、欄位與大小。這些是沿用公開 capture 已驗的工程模式，不是批准 private I/O。

Balance uTime 與 position uTime 分別描述來源更新，後者可長期不變；config 或空 positions 回應也未必有可用的來源 timestamp。不得用 now、GET 完成時間或 max(row.uTime) 偽造更新。原始欄位語意見 [balance](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-balance)、[positions](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-positions)。

沒有 source clock 的回應要標明缺少與 receipt-only；只有受審查的多來源 anchor／對帳程序才能建立相應風控 stamp，不能私自將 receipt time 寫入 `ObservedSource.observed_at`。同時點 anchor 以外的讀取沒有文件化的共同 snapshot/revision token；兩次資料相同也不足以證明中間沒有成交或餘額變化。

## 5. 既有 exact fields 的 materialization 對照

真實類別名稱是 [`ContractRiskSpec`](../app/trade_qualification/portfolio.py)，不是新增平行的 InstrumentRiskSpec。下列映射都**尚未實作**。

| 既有欄位 | 所需來源／不得默認的部分 |
| --- | --- |
| `ObservedSource.source_sha256, observed_at, received_at` | 指向可重播的完整原始來源集合／實際時鐘依據；hash 不能自行證明完整。 |
| `EvidenceStamp.environment, account_id, complete` | 必須 demo＋exact uid，承接上述共同欄位；complete 由完整性程序派生，不能從 caller 布林、HTTP 200 或 persisted=True 取得。 |
| `ContractRiskSpec.instrument_id, base_currency, settlement_currency, contract_kind, contract_value_currency, contract_value` | instrument 原始規格與明確單位映射；只接受現 evaluator 支援的 linear_base。ctMult、inverse、幣別缺漏或衝突不得猜轉換。 |
| `ContractRiskSpec.lot_size, min_contracts, max_contracts, max_leverage, correlation_group` | 對應 lotSz／minSz／訂單類型適用上限／leverage ceiling 的明確規則；correlation 是另行審核、有版本的分類，不是 exchange 提供的值。每個 instrument 只能有一個一致分類。 |
| `PositionExposure.position_id, instrument_id, direction, settlement_currency` | 保留實際 posId 與 posSide／signed net pos；驗模式後才推 long／short，缺欄不默認。不能僅用 symbol 合併 hedge mode 兩側。 |
| `PositionExposure.notional, margin, risk_amount, correlation_group` | 完整數量與規格、明確幣別、當前價格／保護來源和成本；USD notional 不冒充 USDT，無 SL 或 unknown margin 不補 0。無法表示的部位／保證金狀態須阻擋 materialization。 |
| `PendingReservation.reservation_id, instrument_id, direction, settlement_currency, notional, margin, risk_amount, correlation_group` | exchange 未成交餘量＋local in-flight/uncertain 聯集；以明確 ID 關係和狀態機去重，不把整筆 sz 與已入 positions 的成交部分重算，也不得丟掉可能曝險。 |
| `RealizedOutcome.outcome_id, sequence, instrument_id, closed_at, realized_pnl` | 原始 fills/bills 與持久分組／對帳；closed_at 採實際事件語意，net PnL 的幣別／費用歸屬可重播。 |
| `PortfolioRiskSnapshot.account_id, settlement_currency, balance_stamp, positions_stamp, history_stamp, reservations_stamp` | 全部同 environment/uid/風險幣別。各 stamp 獨立保留來源與 freshness，不覆蓋成 batch completed_at。 |
| `equity, available_margin` | 由帳戶模式和結算幣別選定的真值；初版建議只支援已明確映射的單結算幣線性合約範圍。其他資產／負債／mode 未能納入時不能稱 account-wide。 |
| `peak_equity, peak_observed_at, peak_window_started_at` | 同帳戶／同幣別且對應 policy drawdown window 的持久峰值來源；不能把第一次讀到的 equity 宣稱全窗口 peak。 |
| `history_start, history_end, loss_streak_at_history_start` | 起點前可信 seed＋完整事件覆蓋。既有 evaluator 要求 history_end 等於 history stamp observed_at，窗口含當日 UTC 與過去七日；不能以跨日重設 seed。 |
| `positions, pending_reservations, loss_history, position_count, pending_reservation_count` | 所有範圍內集合＋實際長度；空 tuple 只表示已證明為空，未知必須 incomplete；每種集合最多 2048，超過不得截斷。 |
| `DemoRiskAuthority.stamp, environment, demo_enabled, armed, order_writes_allowed, simulated_trading_header, emergency_stop, live_trading, live_order_writes, live_auto_execution` | 來自獨立本機 guard 的受控快照，不是 account/config 的推測值；collector 不設置／開啟任何 guard，也不產生寫入許可。 |
| [`PortfolioInputs`](../app/trade_qualification/engine.py) `requested_contracts, requested_leverage, instrument, account, authority` | collector 不改原數量／槓桿；缺 instrument/account/authority 必須明確 None 或返回 incomplete，交由既有 first-failure 流程拒絕。 |

## 6. Local ledger 與不得 complete 的情況

Local checkpoint 至少以 `(environment, uid, settlement_currency)` 分域，保存 revision、全部 in-flight/uncertain、原始 intent/order/algo 關係、history watermark 與 seed、peak window 與來源 pin。**這不是要求交易所 ID 連號，也不是已實作 R6 原子預留。**

以下任何一項存在都不得產生 complete 風控 snapshot：

- 身分／環境／模式切換、部分回應來自其他 uid、同 mainUid 卻不同子帳戶；回應 scope 不涵蓋所有會影響風控的產品／幣別。
- 缺頁、重複／衝突事件、未證明終止、超保留期／byte/row/page budget、未知空集合、失敗後以舊 mirror 替代。
- 掛單與 positions 在讀取中轉移、cancel/timeout 狀態不確定、ledger revision 改變、找不到 local reservation 或尚無去重證據；不能因 TTL 到期、單頁未見訂單或舊取消紀錄不再可查就釋放可能曝險。
- 缺 price／margin／stop／成本／correlation、不支援合約或無法表示的資金狀態；不能自行改小 requested size、提高 leverage ceiling 或將 unknown risk 設 0。
- 缺實際 history coverage、連敗 seed 或 peak 覆蓋；不能從只有本策略的結束紀錄推論整個帳戶，不能把 transfer/deposit 當交易獲利重設連敗。
- 來源時鐘缺失或無可審核對帳依據、跨來源狀態無法一致、資料過期／publication barrier 不符。

即使上述 scoped evidence 完成，也只可進純 G11／current-risk 計算；真正送單前仍需 R6 同帳戶鎖與原子 reservation，以及 R7 本次 G12 續行、到期與 event 消費驗證。

## 7. 舊路徑為何不能直接重用成完整證據

- [`private_rest.py`](../app/exchange/okx/private_rest.py) 的 `_request` 只回解析後 data、讀取可 retry，沒有 raw body／逐次 request/receipt/page provenance；positions/pending 固定 SWAP，history 固定單頁 limit。既有 Demo subclass 的固定 simulation header 可以參考，不能直接視為新 collector。
- [`private_parsers.py`](../app/exchange/okx/private_parsers.py) 會把缺數字補 0、缺 posMode 補 net_mode，balance uTime 缺失可補主機 now；新來源契約必須獨立 strict 解析。
- [`OkxDemoService.reconcile`](../app/okx_demo/service.py) 平行取六種來源，history limit=100，再篩掉缺 ID 或零量資料。persisted／reconciled_at 不代表完整頁鏈、同時點或同 uid revision。
- [`OkxDemoRepository.sync_snapshot`](../app/database/repositories/okx_demo.py) 是 current-state mirror，balance/checkpoint 使用 singleton id；替換 positions/algo、upsert orders，沒有本計畫要求的 account-scoped completeness ledger。
- [`Demo automation`](../app/demo_automation/service.py) 的 peak／連敗／realized events 是既有策略運行狀態，不是本計畫的完整帳戶歷史與固定 drawdown-window 證據。不得為通過新 evaluator 將這些缺口自動補成零。

## 8. 待辦與下一 checkpoint 的驗收範圍

1. **先做 bytes-only 契約**：plan pin、uid/mainUid 差異、Demo/Live 混用、strict scalars／serializer trap、raw tamper、query/cursor/時間／資源上限、未知欄與空集合反例；全合成來源。
2. **再做固定 Demo GET transport**：另行確認 read-only 憑證整合方式；MockTransport 驗 isolation、秘密不落 audit、取消清理、每頁 barrier、無 retry/fallback。此階段仍不標完整帳戶。
3. **再做純頁鏈／事件／單位映射**：cTime 與 fillTime 不混、fee/funding 不重算、partial-fill 與 pending 聯集、unsupported exposure 拒絕；保持原 requested size/leverage。
4. **獨立持久帳本里程碑**：account-scoped local uncertain、history/peak bootstrap、revision 與修復／對帳程序；未驗收前不派生 complete。
5. **最後才接 R5 trusted materializer／G11**；R6 原子預留與 R7 runtime 續行維持獨立未完成。實际 Demo 帳戶／交易驗收必另立收據，本文件不包含任何此類樣本。

頁數、bytes、超時與初版支援帳戶模式是待明確決定並測試的工程限制，未校準，也不是策略獲利或風險安全性證明。
