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

## 尚缺與驗收

這一步補的是持久化與一次性消耗，不是完整 execution runtime。可信全帳戶與
revision、所有可能成交價的風險覆蓋、本次 G12／完整 recheck 的受控接線、真正
Demo submit／protection／ack reconciliation 和 post-submit worker lifecycle
仍須獨立完成。不能因本元件通過就啟用新交易路徑。

單元測試使用合成來源；新增 PostgreSQL 整合案例驗雙 worker、rollback、
commit 後讀失敗、舊 consumption 拒絕補造及 uncertain hold。是否真正執行
以本輪固定來源驗收紀錄為準；2026-09-12 續作時 Docker engine 不可達，
不能將「新增案例」或上一版 Linux 成績寫成這一版資料庫整合已通過。
