# 原子預留消耗與持久送單意圖

`QualificationLedgerRepository.consume_with_submission_intent` 將既有
`reserved → consumed`、scope revision 與不可變 intent journal 放在同一個
PostgreSQL transaction 中。沿用 migration 0017，不新增資料表，也不掛入
現行 Demo scheduler；既有 `consume_once` 相容保留。

## 保證與先後順序

1. 取得同 Demo UID／結算幣 scope lock，驗 expected revision。
2. 讀回 immutable 原預留 request；重查當前帳戶 claims、期限、報價與原風險覆蓋。
3. 由原 request／consumed receipt 生成 `ctcc-demo-submit-intent-v1`，固定原
   report、event、Entry／SL／TP、contracts、leverage、coverage 與 source pins。
   client／protection client IDs 由同一 reservation ID 派生，caller 不能改名重試。
4. 在同一 transaction 寫入 consumed transition 的 evidence JSON，保留最後
   時鐘／風控檢查。任何 commit 前錯誤都回滾 consumption 和 intent。
5. commit 成功後另開 session 讀回 journal，重播原 request／receipt，核對
   SHA、immutable metadata、journal time 與 revision。此為 DB 提交／讀回，
   不宣稱硬體斷電持久性、交易所認證或已下單。

原 journal 的資料庫 trigger 禁止 UPDATE／DELETE，reservation 的事件 tombstone
不能透過換 report 或程序重啟消失。舊版只 consumed、沒有此 intent 的紀錄不能
事後補造「送單前已保存」；讀回會明確回 `submit_intent_missing`。

## 不確定結果

commit 回覆或後讀失敗時，呼叫方不可假設回滾，更不可自動再次 consume 或送單。
`read_submission_intent` 可在較晚的 uncertain／reconciled 狀態作歷史查證；它
不是 restart dispatch queue。過期不釋放 uncertain 風險，仍須既有明示對帳。

intent 保存當時尚未送單的事實，不代表之後或其他程序沒有交易。它不接收
caller PASS、order callback／payload／ack，也不選新的 order type、margin mode
或較新市價。`execution_authority`、`order_retry_authority`、
`all_fill_prices_covered` 保持 false；hash 或可讀回記錄不能變成可重用許可。

## 0027 送單後結案保護（待完整驗收）

舊 `reconcile_reservation` 可用呼叫者提供的較新空倉 claims，將已
`consumed` 或 `uncertain` 的預留標成 `reconciled_flat`。空的當前庫存並不能
排除延遲出現的訂單。0027 在 repository 讀取這類 claims 前拒絕上述轉換，
並將同一拒絕加入 `public.qualification_reservation_update()` 資料庫觸發器；
直接 SQL 也不能繞過。拒絕時原預留、送單意圖、修訂號與事件 tombstone
保持不變，重啟或期限屆滿也不自動釋放。

升級前會在同一個 NOWAIT 表鎖下稽核既存 terminal 預留。任何已是
`reconciled_flat` 的舊列都會使 0027 升級失敗，即使看起來只是
`reserved` 未送單取消；缺少送單轉移紀錄不能證明從未送單。升級不改寫、
刪除或自動隔離舊資料。目前部署資料庫是否含此類舊列仍未知；若有，既有
immutable ledger 無法靠一般 UPDATE／DELETE 清除 blocker。必須另外審查
來源證據及獨立的 disposition／quarantine migration，並保留原事件與意圖
可鑑識性；本 0027 不提供該遷移或 override。失敗狀態不得冒稱 schema
已受 0027 保護。

從未消耗的 `reserved` 預留仍可沿用既有的本地未送單取消語意；這不證明
交易所全帳戶已清倉，也不會使呼叫者 claims 成為可信帳戶來源。正式的
送單後結案仍缺受控、已認證的確切 Demo UID/session、最後送單嘗試後的
完整訂單／成交／倉位／保護分頁時間序和可驗證的結果終局。尤其不確定的
exchange response 不能靠一頁空資料或逾時自動解除。現有 Demo/Live
送單硬閘保持拒絕；0027 不授予交易權限或宣稱 Demo 驗收通過。

## 尚缺與驗收

這一步補的是持久化與一次性消耗，不是完整 execution runtime。可信全帳戶與
revision、本次 G12／完整 recheck 的受控接線、真正
Demo submit／protection／ack reconciliation 和 post-submit worker lifecycle
仍須獨立完成。不能因本元件通過就啟用新交易路徑。

2026-09-23 範圍更正：主指令要求原 candidate 與新 executable reference 各自
通過，再於原子 transaction 預留最差抽樣風險；不是送單前保證全部未來成交價
或市場止損的實際損失。`all_fill_prices_covered=False` 如實保存此界線，並非
必須改成 true 才能送單的 gate。FOK adverse price boundary、固定原 SL／TP、
實際完整成交／價格／RR／size／margin／leverage 核對與 mismatch EStop 仍必須
完成。較好的價格不能修復原 candidate 的失敗；此更正不授予 dispatch 權限。

單元測試使用合成來源；新增 PostgreSQL 整合案例驗雙 worker、rollback、
commit 後讀失敗、舊 consumption 拒絕補造及 uncertain hold。是否真正執行
以本輪固定來源驗收紀錄為準；2026-09-12 續作時 Docker engine 不可達，
不能將「新增案例」或上一版 Linux 成績寫成這一版資料庫整合已通過。
