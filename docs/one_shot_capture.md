# 本次 G12 → 新來源 → 原候選重查

2026-09-12 開發增量；這是未掛入 scheduler／HTTP route 的明示診斷入口，
不是完整 R7 execution runtime，也不會預留風險或送單。

`app.trade_qualification.one_shot.publish_capture_recheck` 接受原始 market、
`PreEvidenceRun`、精確七欄 original inputs，以及固定公開收集政策、Demo
帳戶 plan／外部 plan pin／明示 credentials。沒有 injected publisher、collector、
order callback、caller PASS 或舊 `EvidenceGateRun` 參數。

## 實際順序與停止條件

1. 先驗 exact 原始型別、bounded scalar、已知時區與隱藏欄位；不執行不明
   `__class__`／serializer／timezone callback。忽略 caller quality 的值，品質重算。
   複製原候選、行情與政策後重播 G1–G11。較早關卡失敗不讀 clock、不寫檔、不開連線。
2. 本次真正呼叫既有 G12 出版器，產五圖＋report，使用原生安全存放／六檔讀回。
   只接受本次 fresh written receipt；失敗／已存在／到期則停止，不重新命名再試。
3. 屏障取該 receipt 的實際完成時間，固定 owned 公開 HTTP／WS 與 Demo GET-only
   帳戶收集器並行啟動。所有請求遵守既有嚴格屏障、source／receive clocks、分页
   與 timeout。任一失敗就取消並 join 全部 owned tasks，外部取消即使在失敗清理中
   到達也保持 `CancelledError`，不轉成普通拒絕後繼續。
4. 回傳原始資料逐一重播，核對本次 report／instrument／account plan／barrier；
   經版本化 SWAP bridge 產當前 snapshot，使用該 owned WS 的真 raw reference。
5. 可提供 [帳戶映射補充輸入](account_materializer.md)，但必須同時給外部
   `expected_materialization_inputs_sha256`。在 G12 之前複製與 pin，之後對本次
   packet SHA 與原補充輸入重播 materializer。不接受 caller 製造的 mapped PASS。
6. 實際跑 recorded recheck：原來源、capture barrier、current conditions、
   continuation、timing、原 zone、固定 SL/TP、雙情境當前帳戶風險。
   沒有新的完整帳戶就停止；**不回退到原 G11 account／authority**。

總 wall budget 為明示 1–120 秒（預設 90 秒），並保留既有 leaf 更短 deadline。
每次 clock 都檢 wall budget、單調性及原 intent／trigger／zone／timing 的最早 expiry；
cleanup 使用 leaf 自身的小額 bounded allowance，不能拿清理後的過期結果繼續。
同步原始驗證／render 本身不可搶占，但其後一定再次驗 wall／candidate deadline。
此 API 不內部重試，不改 source timestamp，不固定加時鐘偏移。

## 明確不授予的權限

結果 `OneShotCaptureResult` 只作 in-memory evidence。raw account、mapped account
及各來源不進 repr，不自動存 raw account，不輸出 provider exception／headers／secrets。
`record_kind=one_shot_capture_not_execution_permission`；execution authority、source
authenticity、account completeness、atomic reservation、order submitted 永遠 false。
現有 materializer 的四類 stamps 也保持 incomplete，local authority 明確是 None；
因此可計算部分 exposure，不會由這個接點進入 reservation／submit。

它確實在本次 G12 後啟動新來源，並非接受舊 receipt 的假接線；但不能把本次執行
或 hash 換成可保存、可重用的 permission token。成功取得資料也不是完整 intrabar
path／account auth／durable execution intent 的證明。尚缺事項見
[本輪交付與回家待辦](homewait_completion.md)。

## 驗證

`tests/unit/test_qualification_one_shot.py` 的雙向合成案例使用真 G1–G12 evaluator、
真 raw HTTP/WS parser、真 market bridge 與完整 recorded checks，只有 transport
是 synthetic。Windows 可攜 suite 的 publisher 是明確契約替身，不冒稱原生磁碟成功。
Linux 另跑真正 POSIX 六檔寫入／SHA讀回，再允許第一個 synthetic request 的案例。
兩種證據分開；完整固定來源驗收及未通過的中間 log 保存在本機 reports，
不得拿舊版測試數或合成案例當真實 Shadow／Demo 交易樣本。
