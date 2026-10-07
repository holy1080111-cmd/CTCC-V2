# Recorded Demo account materializer

狀態：已實作純離線 raw-record 映射、完整性評估和重播；**不是可信完整帳戶、完整 R5／G11／R6／R7 或送單授權**。沒有網路、設定、憑證、系統時鐘、資料庫與下單操作。來源限制承接 [帳戶來源計畫](qualification_account_source_plan.md)。

## 輸入與輸出

實作：[account_materializer.py](../app/trade_qualification/account_materializer.py)。

`materialize_demo_portfolio_snapshot(packet, *, expected_plan_sha256, expected_packet_sha256, inputs, expected_inputs_sha256=None)` 先 exact-type 檢查 supplemental inputs，再從原始 packet bytes 重播。`expected_packet_sha256` 是 `freeze_demo_account_packet` 的 payload SHA，不是 packet 內自簽欄位。

`AccountMaterializationInputs` 必填 `account_id`、`settlement_currency`、`correlation_version`；`environment` 固定 `demo`。其餘資料缺少時保留缺口：

- `instruments`：每筆 `InstrumentEvidence` 是**單筆 instrument JSON row** 的 bytes／SHA，加上明示的 recorded `observed_at`／`received_at`。不是 API envelope。instrument API 不提供這個 observation clock，故此時間是外部 provenance claim，不能冒稱交易所更新時間。
- `correlations`：唯一 instrument → group，明示版本；不是交易所提供的分類。
- `costs`：每 instrument 的 `cost_per_base`、來源 pin、資料時鐘。成本是明示輸入政策，不從 score／槓桿推估，也不是新的成本校準器。
- `ledger`：既有 `LedgerScopeState` 加 source stamp 和可選精確 order／client-order 關係。source SHA 必須等於既有 `reservations.digest(state)`；仍不認證實際 DB revision。
- `history`：同 Demo／UID／幣別的 recorded seed、窗口、連續本機 outcome sequence，以及引用原始 fill／funding bill IDs 的 grouping。
- `peak`：同 scope 的固定窗口與原始 balance samples。sample 使用相同 `InstrumentEvidence` raw-JSON/provenance 容器，但在此欄只解析 balance row，並核對 balance `uTime`；不把 instrument 欄位當 balance。

`copy_materialization_inputs` 在任何 serializer 前拒絕 subclasses、hidden fields、opaque scalars、iterators、非有限數字和不可信 timezone callback，回傳重新建構的 immutable inputs。`materialization_inputs_sha256` 給外部 pin；有 IO 的協調器應在 IO 前固定 copy 和外部 SHA，完成後用同一份 copy 與 `expected_inputs_sha256` 重驗。純離線 API 省略此外部 pin 不代表輸入已認證。

結果 `AccountMaterializationResult` 提供實際算出的 instruments、equity／available margin、positions、pending reservations、loss history、逐 row projections、原始 packet／inputs SHA 及缺口。無法表示的值用 `None`／具體 reason，不填零。只有已映射的 rows 進入 exposure tuple；projections 保留未映射 rows，未映射不會被誤當完整集合。

結果固定 `state=recorded_mapping_incomplete_account`，`account_complete`、`source_authenticity_verified`、`execution_authority` 全為 false。只要 packet 有任何 `incomplete_reasons` 或映射留下任何缺口，`snapshot=None`，即使可算出部分帳戶數字或所有合成 row 都有形狀。現行驗證過的 packet 必然保留來源、歷史和跨讀取完整性缺口，因此不產生正式 `PortfolioRiskSnapshot`。沒有 caller `complete` 開關；診斷欄位不能交給 `evaluate_portfolio` 作完整風險快照。

## 已實作的 recorded 計算

### 單位與曝險

支援範圍為 `acctLv=2`、明示單結算幣與 `linear` SWAP。`ctMult` 必须明確為 1，`ctValCcy` 與結算幣不同；缺漏、inverse、其他產品或不一致欄位不映射為合法風險規格。

線性衍生品的 `ctVal` 為 base currency 單位，因此由 `ctValCcy` 取得 base；官方 `baseCcy`／`quoteCcy` 只適用 SPOT／MARGIN，可以缺少或為空，但若非空就必須一致。沒有拆 instrument 字串猜單位。價格 × contracts × ctVal 得到結算／報價幣 notional。公開 `lever`／`maxLmtSz` 僅形成已記錄規格，不是帳戶當下槓桿許可。[官方 instruments](https://app.okx.com/docs-v5/en/#public-data-rest-api-get-instruments)

balance 只從唯一匹配的 `details.ccy` 讀取 `eq`／`availEq`；頂層 `totalEq`／`availEq` 及 `notionalUsd` 不改標 USDT。未知 liabilities／borrow scope 保留缺口，不宣告為零。內部 balance 時鐘保守使用頂層與幣別 `uTime` 的較早者；缺 source clock 不用 receipt time 代替，也不產生正式 snapshot。[官方 balance](https://app.okx.com/docs-v5/en/#trading-account-rest-api-get-balance)

持倉按真實 `posId`、net signed quantity 或 hedge `posSide` 判定方向，核對 raw `instType` 與 instrument 規格。notional 用當前 raw `markPx`；margin 必須有明確 raw `margin`，不由槓桿補猜。風險從 mark 到實際匹配 stop 的距離加明示每 base 成本計算。

只有唯一 instrument／posSide／反向 side／margin mode／完整 quantity 可匹配，且 `reduceOnly=true`、live conditional／OCO、明確 mark-trigger market SL 的 algo 才標為 protection。多個可匹配 algo 不任選一個。OCO 另外檢查 TP 方向與型態。無法支持的 algo 保留 `unsupported_algo`，**不一律當成開倉掛單，也不默默消失**。這只證明 recorded geometry，不保證實際成交價格。[官方 algo list](https://app.okx.com/docs-v5/en/#order-book-trading-algo-trading-get-algo-order-list)

普通 opening pending 初版僅支援明確 limit、`reduceOnly=false`、有效 leverage 及單一 attached SL。數量是 `sz - accFillSz`，不再把已成交部分全算為 pending。`reduceOnly=true` 普通單只列 reducing order，不能替代持倉保護；其來源若沒有有效正數剩餘量、可辨識的結算／持倉模式或更新時間，會保留具體缺口，不把 `None`／零剩餘量當成正常的空掛單。market／動態或其他不支援旗標不猜價格或止損。

## Ledger／history／peak 的有界計算

local `reserved`／`consumed`／`uncertain` hold 沿用既有 exact dual-scenario coverage；不看 TTL 釋放。scope、revision、source time、合約價值與 correlation 均須匹配。明確 order ID＋client-order ID 關係才能合併 exchange／local hold，三個金額取各自最大值；partial-fill 情況仍保留完整不確定 local hold 並標記 conservative union，不能藉帳面成交量削掉未對帳風險。缺關係不任意丟棄 hold。這不是原子 reservation 或跨讀取對帳完成。

history 對 supplied grouping 逐筆驗 fill IDs、費用幣別、方向、事件時間和 inventory，僅支援從 flat 開始、未越過零而最後完全回 flat 的 recorded round trip；相同 fillTime 的先後不明時不猜順序。recorded seed 與 outcomes 的起訖必須**恰好等於**本次 packet 固定查詢的 `history_start`／`history_end`；較短區間即使內部每筆成交可映射，也回報 `history_recorded_window_incomplete`，不產生 mapped history。`closed_at` 取實際最後 closing fillTime，不取 order cTime 或 receipt time。已提供 fills 的 recorded PnL 使用 `fillPnl + fee`，但必須先有每筆正數 `fillPx`，且交易所 `fillPnl` 毛額須與相同成交價格／數量、線性合約面值的獨立 FIFO 重算精確一致。矛盾時回報 `closed_outcome_gross_reconciliation_mismatch`，不把來源不一致的數值寫成已實現 outcome；這不是 exchange 真實性或全歷史證明。2026-09-19 修正：funding bill 的 `ts` 只保留原始帳戶 cashflow 時間，不能證明某 holding 的 accrual 歸屬；本 grouping 模型尚無獨立 accrual provenance，因此含 funding bill 的 outcome 回報 `funding_accrual_provenance_missing`，不產生 outcome／snapshot，原始 `balChg` 與頁鏈照常保留。未知 cashflow／未分組 fills 保留缺口；grouping、seed 仍是 recorded local claims，不認證 ingestion watermark 或全帳戶 outcome 歸屬。[官方 fills](https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-transaction-details-last-3-months)、[官方 bills](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-3-months)

v2 packet 的近期／archive fills 與 bills 以相同 billId、完全相同 canonical row 合併為一次經濟事件，兩份 raw receipts 都留在 packet；orders history 另外以 ordId 檢查相同 canonical row，僅作對帳，不作 PnL。任一重疊衝突不選擇勝者，回報 `history_overlapping_source_conflict`。新增八類 algo inventory 中未分類的活動單不消失。新增本次 account instrument 與 cross／isolated leverage 資料核對；任何 metadata 缺失／scope 不完整／規格衝突都阻止 snapshot，不能由舊 supplemental input 覆蓋本次讀回。

peak 是原始有限 balance samples 的實際最大值及最早達峰時間；核對 scope、raw hash、window 與 current equity，不把 current equity 自動當 peak。每筆 sample 的頂層與幣別 `uTime` 都必須存在、位於窗口開始之後且不晚於 receipt；記錄時間使用兩者較早者並與 sample `observed_at` 相符，不能用頂層時間掩蓋幣別的未來或缺失時間。sampled maximum 不代表連續窗口峰值或 drawdown bootstrap 已完整。

計算用 Fraction，於既有 20 decimal places DTO 邊界才一次保守量化：曝險／margin／cost 方向向上，equity／available／realized PnL 向下。未改 requested contracts、leverage 或任何現有 risk policy。資源邊界是工程上限（8 MiB、128 instruments、2048 exposures、8192 projections、bounded JSON depth／nodes），不是校準結論。

## 重播與 runtime 邊界

### 新增：account anchor 跨來源反證檢查

`app.trade_qualification.account_consistency.reconcile_recorded_account_sources`
接受原 `DemoAccountPacket` 與外部 plan／packet SHA；先重播整份原始 packet，
再產生 immutable `AccountConsistencyReport`，不接受 caller 傳入的成功 report。
報告保留 anchor／balance／positions 的原始 receipt pins，逐项 finding 只列 field、
inventory identity、缺漏或矛盾及兩側 receipt，不附原始私人數值。仍屬私人帳戶證據，
不自動保存或發布。

已檢查的必要条件：

- anchor `balData` 與 balance `details` 的**全部幣別集合**相同，且結算幣存在；
  各幣共同欄位 `eq` 以 exact Decimal 比較，缺值不當成 0 或彼此相等。
  不拿頂層 USD `adjEq`／`totalEq` 去比 USDT，也不在 anchor 虛構或要求
  balance-only 的 `cashBal`（[官方 anchor schema](https://www.okx.com/docs-v5#trading-account-rest-api-get-account-and-position-risk)
  的 `balData` 是 `ccy`／`eq`／`disEq`）。
- anchor `posData` 與 positions 的全部 `posId` inventory 相同，包含零量 row；
  相同 ID 的 `instId`／`instType`／`posSide`／`mgnMode`／signed `pos` 必須一致。
  不只比較 ID 和數量。anchor 若有 `ccy` 也須匹配，不猜該產品未提供的幣別。
- anchor 必須有真實 raw `ts`，不得早於本次 publication barrier。
  balance 頂層及每幣 `uTime`、position `cTime`／`uTime` 必須存在且不晚於 anchor。
  更新較舊且未變動的 position 可以保留舊時間，不強迫改成同一 timestamp。
  來源更新比 anchor 新代表不能聲稱同一 cutoff；不加容差或覆寫時間。

materializer 在自身完成 raw replay 後執行同一檢查，將具體 blocking reasons
加入既有 incomplete reasons。**任何缺漏或矛盾都不建立 snapshot**，即使其他
supplemental ledger／history／peak 都可映射；已算出的逐 row mapping 仍可供調查。
已完整提供官方 anchor balance／margin mode 等合成欄位且無 finding 的 recorded
測試仍只保留診斷映射；原 packet 的未完成原因不會因合成補充資料而消失。

這是必要的反證檢查，不是足夠的全帳戶證明：分次讀到相同數字仍可能有中間變動，
沒有共同 exchange revision、可信 transport 認證、advanced／non-SWAP 全產品範圍、
orders／fills／bills 的共同 cutoff、持久 history seed／peak 或 local ledger 真實性。
空 findings 也固定保留 `source_authenticity_unverified`、
`cross_source_atomicity_unverified` 與 account scope/history completeness 未驗證；
所有 authority／complete flags 仍 false。16,384 findings 是工程資源界限，超過即拒絕，
不是截斷後冒稱沒有問題，也不是已校準風險政策。沒有新 API 呼叫或送單。

- `get_materialized_instrument(result, instrument_id)` 先 strict-copy／核對內部一致性，再抽已映射 spec；內部 hash 自洽不等於來源認證。
- `verify_account_materialization(result, *, packet, expected_plan_sha256, expected_packet_sha256, inputs, expected_inputs_sha256=None)` 必須由原 packet 與 inputs 全部重新計算並比對。
- `freeze_account_materialization(result, **replay_inputs)` 與 `verify_frozen_account_materialization(payload, *, expected_sha256, **replay_inputs)` 使用 canonical bytes、外部 SHA 與同樣完整重播；不能只拿一份自簽結果當成功。

輸出含帳戶與曝險 ID／數字，即使沒有 credentials 仍屬 private account evidence。協調器可保留記憶體供重驗；不可因新增 freeze API 就自動發布真實帳戶資料。單元測試全部使用合成資料，沒有真正帳戶或權限驗收。

未完成：可信 IO／source authenticity、由可信 observations 發行的本機帳戶 revision、non-SWAP／advanced-product coverage、完整所需 history ingestion／seed、明示版本的 peak／DD-window 政策、持久 ledger 真實性及原子預留、真正 G12 續行與送單。本機 revision 不冒稱交易所跨 endpoint 原子快照；measured HWM 不冒稱未抽樣最高淨值，舊 v1 continuous-peak 與完整帳戶 claims 仍 unknown／false。[B2a](account_history_query_verifier.md) 僅核驗已保存 fills／bills generation-time 查詢及保留範圍，不補出 loss window／seed／funding。同步時鐘、登入、部署或 Live 開關不在本模組範圍。
