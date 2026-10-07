# G12 後公開來源與帳戶來源同次接續：診斷 V2

`publish_capture_public_account_v2` 把現有 G12 發佈、原生公開資料收集與
原生 Demo 帳戶頁鏈收集放在同一個 task、同一次呼叫。它不接受外部 G12
receipt、`passed=true`、舊公開資料包、舊帳戶資料包、報價或發佈屏障。
原始 G1–G11 `run` 與輸入會由現有 `_original` 重播，保留原 event、entry、
SL、TP、report、instrument 及帳戶 UID。原始輸入仍由 caller 提供；這項重播
**尚未**把 V4 的受控原始來源診斷接成可建立候選的來源權限。

只有本次 G12 完整寫入並 readback 後，私有 `_PublicationV2` 才能開始新的
公開 REST／WS 收集。公開 carrier 在同一 task 一次性消耗，逐一要求 HTTP
request 與 WS connection start 嚴格晚於 G12 publication barrier。之後才
啟動受控 Demo 帳戶收集，將原生 V6 raw-packet lease 在同一 task 一次性消耗；
account packet barrier 與每頁 request start 必須晚於 G12，且每頁不得早於
本次 account capture start。帳戶計劃、exact UID、instrument、頁鏈、source
reference、proof、readback、receipt 與 lease expiry 都須一致。完成時再取
原生時鐘樣本，確認仍在原候選與 account lease 期限內，並重新檢查公開資料
在該時刻的完整度與 freshness。

回傳的 canonical receipt 只保留固定原交易幾何、時間、頁數與雜湊，
`admission=DENY`、`execution_authority=false`。失敗後，上層 receipt 清除未完成
join 的帳戶 pin／頁數，不能以部分帳戶讀取宣稱完整；已持久化的 G12、公有
journal 與帳戶頁鏈／proof 留在各自的 no-clobber 根目錄供稽核，不會因此被
刪除。呼叫後受控帳戶 session 被消耗，不能重用作另一筆嘗試。

目前原生 V2 Demo 公開來源的 region 認證仍被硬性拒絕，因此 production
路徑會在 G12 前拒絕；正向單元測試只用明示 synthetic transport、時鐘與
帳戶頁鏈驗證接續順序。這不是實際 OKX Demo evidence。此接續也尚未完成
source-derived Original Candidate、current G1–G4、原事件存活、原 zone、
固定 SL/TP、雙情境成本、完整 PortfolioRiskSnapshot、原子 reservation、
durable intent 或任一送單路徑的最終 authority。Demo 與 Live 下單持續拒絕。
