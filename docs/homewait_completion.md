# CTCC 本輪交付與回家待辦

2026-09-12 使用者要求「能完成的都完成，不能完成的等我回家」。本輪只完成
不需要本人登入／OS 管理員介入的開發與隔離驗證，沒有自動登入、變更 ACL、
修改時鐘、重啟部署、遷移現行資料庫、重送平倉或開啟 Live。

## 已實作的四個增量

| 範圍 | 本輪實作 | 保留邊界 |
| --- | --- | --- |
| 帳戶資料映射 | [raw account materializer](account_materializer.md)：合約單位、部位、普通單剩餘量、保護單、本地 hold 聯集、完整已提供的歷史／peak 輸入 | 缺資料保留 unknown／incomplete；hash 不等於認證，四類 stamp 不標 complete |
| G12 後新來源 | [one-shot capture](one_shot_capture.md)：本次真正 G12 後 owned 並行 GET/WS，再重播新來源與原候選重查 | 不接受舊 receipt／PASS；帳戶不完整就阻擋，不接 reservation／order |
| 成交後記錄 | [post-submit reporting](post_submit_reporting.md)：固定候選／consumed receipt 的明示 ack → 既有 durable outbox → 分離的單輪 Notion worker | 不確定結果不冒充成交；不重送交易，尚未掛入服務 lifecycle |
| 歷史策略證據 | [regime admission](regime_event_admission.md)：結構反轉／波動擴張的 OHLC 歷史與既有必要條件重算 | 新 sidecar policy 不修改舊 G2 hash；sweep 的 HTF 政策不明仍阻擋 |

本輪包含交叉審查與反例修補：失敗清理時外部取消不可吞掉、opaque／hidden raw
state 不觸發回呼、帳戶 sample 的多來源時間需因果一致。精確程式提交、ZIP／
完整分支歷史 bundle、隔離映像與測試日志保存於 `reports/homewait-20260912`。
驗收數據以該固定來源的最終 `CHECKPOINT.md` 為準；此文件不預填尚未跑完的結果。
`reports` 不推送至公開 Git，不包含在 source manifest／Docker context。

## 等本人回家／環境條件具備

- GitHub：完成正常登入與 repository installation。上一輪 installation 清單為空；
  app「允許所有操作」不能取代帳戶登入。之後只推已驗分支，核對同 SHA 的 CI，
  不繞過拒絕或沿用舊 CI。
- Windows：在受控維護窗按原時間同步政策處理 W32Time，再驗 source／receive
  因果；目前的 future-source 拒絕不能以容差／改 timestamp 掩蓋。原生安全目錄
  存取與長路徑失敗仍須實機驗收；Linux 成功不是 Windows 成功。
- Notion runtime：合法 REST token 與四個實際 property-ID pins；文件工具連線
  不能自動變成 CTCC 程序的 REST credentials。之後驗真送達／讀回與 bounded worker。

## 尚需繼續的工程／實測，不是假稱全部只差登入

1. 可信、同 account cutoff／revision 的全 scope inventory；非 SWAP／未審查合約、
   本地 uncertain、history／peak 持久來源與 completeness 的認證整合。
2. 本次 G12→新資料→完整 recheck→同帳戶原子 reservation→持久 submit intent→
   單次 Demo submit／保護的全部受控路徑。不能跳過尚未完成的帳戶／路徑證明。
3. post-submit producer 與 Notion worker 的服務 lifecycle、真 fill／bill／holding
   price path 的 forensics 接線；寫入結果不明時保留曝險並先對帳。
4. history regime policy 的完整版本化 Gate 整合；sweep HTF 規則衝突需清楚決策。
5. 與上述驗收一致的部署、真 old/new Shadow、受控 Demo soak、四項真實例證與
   最終稽核。新流程真實樣本仍為 0／0，不以合成結果推論 edge 或獲利。

既有部署保持 `d984753`／DB0016；新 migration0017只在隔離測試資料庫驗證。
3／5／8／10／20 動態槓桿上限與既有風險設定不由本輪改寫；保持 Live 禁用。
本輪施工 checkpoint 可封存，但全案與 Notion 第 5、11–15、19–21 步不勾 Done。
