# 動態槓桿恢復：使用者已選定，部署尚未切換

2026-09-12 使用者明確選擇恢復 **3／5／8／10／20 倍動態上限**，不是僅恢復
1／2／3 分級。這是依條件使用的上限階梯，不是所有部位強制使用 20 倍。

此最小修補工作樹以 `d3f206a59888ef3d72732fa30deaa8278ac72cc5` 為基礎，
只移植 `_twenty_x_reasons` 對 `risk_score is None` 的判斷修補，另附本文件、
保守設定範例與獨立回歸測試。不把未完成的 qualification pipeline 部署進來。
工作樹中的修補與測試不是已套用部署的證明。

[保守 overlay 範例](demo_dynamic_leverage_conservative.env.example) 已準備。
它不是 active `.env`，沒有 arming／write／Live 權限或憑證。五級 structural
risk 均為 0.005（每筆 0.5%），portfolio stop-risk 維持 0.01（1%）；不恢復舊
1.5%–6% structural 風險或 10% portfolio 限額。現有 300 USDT margin bucket、
60% portfolio margin 設定、SL／TP、成本、淨 RR 和 20x 品質門檻不放寬。
但目前 bucket 分支未另行套用獨立 60% 總保證金硬閘，不能把保留設定冒稱
新增防護；槓桿提高也可能用滿原本未用完的風險預算，並非實際損失不變的保證。

2026-09-12 主線診斷在原容器的獨立 subprocess 只建立 proposed Settings，
確認與原 Settings 真正不同的僅七欄：max leverage、structural enable、五級 risk。
既有五級 cap defaults 本來就是 3／5／8／10／20，範例明確釘住它們。
legacy 1／1／1 與 automation 1 設定可以保留：structural 分支不採用它們。
該次驗證未修改服務記憶體、磁碟 `.env`、容器、倉位或權限。本分支回歸另以
只接受顯式 constructor 參數的 Settings 執行，不讀環境變數、dotenv 或秘密來源。

## 目前切換限制

主線只讀查詢確認原部署 `d3f206a59888ef3d72732fa30deaa8278ac72cc5` 在
2026-09-12 06:36 UTC 前後仍 armed／running，有四筆已追蹤跨倉／ATR 部位，
沒有觀測到 emergency／lock。這是當時的觀測，不是目前帳戶狀態的持續保證；
也不是完整私有帳戶分頁認證，不能由追蹤數推論「沒有其他曝險」。

重要區別：既有 guard 只對 structural trade 要求 isolated；不能聲稱切換
全域 flag 會自動封鎖每筆舊 cross／ATR 倉。真正明確的重啟邊界是
`SafeDemoAutomation.recover()` 主動解除 armed，而 `arm()` 要求 exchange positions、
pending orders、pending algos 及 tracked trades 全部為空。不能直接恢復 persisted
armed、豁免這項檢查，或為了切換擅自平倉／調整既有槓桿／重寫保護。

因此此 overlay 目前僅是**待套用設定**。本修補工作未重啟原服務，沒有宣稱動態
模式已在運行，也不承諾未排程的背景自動切換。

## 安全套用順序

1. 在沒有 in-flight submit／不確定結果的邊界停止新進場；先保留原服務與
   account／order／protection 記錄，不取消保護、不清空帳本。
2. 以完整帳戶查核與本地 uncertain／reservation 聯集確認空倉、無 pending／
   algo／tracked／uncertain 曝險；維持原保護直到既有倉位自然或明確授權處理完畢。
3. 驗收此最小部署修補，不直接把整個未完成 qualification 分支部署。
   舊 `_twenty_x_reasons` 的 `risk_score or score` 是獨立 downward-gate 缺陷；
   正常 `_risk_profile` 的零分會落到 low cap3，而非 20x，
   不宣稱已證實實際 20x 誤單。修補只允許真正缺值 `None` 回退到 raw score。
4. 驗證完整提案 Settings、保存可回復配置、只改已選定槓桿／風險七欄與明示 caps；
   用已驗來源切換，確認新 runtime schema／服務健康／Live false。
5. 重新做原有 recovery／flat／arming 檢查；驗證 API 回傳五級上限、0.5%／1%
   risk limits、所有保護與舊狀態一致。設定成功不等於已授權略過新進場資格。

本文件是 rollout 計劃與狀態，不是新流程完整 Demo 驗收。
