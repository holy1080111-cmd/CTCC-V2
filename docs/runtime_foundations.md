# Demo runtime 基礎增量：實作不等於交易放行

2026-09-12 開發 checkpoint。既有 `d984753` Demo 服務持續運行；本頁的新元件
尚未部署，也沒有修改交易憑證、Live 權限、風控預算或重新送出維護平倉。
各元件的合成測試、真實 PostgreSQL 資料庫 transaction 測試與 Linux 檔案測試分開記錄。
來源、帳戶認證與 execution authority 保持 false；不把 DTO/hash/持久化當許可。

## 本次範圍

| 元件 | 新增能力 | 仍不具備 |
| --- | --- | --- |
| `market_bridge` / `SwapTickerV2` | 原始公開 packet 全重播＋外部 pin；SWAP contracts/base volume 分域、quote volume 未知；真 G1 與 v1/v2 JSON/hash 相容性 | clock/source/socket 認證、本次 runtime 組合 |
| `account_capture` | 固定 13 streams 的 bytes-only 身分／頁鏈／clock／freeze/replay 驗證 | private transport、完整 history/peak/ledger/specs/protection materializer |
| `reservations` / qualification ledger | 同 Demo UID／幣別 row lock、事件 tombstone、兩個既定價格情境的 exact risk/margin/notional 保留、revision 與不可變 journal | 所有可能成交價覆蓋、真帳戶認證、本次 G12 續行或送單授權 |
| `trade_evidence.outbox` | immutable enqueue marker、append-only state、worker fence、遠端介面在本地 lease 外執行、未知結果保留 | 真 Notion adapter、scheduler 接線、遠端 exactly-once 證明 |

## 持久預留的邊界

Migration `0017` 新增 `qualification_account_scopes`、
`qualification_reservations`、`qualification_reservation_transitions`。原 `0016`
部署資料不由此開發流程遷移；測試只使用內網、無 host ports 的 tmpfs PostgreSQL。

`LedgerScope` 以 demo、exact numeric UID、settlement currency 分域；account revision
與 ledger revision 分開。`QualificationLedgerRepository` 注入 session factory
與 clock，沒有全域 DB／設定／exchange API。所有 mutation 在同 scope lock 內
驗 expected revision。原 event 唯一性不因新 report、較晚 expiry 或程序重啟而重置。

風險重算使用原 candidate 與最新 executable quote 的兩個明示情境；預留額由
exact fractions 取逐項最大值，再向上取至 20 位小數，且向上取整後也須符合 caps。
同時合併當前 account claimed pending 與 local active holds；相同 reservation ID
只在完整欄位一致時去重。較有利價格不能消除原候選的風險失敗。

`consume_once` 不是送單：它重驗期限、quote、當前 claims／guard 及風險覆蓋後，
只做一次持久狀態轉換。`uncertain` 不因 TTL 過期而自動釋放；只有明確、較新且
新鮮的 complete flat/no-pending **claims** 與 revision 對帳才可釋放，原事件
tombstone 仍保留。claims 的真實完整性必須由尚未完成的可信 adapter 另行證明。

## 非阻塞記錄佇列的邊界

`enqueue` 必須在收到實際 submit 結果後由受信任 producer 呼叫；此模組只驗
bounded metadata／source pins／submission receipt claims，不能自行證明交易發生。
report ID 綁 immutable JSON；後續狀態為 hash-linked append-only journal。
只有本地 dispatching marker 確實持久化後才呼叫 injected adapter，且不持有檔案 lease。

只有明確帶 receipt 的「未建立」結果可依有限 backoff 重試。timeout、crash、
cancellation、過期 dispatch fence 一律 uncertain，不盲目重建遠端頁面；需要
worker 靜止與獨立 receipt 的明確 reconciliation。所有 terminal/unknown records
保留；retention decision 不執行刪除，也不把 Notion 重試連到交易重送。

Storage 沿用既有 native ancestor handles／ACL／no-clobber／讀回檢查，沒有
權限 fallback。Windows 此主機的 ancestor 開啟拒絕仍未解；Linux IO 通過不能
冒稱 Windows 成功。合作程序 lease 不防同權限惡意程式，hash 不證明遠端 exactly-once。

## 驗證與剩餘工作

工作樹初步 market/account/G1/prefix/engine 範圍 1,239 項通過（108.01 秒）；
後續 v2 多／空實際 G1–G11 重播及 POSIX G12 六檔讀回擴充後，market/parser
範圍 48 項通過（16.58 秒），彼此重疊不相加。交叉審查修正 inverse 合約結算幣
推導後，bridge 僅接受 reviewed USDT universe；帳戶修正時區 callback 與 bills 時間
語意後，最後獨立單元 291 項通過。Linux outbox/account/market 最終範圍為
501 passed／1 Windows-only skipped（21.54 秒），包含 7 個實際 POSIX outbox IO
測試及多／空 G12 六檔讀回。Outbox Windows 本機為 154 passed／7 POSIX-only
skipped，原生案例只確認 ancestor 權限拒絕且零寫入，未證明原生成功。

Ledger 初輪隔離回歸未通過：21 failed／57 passed／8 errors（52.19 秒）；定位
ExecutableQuote preflight 型別回歸及測試 quote-age 120 超出既有 60 上限，
另由獨立審查發現 duplicate self-hold 必須在存 claims 前拒絕。三處修復後，
第二輪 91 passed（100.32 秒）：69 新單元及 22 真實 PostgreSQL integration，
涵蓋雙 worker、重啟、鎖後到期、晚期 clock／quote／account stale 回滾、
去重／duplicate self 拒絕、disarm、flat 對帳、journal rollback 與 SQL 不可變性。
另本機 ledger/current-risk/origin 範圍 311 passed（55.46 秒），彼此重疊不相加。
未改 evaluator 上限或略過失敗。完整隔離驗收仍待固定來源後執行，不沿用
d984753 的完整回歸計數。

完整驗收將使用 Git tree archive 的唯讀 `/workspace` mount，外部逐 Git blob
核對 archive／解出來源，再驗 canonical manifest；container 內以外部 pin 核對
manifest 與逐檔內容，不把列印 tree ID 當獨立 Git tree 證明。image
`sha256:aa3593afabeeeb272983fa3f52a55c73774a90a2b9170ca8f723f1ad371df616`
只提供既有 Python／相依套件 runtime；不是本輪新建 image，也不是部署驗收。

下一依賴仍是可信 source clock／account materialization、本次 G12→fresh recheck
→reservation 的 one-shot runtime、全部 Demo submit/protection 路徑、真 Notion
adapter、realized forensics、完整 regime history 與真正 Shadow/Demo soak。
新流程真實 Shadow／Demo 樣本仍為 0／0；維護清倉不計入這些樣本。
