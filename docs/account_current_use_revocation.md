# V7 帳戶空倉現行用途撤銷

先前 V7 current-source 與 native-clock 收據可依原本 policy、原始頁鏈與 hash
重播，內容不修改。舊 V7 空倉觀察沒有把 REST gateway `inTime`/`outTime`
及來源 row 時間相對於同頁 `outTime` 當成必要條件，因此不能再當成**現行**
空倉資格。`account_native_proof.current_use_contract_for_plan` 擋下新的 V7
flat native-proof 發行；歷史 `contract_for_plan` 與純重播維持原樣。
目前硬拒絕接在 native 初始／歷史接續的新發行入口；其他歷史診斷讀者仍可
重播舊 `observed_flat` 欄位。它們未接入新的正向現行資格，舊欄位不得單獨
用來解除安全閘。此切片沒有宣稱所有讀者已改用新收據。

`account_current_use_revocation.audit_v7_current_use_revocation` 從完整、外部
pin 的原始 B1 V7 頁鏈重播既有 V7 驗證與 row/gateway 稽核，產出新的版本化
DENY-only 收據。每一頁都檢查原始 REST gateway 時間是否齊全，並比對該頁
所有已知來源 row 時間不得晚於原始 `outTime`。缺 gateway 欄位是
`exchange_gateway_time_missing`，來源時間較晚是
`source_timestamp_after_gateway_response`；不以本機接收或關閉時間補值，也不
加入容忍度。若有頁面缺 `inTime`/`outTime`，
`gateway_causal_coverage_complete=false` 且
`known_source_row_times_ordered=false`；無法比較不等於已證實時間順序。
即使時間全部一致，收據仍固定
`current_flat_qualified=false`、`flat_start_permission=false`、
`account_complete=false`、`execution_authority=false`、`admission=DENY`。

[OKX 官方 API guide](https://www.okx.com/docs-v5/en/) 定義 REST `inTime`
為 gateway 收到請求、`outTime` 為 gateway 送出回應，單位都是 Unix 微秒。
此因果檢查不證明註冊站點、憑證 session、完整帳戶歷史、exchange-wide
原子快照、保護或下單權限。未建立任何新的正向交易路徑。
