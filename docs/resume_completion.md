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

同日第八個 checkpoint 為 `caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`。
其完整 Windows 回歸正在獨立測試服務執行。首次 Docker 完整驗證已明確 FAIL：
Windows 目錄 build context 將全部 731 個來源檔的 executable bit 改變，雖然
bytes 全部相同仍被 exact COPY 防線拒絕。已修正 runner，直接使用同一份已驗證
的原始 tar bytes；Linux 41 項針對性測試通過，新完整隔離驗證仍需終態結果。
已失敗的原紀錄不改成 PASS，新 runner 的 hash 與 target source SHA 分開保存。
目前又有未封存的 public initial capture／account observation／control-bound
ledger 施工；第八版回歸結果不涵蓋這些後續變更，最終仍須重新封存與驗證。

同日稍後更正當前環境狀態：兩次非預期重啟中斷完整測試。Windows event 86
明確記錄第二次為嚴重熱溫關機；Docker 與長回歸保持停止。使用者已改善通風，
MyASUS 也實際顯示兩個風扇有轉速，但持續負載穩定性尚未驗收。先保存 747 個
未提交來源檔的 exact-byte checkpoint，並繼續低負載原始碼修正；中斷工作不算
通過。校時修正後、第二次重啟前曾有兩次原生／OKX 時間因果雙通過，新開機後
仍須重新量測。DB0021 部分 schema／migration 案例已完成，範圍及原 runner
FAIL 原因另見 [本輪驗收紀錄](final_completion_validation.md)，不借作最終 PASS。

22:15 後續確認：MyASUS 僅風扇診斷完成、零問題；新開機後原生時鐘與直接
OKX 時間因果探測均通過。Docker 的實際 VM 限制已讀回為 2 CPU／約 3 GB，
原部署服務仍停止。已恢復單核心、45 秒上限的短元件驗證，包含原始候選、交易
規格與新版資金費率語意；各自凍結版本及實測結果見本輪驗收紀錄。長套件仍未
完成，不能將短測試或風扇診斷当成持續負載／交易上線驗收。

2026-10-01：後續檢查確認 Windows venv 啟動器另生實際 Python worker，
舊 subprocess timeout 尚不能證明整個程序樹已清理。新增純作業系統驗證
仍保留同一程序數量上限、具體 Job membership、私有放行與期限。第三次
量測讀回本次額外成員的實際 `conhost.exe` 身分及父程序；原 single-S
檢查正確拒絕放行，舊失敗仍保留。另版修正子程序主控台啟動方式與真實
程序參考的釋放順序，未豁免未知程序或放寬 Job 防線。其後四種純 OS
情境（正常退出、逾時、主控程序崩潰、強制清理）已逐項實際通過；完整
程序樹退出均在原外部 5 秒期限內，未使用外部救援。原 reader 型別拒絕
仍保留，另版只接受精確 Int32／Int64 零值後以全新情境重驗。77 個來源／
證據檔案的原始雜湊另見本輪紀錄；這不證明原應用測試或完整回歸通過。
應用監督接線仍在獨立 source-only 審查，舊應用驗證入口保持關閉；不把
新 OS 證據追溯套用於先前正常完成的短測試。

Gate 3 定向來源稽核確認，既有 Dev/Val archive 收據是 2026 年取得，
不能證明 2024/2025 年逐 row 可用性；既有 retrospective holdout 已曝光。
真正 PIT、Candidate/trial seal、多個未曝光窗口與唯一正式評估仍未驗收。
帳戶逐筆費用／部分損益的跨日歸屬正在外部精確來源上實作；缺少 funding
事件連結、完整財務尾段、streak seed 或 HWM 時仍維持 unknown，沒有
完整 PortfolioRiskSnapshot 或新增交易權限。細項及實際 hash 見本輪紀錄。

2026-10-05 續作狀態更正：目前 canonical 工作樹仍在
`develop/v2-final-completion-20261005`，HEAD 為
`caa1588bb6dd8174c0f4eea4ceb9236f0004a7c9`，HEAD tree 為
`2818ed63896dbbbb7341d15550210c6837d6ac1b`；不可用舊 main 覆蓋。先前本頁
所述 W32Time Stopped／Manual 是較早的狀態。本次讀回服務為 Running／Automatic，
系統時區為 Taipei Standard Time；三次直接 OKX 公開時間請求都符合本機請求開始、
交易所時間與回應完成的因果順序，單調時鐘與牆鐘差也通過。本項只驗證時鐘，
不等於完整公開市場收集器驗收；每次非預期重新啟動後仍須重測。

筆電先前因嚴重過熱自行關機；使用者已改善通風，但風扇狀態仍不確定。故本次
只做低負載驗證，暫停長時間回歸、Docker 與資料庫全矩陣。Windows 結構風險
單元套件 14/14 通過，Demo／Live 送出邊界定向套件 133/133 通過，原始報告及
雜湊見 [本輪驗收紀錄](final_completion_validation.md)。這些是局部測試，不代表
Demo 成交、完整帳戶快照或 Live 驗收。Notion 原規格的四項真實 evidence 仍為
0/4；沒有登入帳戶、下單、Notion 寫入或 GitHub 發佈。R7→reservation→intent
仍未接成可授權的完整執行鏈，交易送出權限保持 fail closed。

本次文件變更及 manifest 由後續 overlay checkpoint 保存；來源 commit／tree 未變。
尚未完成的 Gate 3 sealed OOS、Gate 4 shadow、Economic OOS、stress／chaos、
Demo acceptance、Micro Live 與最終 release 均不得標為完成。

2026-10-05 低負載續作：完成一筆 OKX 公開市場診斷擷取及原始資料離線回放，
範圍是 BTC-USDT-SWAP 的 4H／1H／15m／5m candles、ticker、mark、funding、
books5、open interest 與公開 WS ticker。198 個原始／事件檔案、126-event
journal 的 replay 通過；capture readback 與所有 acceptance flags 見
[本輪驗收紀錄](final_completion_validation.md)。此結果不代表 source authenticity、
measured PIT、完整 account、G1-G12 或任何執行授權，沒有登入或送單。筆電風扇
持續負載狀態未確認，所以仍不啟動長回歸、Docker 或 DB 全矩陣；需要的下一個低
風險工作是繼續 source-only 核驗 R5 account completeness／pagination 與 R7、
reservation、intent 最終授權邊界，保持未知即拒絕。


同日 HighVol/Momentum 安裝器續查：reviewed package 的來源 identity 與 PowerShell
parser 通過，disarm/restart guard 6/6 通過；9/15 歷史原檔仍保留 `$Mode:` parser
錯誤以供追溯。五個 HighVol policy 案例因 canonical 尚缺 policy module 而未能
收集，並非通過；沒有執行安裝器或部署。canonical integration、來源 pin 相容性、
idempotent install/reinstall/upgrade/rollback 仍未驗收，詳見
[本輪驗收紀錄](final_completion_validation.md)。

2026-10-05 低負載續測：V5 account capture parser 與 owned collector 定向套件
696/696 通過，JUnit 位於 `validation-results/windows-account-source-v5-20261005.xml`。
這些測試是離線／合成驗證，沒有私有帳戶請求，不代表帳戶可信或完整。官方 since-2021
bills quarterly archive 仍未接線；規格中 `result=false` 的等待時間、Q2 日期範例矛盾，
以及短效 `fileHref` 的保密處理已明記為 fail-closed 條件。整體 account source 與
PortfolioRiskSnapshot 繼續未通過。

續作新增季度 bills archive 的離線 response／ZIP／CSV 驗證器；13 個合成案例通過，
包含 2021 Q1 邊界修正、`billId` 倒序／重複拒絕、季度窗口檢查與 `fileHref` 僅存雜湊。
初次 Q1 邊界測試失敗的報告仍保留。此模組沒有 HTTP 或 credential 路徑，也沒有接上
目前固定 GET 的 account collector、durable receipt 或 lifecycle materializer；帳戶完整性
仍 DENY。細節與報告 hash 見 [本輪驗收紀錄](final_completion_validation.md)。


同日已對照 OKX 最新官方文件重核帳戶區域與頁鏈：Global／US-AU／EEA／
Turkey 分別精確路由，cursor 保持 ordId／billId／algoId 分離，短非空頁之後仍要
取得空 terminal page。現行 admission proof 仍只接受 Global，Live client 只支援
Global／EEA；本機帳戶註冊區域尚未驗證，沒有私有 API 請求。官方 since-2021 bills
archive 也不在目前 34-stream plan；28 日 history query 不足以證明 lifetime loss
seed、funding accrual 或 HWM，仍不 materialize complete PortfolioRiskSnapshot。

補充核實：since-2021 bills 不是一般 cursor page，而是逐季 POST 申請、GET 取得
非同步狀態與短效下載連結，再驗 raw archive/CSV 與季度完整性；目前 collector 尚無
這段 acquisition/readback/reconciliation。apply `ts`、CSV row `ts` 與 funding accrual
time 不可互換，不能用它宣稱 funding 或 realized outcome 已完整。官方規格與缺口已記入
[account source 文件](qualification_account_v5.md)及[驗收紀錄](final_completion_validation.md)。

同日 R7→reservation→intent 唯讀 source review：control-bound reservation 和
consume/intent 本身有單一 account-scoped 交易、current revision/control/expiry 重讀、
風險重算、原子 journal，再由另一 DB session replay/readback；但目前沒有把
`publish_and_recheck(...)`、完整可信 public/account producer 與這些 primitive 接成
可授權的 coordinator。`DispatchOwnership.require_ready` 仍固定 DENY。Demo transport
邊界 52 個合成案例通過，3 個核心 dispatch-deny 案例通過；完整 dispatch 合約檔因
刻意等待/競態案例超出低負載範圍而在 46% 中止，未計作通過。詳見
[本輪驗收紀錄](final_completion_validation.md)及其 JUnit 雜湊。未送任何帳戶請求或訂單；
post-G12、reservation/intent production integration 與 Demo/Live acceptance 仍未通過。

同日 Windows outbox publisher 定向驗收：原生 Windows pinned publish/readback 與
no-clobber 通過；注入 late `WinError 32` 後，state journal 保留、成功標記不存在、
無法重建或 dispatch。7 個 targeted cases 與 2 個 native readback cases 通過；完整
outbox、Linux native storage、worker crash matrix 及 Notion delivery 仍未驗收。詳見
[本輪驗收紀錄](final_completion_validation.md)。

## 2026-10-05 continuation correction: bounded PostgreSQL validation completed

The earlier entry stating that Docker/PostgreSQL checks were paused describes the state before the bounded isolated runs on 2026-10-05. With the host kept to a single 0.5-CPU, 768-MiB disposable PostgreSQL container per run, the fresh database upgraded through 0021 with no Alembic drift; 36 reservation/submission-intent integration cases and 34 actual migration downgrade/re-upgrade/locking/retention cases passed. Both temporary containers were removed. The existing recovered PostgreSQL container and its persistent volume were not used. Exact JUnit hashes and source pins are recorded in `docs/final_completion_validation.md` and the current validation checkpoint.

A separate 10-case account portfolio runtime unit selection passed on the current documented working tree. The earlier 12-case account observation index Linux report also remains historical evidence; its longest test took 251 seconds and it is not being treated as a fresh full-source regression. These bounded results do not establish the entire PostgreSQL matrix, authenticated account completeness, G1-G12 production dispatch, OOS, Demo, or Live acceptance.

At the latest read-only GitHub check, `main` remained `d3f206a59888ef3d72732fa30deaa8278ac72cc5`, the public evidence branch remained `d9847539b5d5d03e3af385488ae11bb019fd4858`, and the local completion branch had not been published. No CI run, PR, merge, or release was performed. `DispatchOwnership.require_ready()` remains fail closed and no account request or order write occurred.

2026-10-06 Windows publisher follow-up: the earlier same-directory hard-link
implementation was replaced by `CREATE_NEW` final-name reservation. The focused
native sync/no-clobber and late-journal-retention checks passed, while the full
Windows storage module still has four fixture-creation failures (junction and
hardlink permissions); async public-capture integration did not start because
the local event loop blocked during socket-pair initialization. See the dated
acceptance entry for exact reports. The earlier 2026-10-05 result is retained as
historical evidence, not treated as proof for the current implementation.
