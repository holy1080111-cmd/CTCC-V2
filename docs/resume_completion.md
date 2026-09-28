# CTCC 再續作：整合增量與實際驗收邊界

2026-09-12 使用者要求「剩下的現在完成」。本輪續作包含 CTCC 程式與 Notion
開發紀錄；不把元件完成、合成案例或舊版成績當成全案已完成。

## 本輪新增

- 帳戶來源交叉核對：重播完整固定 packet，比對 balance／position-risk 的
  幣種 inventory 與共同 `eq`、position inventory／side／margin mode／quantity
  與來源 cutoff；有矛盾就不產生 account snapshot。正常 risk anchor 不需要
  未定義的 `cashBal` 欄位。見 [帳戶映射](account_materializer.md)。
- 原子送單意圖：同一 PostgreSQL transaction 消耗既有預留並保存固定原候選／
  client IDs，提交後另開 session 讀回。未知結果不可自動重送；不事後補造舊
  consumption 的 intent。見 [持久送單意圖](submission_intent.md)。
- 原始成交資料分析：submission、account、order attribution、holding OHLC
  各自外部 pin，再重播實際已提供的 fill／逐筆 fee；funding bill 不冒充 accrual
  時間。資料不完整就保留未知，不宣稱真正結案或完整 PnL。見
  [raw trade forensics](raw_trade_forensics.md)。
- 歷史策略的版本化 G1–G11 路徑：重用既有條件／結構／成本／風控 evaluator，
  不改舊 policy/hash 或將舊 Unknown 洗成 Trend。G12／recheck 的版本相容接線
  仍需完成；expansion 的明示 HTF 條件與 sweep 的 HTF 規則缺口仍阻擋。見
  [版本化歷史資格引擎](history_qualification_engine.md)。

來源核對的欄位語意依 [OKX 官方帳戶風險 API](https://www.okx.com/docs-v5#trading-account-rest-api-get-account-and-position-risk)。
matching values、hash 與 pagination 都不證明 authenticated all-account completeness
或同一全域 revision；這些界線保留，不為讓帳戶過關而補零或放寬時鐘。

## 實際環境與驗收紀錄

本輪 Docker CLI 指向 desktop-linux，但 Linux engine pipe 不存在，Docker Desktop／
backend 程序未在執行。W32Time 仍 Stopped／Manual。沒有改 ACL、時間或服務；
不能沿用上輪「Demo 正常」當成本輪最新狀態，也不能將尚未執行的新 PostgreSQL
案例當成通過。未藉啟動共用 Docker 自動恢復可能帶交易動作的舊服務。

GitHub 連線可辨識帳號 `holy1080111-cmd`，但 installation 清單仍空。登入可辨識
不等於該 repo 可寫；本輪未更換凭證、試探其他寫入途徑或聲稱同步成功。
Notion 文件連線與 CTCC runtime REST token／四個 property-ID pins 仍是不同層。

精確 commit／tree、各獨立測試數、跳過與失敗、備份與還原結果保存在本機
`reports/resume-completion-20260912/CHECKPOINT.md`；此頁不預填尚未完成的驗收。
本輪不繼承上一版 8,947 項 Linux 完整回歸，沒有 matching CI 或新版本部署。

## 真正剩餘條件

1. 恢復受控測試環境，執行新版真 PostgreSQL／固定映像完整回歸；需要時另驗
   Windows 原生安全目錄與時間因果。不能以跳過防線當成功。
2. 完整可信 account ingestion／history／peak／scope／revision，以及主指令要求
   的原候選與新執行報價重算、最差抽樣風險原子預留；串起本次
   G12→新來源→完整 recheck→reservation→intent→Demo／
   protection／uncertain reconciliation。舊 receipt 不可變成續行許可。
3. history G12／圖後重查／合法 reversal protection policy 的完整版本化接線；
   明定 expansion／sweep HTF 規則，不擅自放行已被原安全条件拒絕的交易。
4. post-submit／Notion worker lifecycle 與真實送達讀回、實際成交 ingestion 與
   funding accrual／holding-path 完整性、真 Shadow／Demo soak 和四項最終例證。
5. GitHub repository installation、同 SHA CI／部署與最後稽核。新流程真實
   Shadow／Demo 樣本仍 0／0，未開 Live，未執行本輪新交易。

這些未完項目是明確的工程、環境及認證依賴，不會全部標成「只差使用者登入」。

2026-09-23 更正上述第 2 項：先前「全部成交價的風險覆蓋」措辭超出主指令的
最差抽樣預留要求。既有 coverage 明示 false 不變；受控 FOK 邊界與成交後真實
price／RR／quantity／margin／leverage 核對、mismatch EStop 仍是必要驗收。
此頁前段的日期、版本與環境描述是 2026-09-12 歷史紀錄，不是目前驗收結果。

2026-09-28 續作：Docker Linux engine 已可使用，原部署容器仍停止。新建的
隔離 PostgreSQL 已遷移至 0020，Range V5 六個真 SQL 測試通過。Windows 專用
loopback 測試資料庫也完成 fresh migration／drift check；完整新版本回歸尚待
執行。B2a 審查找出的列索引布林／數字混同與跨階段來源時間缺口已修正；同一組
獨立反證由 16 項失敗改為 23 項全通過，舊測試成績不可代替修後驗證。
W32Time 已經人工授權啟動，但實測仍落後交易所與 NTP 約 0.71
秒，可信來源仍 fail closed。詳見 [本輪驗收紀錄](final_completion_validation.md)。
