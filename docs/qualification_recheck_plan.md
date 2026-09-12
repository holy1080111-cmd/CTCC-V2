# 完整 Execution Recheck：下一步可執行實作計劃

狀態：**R1–R4 離線計算／recorded-only 組合已實作；封存來源驗收另記，R5–R7 runtime 未完成**。
原依賴審查以 `7c7e9b5` 為基準；新增介面與限制見
[recorded-only recheck](recorded_recheck.md)。這不是完整 recheck 完成證明，
本計劃不啟用交易或修改部署。
本輪 R1–R4 的精確來源與平台结果見
[recorded recheck 驗收](evidence/qualification_recorded_recheck_20260912.md)。
G1–G12 的來源封存／平台測試／CI 已獨立記錄於
[驗收紀錄](evidence/qualification_pipeline_20260912.md)，不等於以下 R1–R7 的完整 runtime 交付。

## 1. 最小安全接點與現有能力

| 能力 | 可重用的實際介面 | 尚缺的完整組合 |
| --- | --- | --- |
| 原始 G1–G11 重放 | `engine.verify_pre_evidence(run, market, **original_inputs)` | 必須保留原 source、policy、intent、risk claims；不能拿最新 snapshot 重跑後替換原紀錄。 |
| 本次 G12 | `trade_evidence.gates.publish_qualification_evidence(...)` | 新 one-shot 外層在它**本次**回傳 G12 PASS 後立即續跑；不接受舊 receipt／caller-made G12 取得續行權限。 |
| 發佈屏障後報價 | `quote_collector.collect_executable_quote(..., barrier_completed_at=receipt.completed_at)`，再 `validate_collected_quote` | 屏障必須取自上述實際返回的 receipt；全批 request start 嚴格 `>` 屏障，最後決策時再次驗每個 component freshness。 |
| 新資料品質／分析 | `data.evaluate_data(...)`、`analysis.service.analyze_snapshot_at(...)`、`inspect_candles_at(...)`；`current_conditions.evaluate_current_conditions(...)` 實際重算 G1–G4 | 已重建當前品質／分析／策略條件，並由 R2 獨立驗證 append-only 延伸；仍沒有新管線的受信任 bounded 原始 OHLC／WS 收集接線。 |
| 原事件存活／時效 | `continuation.evaluate_continuation(...)`、`timing.event_identity`、`evaluate_timing`、原 `TriggerDetection`／zone | 固定原事件的四 TF continuation 已實作；不能讓新事件刷新原候選，也不能把 confirmed OHLC 存活當成完整 intrabar 路徑證明。 |
| 原 zone 和最新價格 | `location.evaluate_location(...)` | 使用原 zone／entry／expiry；不能 `build_entry_zone` 重新製造新位置或延長期限。 |
| 固定 SL/TP 驗證 | `fixed_protection.evaluate_fixed_protection(...)` | 已重播原 selector／audit 並以新來源重驗原 bracket；不重選更有利價格，仍須搭配來源認證與完整路徑政策。 |
| 可成交價格成本 | `executable_economics.evaluate_executable_economics(...)`、`current_risk.evaluate_current_risk(...)` | **已實作**兩情境成本、兩情境帳戶上限及 recorded-only 原 policy／fresh quote 綁定；缺本次受信任 G12 續行、帳戶認證及 exact 單筆預留。 |
| 當前帳戶風控 | `portfolio.evaluate_portfolio(...)` 和 typed account／instrument／Demo guard records；`recheck.evaluate_recorded_recheck(...)` 逐步組合 | 已有純計算及記錄一致性檢查，沒有產生這些嚴格合約的受信任完整帳戶收集器，也沒有本管線原子風險預留／event ledger。 |

程式依據：
[G12 one-shot](../app/trade_evidence/gates.py)、
[原事件連續性](../app/trade_qualification/continuation.py)、
[固定保護驗證](../app/trade_qualification/fixed_protection.py)、
[双情境成本](../app/trade_qualification/executable_economics.py)、
[當前雙情境風控](../app/trade_qualification/current_risk.py)、
[離線組合](../app/trade_qualification/recheck.py)。
現有雙情境成本的 116 項測試與限制已記在
[data qualification](data_qualification.md)；它不是尚待重寫的公式，
也不能被單獨的 PASS 說成完整 Execution Recheck。

## 2. 分階段交付順序（保留原需求與尚缺的 runtime 邊界）

以下狀態指程式的離線實作範圍，不是完整 runtime 的完成勾選。
工作樹 focused 測試與不可變來源的 Linux／Windows／matching CI 驗收分開記錄；
本頁不預填尚未完成的封存來源測試結果。

| 階段 | 已有實作 | 仍未交付 |
| --- | --- | --- |
| R1 | `RecheckOrigin`、原 source／policy／intent／snapshot 重播及固定最早期限 | 本次 invocation 真正觀測 G12 發佈的 runtime 權限 |
| R2 | 原事件重播、四 TF 不可改寫且連續的已確認歷史、失效觸碰與盲區紀錄 | 受信任逐 TF capture 與完整 intrabar 路徑證明 |
| R3 | 實際重算 current G1–G4、固定原 SL/TP 的 noise／liquidity／較近障礙驗證，原 timing／zone 重查 | runtime 來源接線；不得以新事件或新 bracket 修復失敗 |
| R4 | 原候選／可成交報價兩情境成本及當前帳戶計算，保留完整拒絕原因 | exact 單筆風險預留；顯示用最大 risk／notional／margin 不是帳本金額 |
| R5–R7 | 仍是下列完整需求與後續工作 | 受信任 adapters／完整帳戶認證、原子 event/risk 帳本、本次 one-shot 組合 |

本輪已實作原事實鎖定、current G1–G4、四 TF 連續性、固定保護、雙情境當前
風控與 recorded-only 組合。這些離線元件不能代替 R5 的真實來源認證、
R6 的原子預留或 R7 的本次 one-shot 續行。以下各 R 的完整交付要求仍保留；
來源封存和實際測試結果另記，不把純子檢查的 PASS 升級為交易權限。

### R1 — 固定原始交易論點的純合約

已實作 `app/trade_qualification/recheck_models.py` 的 `RecheckOrigin`、
`freeze_recheck_origin`／`replay_recheck_origin` 與單元測試。它固定並重播
已記錄的 G1–G12 事實，但不證明本次 invocation 實際發佈；本次 G12 接點仍屬 R7。
完整合約保留 report／instrument／strategy／direction、原 entry、
原 SL/TP、setup time/basis/invalidation、原 event key、zone、各 policy pin、
publication completed time、六檔摘要。新時效上限只能是原 intent／trigger／
zone／timing deadline 的最小值，不能延長，也不能靠改 report ID 重生。

已實作的純 `RecordedRecheckAssessment` 位於 `recheck.py`，採
exact type、frozen、bounded、UTC-normalized，保留已驗輸入的
source／quote／reference／decision pins、各步 fail codes 和明示缺口；沒有 IO 或
runtime 權限。此階段**不追加** `EntryQualificationResult` 的第 13 個 PASS：
現有 domain 的 `qualified` 會在全 13 關通過時計為 `ORDER_ELIGIBLE`，不能把
「純子檢查通過」包裝成尚缺真實帳戶／原子預留的完整資格。

### R2 — 原 OHLC → 後續 OHLC 的不可改寫連續性

已實作 `app/trade_qualification/continuation.py` 的 `evaluate_continuation`／
`verify_continuation`：重播原 source／analysis／event，驗證新的 bounded raw
snapshot。當前分析由組合中的 G1 重建；continuation 獨立重建品質並驗每個
4H/1H/15m/5m 的重疊已確認 candle 全欄一致，原 tail 與新 tail 中間無遺漏。
目前要求保留原歷史並 append，超出既有 1024 上限就拒絕，不默默截斷、
sort、補洞或用新 high/low 改写原歷史。合併 snapshot receipt 仍不是逐 TF
收集 provenance；以下實際 source capture 要求保留給 R5 的受信任接線。

`Candle.timestamp` 是 open；close 必須加 `BAR_SECONDS`。新增 confirmed bar
的 close 不得晚於其實際 source capture；每個原生 timeframe 必须覆蓋截至
收集時已應收盤的區間，不能只用既有「容許幾根 K 過期」政策掩蓋漏抓新 bar。
原 setup 之後可確定順序的 confirmed bars，任一 timeframe 觸碰原 invalidation
即失效，且 touch 後反彈不能恢復。跨 setup 的大 bar 不推測 intrabar 順序。

原 source 先重放證明原 event；後續資料只驗它的存活，不接受新的 latest
event 替代。必要時遇到不同最新 event 保守取消。source hash 會因新增資料改變，
不要求新舊 source hash 相同；必須保存 append proof 和未變的原 event identity。

邊界：confirmed OHLC 只證明到最後已確認 close，fresh quote 只證明當下快照。
尚未收盤區間中的刺穿後收回不能被宣稱已排除。紀錄各 TF 的 `verified_through`
和盲區；如完整路徑政策要求覆蓋到現在，缺逐筆／受信任 intrabar 證據就拒絕，
不能捏造 confirmed bar 或默認「期間未觸碰」。

### R3 — 固定保護價格與當前必要條件

已實作 `fixed_protection.evaluate_fixed_protection`：原 bracket 只能由原 source
重算並與原 audit 比對後取得。`current_conditions.evaluate_current_conditions`
對新 raw source 實際重跑 G1–G4，驗必要條件／veto／允許路由；固定保護元件重驗
原 SL 是否仍清除原 invalidation、當前必要 buffer/noise 門檻、原 TP 前是否
出現新的較近對向障礙。條件惡化只能取消。

不要對新 source 呼叫完整 `evaluate_pre_evidence` 後採用新的 SL/TP；那會重新
找事件及選 bracket。當前 `select_structural_protection` 是選擇器，不是「只驗
這組原 SL/TP」的公開介面；該責任已由上述獨立 fixed-protection verifier 承接。
`evaluate_recorded_recheck` 已直接重用 `evaluate_location`、`evaluate_timing`，
始終傳原 entry、zone、創建時間、
expiry、原 timing policy 和當前 consumed-event snapshot。

### R4 — 直接接上現有雙情境成本與風控計算

離線部分已實作於 `current_risk.py` 與 `recheck.py`：原候選與可成交 reference
各自重新計算成本及帳戶上限，先失敗先停止。最新 quote 和各帳戶／instrument／
guard 時間的屏障檢查仍是 supplied claims 的一致性，不是來源認證。
exact 單筆預留尚未實作，`atomic_risk_reserved` 保持 `False`。

完整 runtime 仍須把本次屏障後嚴格重驗的 `CollectedQuote.quote`、原成本 policy、原 entry/SL/TP
送進 **既有** `evaluate_executable_economics`。它各算原候選和最新 ask（long）／
bid（short），保留相同 SL/TP、quote、policy，兩者都須通過；有利行情不能修復
原候選失敗。不變更原 candidate，也不將樣本 quote 冒稱保證成交價。

離線外層已補驗原成本 policy、兩結果 quote digest 與新 capture 的一致性、
決策時間的 freshness 及 recorded G12 request barrier。R6 預留必須覆蓋
`worst_cost_adjusted_risk_per_base` 所代表的兩情境風險，不能只用原 G10 的較低風險。
現有 `evaluate_portfolio` 沒有 worst-risk 專用參數；已用同一份新帳戶／
guard／原 size 分別計算原候選和 executable 情境，要求兩者都過。
目前 `maximum_displayed_risk_amount`、`maximum_displayed_notional`、
`maximum_displayed_margin` 僅為兩情境最大值的
顯示紀錄，**不可直接當 exact 帳本預留金額**。後續必須用原始已驗證 operands
求得覆蓋各情境的單筆預留，**不是把兩情境各預留一次**。
帳本不得使用報表 round 後的 RR／風險；以已驗原 operand 作 exact 計算或明示
向上取整合約，避免少預留。兩種樣本的 worst 不代表所有可能 fill 的最壞情況。

### R5 — 新鮮資料 adapters 與完整帳戶認證（未完成，獨立里程碑）

在純 R1–R4 驗收後，才接受信任 adapters。公開 quote collector 已能使用；OHLC
需要各請求實際 start/receive/end、identity、bounded raw hash，WS 需要真實 frame
及 source/receive time，不可把 REST 改名 WS。舊 `MarketDataService.snapshot`
預設 100 bars、合併 receipt，不能直接當新 provenance 合約。不能捏造 mark／
funding／帳戶來源時間或放寬已發現的 source-versus-host 時鐘不一致。

帳戶 collector 要完整 balance、positions、exchange pending、local in-flight、
loss history／peak window、instrument units、correlation group 及獨立 Demo guards；
對缺 page、未知空集合、stale/mixed account、Live stamp、sequence gaps 一律拒絕。
明確保存收集截止點／reconciliation revision，各資料請求必須由本次屏障後啟動。
時間戳／hash／`complete=True` 本身不構成真實來源認證。

本輪只讀 adapter 盤點已對照實際程式，確認不能直接沿用的接點：

- [public WS receive](../app/exchange/okx/public_ws.py) 的 `_consume` 收到 raw
  frame 後才 parse／送 handler；新適配器須在 merge 前保存同一 ticker frame
  與接收時間。[RealtimeMarketHub.apply](../app/market/realtime.py) 的合併
  `exchange_timestamp` 可由其他頻道更新，不能拿它配舊 bid/ask 生成新 WS 證據。
- [MarketDataService.snapshot](../app/market/service.py) 預設 100 bars、整批
  receipt，仍缺每 TF raw response／request-start／receive／complete 與分頁鏈。
- [private REST read methods](../app/exchange/okx/private_rest.py) 的 pending／
  order-history／algo pending 都是單次 GET；[reconcile](../app/okx_demo/service.py)
  並行取六項、history 固定 100，沒有同帳戶完整頁鏈與統一 revision。
- [private parsers](../app/exchange/okx/private_parsers.py) 的缺數值補零與
  `parse_balance` 缺 `uTime` 補現在時間，不可用於新來源完整性合約。
- [mirror repository](../app/database/repositories/okx_demo.py) 的去重、置換
  snapshot 和 `reconciled` checkpoint，不等於所有 exchange pending 與本地
  uncertain/in-flight 風險的完整、revision-bound 聯集，也不提供持久 peak／
  窗前 loss-streak seed。未知資料必須維持未知，不能以空集合／零填補。

最小安全下一步為獨立 bounded raw WS ticker receipt 的解析／重建驗證，再接
保留每頁／每 TF 時鐘的 OHLC collector。純 parser 仍不宣稱真實 websocket IO；
帳戶需完整分頁、同域 revision、本地風險聯集及持久 history/peak checkpoint。
此盤點沒有讀取憑證值、呼叫帳戶 API、修改舊 parser 或變更既有部署。

### R6 — Durable account lock + event/risk reservation（未完成）

新增獨立 Demo-qualified event/risk 帳本：以 account/environment 分域，原 event key
唯一；在同一互斥／transaction 邊界讀取當前帳本、重驗帳戶 revision／expiry／
guards、計算所有 existing+pending+candidate caps、預留 worst sampled risk 並標記
event in-flight。跨 process 同時請求只能成功一次，原 report rename 不可重試。
中斷／未知提交結果保留風險與事件 tombstone，經明示 reconciliation 才可處理。

已存在的 `OkxLiveExecutionRepository.execution_lock/reserve_intent` 提供 lock／
insert-once 設計參考，但屬 **Live write** 管線，不能借作此處 Demo 授權。
`DemoAutomationRepository.fingerprint_exists/save_fingerprint` 是分離查詢、可
upsert expiry 的去重介面，不是本管線的原子 event+portfolio-risk 預留。
沒有這層前，純 risk PASS 仍保持 `atomic_risk_reserved=False`，不得接 submit。

### R7 — 本次 G12 → recheck 的一次性組合驗收（未完成）

新增固定依賴的 one-shot `publish_and_recheck` 外層：只呼叫本次真正 G12，成功
且仍在原期限內才立即收集／重驗 R2–R6。不能公開「舊 EvidenceGateRun／receipt
轉 PASS」入口，也不能接受 caller callbacks 直接回 passed。G12 原六檔和
pre-G12 report 不覆寫；新增獨立 recheck／reservation audit，釘住原 packet。
每步後檢查時間單調與最早 expiry；任何失敗立刻停下游，不發布第 13 關成功。

這一里程碑完成完整重查與預留，不等於啟用下單。接 Demo executor 仍需獨立
受控驗收 Kernel/EStop、guard changes、提交 idempotency、保護單與未知結果恢復；
Live flags 和現有部署不屬本計劃的變更範圍。

## 3. 必加的整鏈 acceptance cases

1. 真 synthetic FVG long/short：本次真 G12 → 屏障後 capture → 原事件存活 →
   固定 SL/TP → 現有雙情境成本 → 新帳戶計算；正例不 mock PASS。
2. 舊／相同 `already_present` packet、手填 receipt、自簽 G12、失敗的前 12 關：
   不讀新市場／帳戶、不預留；途中 expiry 和 expiry 恰相等均拒絕。
3. 任一 endpoint start 等於／早於 publication complete、自身 source ts 晚於
   receive、單一 stale funding、時钟倒退／fold、UTC+8 等價、總 timeout/cancel：
   結果因果一致；不補時間、重試繞過或吞 caller cancellation。
4. 四 TF 分別測改寫 overlap、missing original tail、duplicate、gap、out-of-order、
   off-grid、未收盤／future confirmed、NaN；新增已應收盤 bar 未取得也不能 PASS。
5. 原 invalidation 被後續 5m/15m/1H/4H 觸碰後回復，雙向均拒絕；跨 setup bar
   不推斷順序。不同新 event、report rename、重新 timestamp/expiry 均不能續原單。
6. 新反向必要條件、route veto、noise buffer 不足、原 TP 前新較近障礙：取消而非
   重選；驗原 entry、SL、TP、zone、policy、expiry 每項 bit-for-bit 不被替換。
7. 原 entry 的 net RR PASS 但最新 ask/bid FAIL；有利新 reference 也不能修復原
   候選 FAIL；成本增加不縮 SL。保留原 policy／fresh quote pins，RR equality /
   epsilon、hostile Decimal context；worst sampled risk 必實際進入帳戶上限判定。
8. 新帳戶在 G12 後增 position／pending、掉 margin、DD／loss／correlation 超限、
   armed false／EStop／Live flag／缺 history：停止且不借原 G11 PASS 放行。
9. 兩 workers 同 account+event、不同 report 同 event、帳戶 revision 變更、expiry
   在 lock 後到期、reservation 後 crash、未知 submit、DB rollback：最多一個有效
   預留；風險不消失，不自動重播、不重複計算兩情境為兩筆風險。
10. 純 R1–R4 沒有第 13 關／ORDER_ELIGIBLE；新程式無 legacy candidate builder、
    now/settings 偷讀、網路／下單副作用，fake raw source 只作 synthetic test 標籤。

完成定義：每個 R 里程碑各自有可重現案例／不可變來源 receipt 和 focused 測試；
G12、公開 capture、雙情境成本、portfolio 各自 PASS **不等於** 已完成 R7 整鏈。
整體 runtime 驗收、來源認證、atomic ledger、未校準策略路由及真新管線樣本，
不能因這份計劃存在就從未完成項勾除。
