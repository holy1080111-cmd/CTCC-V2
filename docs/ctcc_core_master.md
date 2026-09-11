# CTCC 核心總規格：123 專案整合

更新日期：2026-09-11。來源為「123」專案的
[CTCC 開發進度](https://chatgpt.com/c/6a94fbd0-34e4-83ee-979c-519b5ecb1c66)。
依 Asia/Taipei 時間，整理範圍為 9 月 9 日 22:51 起的討論，並包含同一段
討論延續到 9 月 10 日 00:28 的決策；不是把凌晨內容誤記為 9 月 9 日。
可追溯的訊息 ID 與機器可讀需求在
[`config/ctcc_core_blueprint.json`](../config/ctcc_core_blueprint.json)。

本文件是統一的核心目標與開發約束，不表示所有策略已實作、驗證、合併或
部署。它不覆寫現有 Safety Kernel、實際 Settings、Arm 或執行程序。
Blueprint 沒有 runtime consumer，不會啟動 Demo／Live、自動 Arm 或提升權限。
此處 Safety Kernel 是既有受控安全／執行邊界的總稱；實際防線分布於
Demo/Live service、automation、risk 與 observability，並非宣稱已新增一個
集中式、同名的 Python 模組。

2026-09-11 使用者另提供 Notion 的進場資格／證據閉環規格，後續施工與
驗收狀態記錄於 [Entry qualification implementation](entry_qualification_implementation.md)。
此新增規格採 12 Gate 與圖後 Execution Recheck；截至 2026-09-12 已推進
資料模型、策略必要條件、保守路由、來源事件／時間／進場區域、多時間框架
SL/TP 選擇及成本／帳戶組合風險離線引擎，正接續同源證據圖與完整 Gate 鏈。
新資格鏈尚未接入 Demo，不能據此宣稱自動下單或
整體驗收完成；最新測試與未完成項目以該施工紀錄為準。

## 1. 核心方向與保留邊界

123 專案作為 CTCC 的研究、設計、外部參考與驗證總索引。CTCC 的目標是
依市場狀態挑選合格策略、產生候選、經數學與風險驗證，再由獨立執行邊界
處理的多策略平台。外部方法先成為研究候選，不直接改寫正式策略。

```text
可信資料／可用時間
  → Regime 與 Model Health
  → 有資格的策略家族 → Candidate
  → Mathematical Core → AI/RL advisory filter
  → 成本後 EV → Portfolio Risk
  → Safety Kernel／Execution Quality
  → 經另外核准的 Demo 或 Live
  → 績效歸因、Challenger 審查、保留或淘汰建議
```

這是目標流程，不是目前 MIE 的 runtime 接線。MIE 的候選仍不得包含
下單數量、槓桿或交易所 payload；聊天示意的 entry/stop/target 只能由既有
受控執行／保護層處理，不能據此放寬 Gate 1/2/3 合約。

## 2. 六個策略家族與目前差距

| 策略家族 | 目標研究內容 | 目前可確認的狀態 |
| --- | --- | --- |
| Trend / Momentum | 趨勢、突破、squeeze、動量 | 已有分析元件；不等於整個新家族通過 OOS |
| Structure / SMC | BOS/CHoCH、OB/FVG、多時框結構 | 已有結構／保護元件；完整 SMC alpha 候選仍須逐項驗證 |
| Mean Reversion | RSI/BB、震盪與回歸條件 | 已有 `range_reversal` 區間反轉元件；完整家族尚未資格化 |
| Statistical Arbitrage | cointegration、spread、z-score、相對價值 | 列為研究待辦，須新增資料與雙腿曝險驗證 |
| Funding / Basis | funding curve、basis、calendar spread | 列為研究待辦；現有 funding 成本處理不等於套利策略 |
| Market Making | spread、inventory、adverse selection | 列為後續獨立執行研究，不套用目前方向性 FOK 的接受標準 |

Regime 必須先決定哪些家族有資格參與，不讓全部訊號無條件投票。
目標對應為 Trend→Trend/SMC、Range→Mean Reversion/StatArb、
High volatility→降風險或否決、Funding dislocation→Funding/Basis、
Low-vol/liquid→經另外驗證的 Market Making。Risk-off 預設 No Trade；
聊天提到的低方向曝險例外，須另行驗證，未在本次放行。
以上是設計分類，不是現有 MIE `MarketRegime` 的可直接載入值；現有枚舉是
`bull_trend / bear_trend / range / high_volatility / transition`。未來 mapper
必須另行驗證，未知與 transition 在本目標規格中預設 No Trade；本次不改
目前分類器或策略行為。

## 3. 數學、AI/RL、EV 與組合風險

保留已存在的 confirmed-candle、因果資料、robust state、conformal coverage、
多時框與 shock 檢查。Mathematical Core 只能保留、降級或否決原始分數；
其 confidence 不是經校準的勝率，未經 OOS 的結構／動量資料仍屬 auxiliary。

AI/RL 的目標能力限定為 VETO / DOWNGRADE / RANK；RANK 只能排序已合格候選，
不得抬升分數、風險、槓桿、保護參數或下單權限。這是未來介面約束，
不代表已訓練 RL 模型或存在 runtime consumer。

EV 的目標為同一單位、同一持有期與同一樣本定義下：

`P(win) × average_win − P(loss) × average_loss − fees − funding − spread − slippage`。

若盈虧已扣除某項成本，必須標記並避免重複扣除。不能拿未校準 confidence
當 P(win)，也不能因高勝率跳過成本、尾部損失與回撤檢查。
現有 MIE DecisionCandidate 已有 net-EV 邊界；完整且具資料證據的 EV
估計器、correlation/regime/strategy-family risk 仍是後續研究工作。
既有 correlated-position count gate 仍須保留，不能用這份未完成的研究取代。

保留目前單筆／組合風險、margin、protection、reconciliation、idempotency、
weekly-loss、drawdown 與 Emergency Stop。聊天中的簡化 1x/2x/3x 範例不是
新的全域參數：repo 另有受控 structural-risk 路徑，應以其原有設定與接受
條件為準，本次不修改任何數值上限。

## 4. 外部系統與交易成果檔案庫

收集範圍維持使用者指定的四項：別人的系統、核心邏輯、績效、完整流程。
每套研究檔案的目標結構為：

```text
research/external_strategies/<strategy_id>/
  strategy_profile.json
  logic.yaml
  performance.json
  source_manifest.json
  ctcc_comparison.json
  trades.csv                  # 原作者確有公開時才加入
```

此結構已有第一版[離線 metadata 輸入合約](strategy_evidence_intake.md)，
能檢查未知欄位原因、材料聲明、作者績效上下文與零權限邊界；仍未建立
實體五檔案、逐筆交易或完成來源驗證。
`external_evidence` 保存作者材料；`ctcc_validation` 保存 CTCC 自己重算、
OOS、Demo 的結果。兩者不可混用。

採用 E0–E6 作為資料可取得程度的索引：作者宣稱、回測報告、逐筆交易、
可重現程式、OOS、Forward/Paper、可供獨立核對的 Live record。等級不是
真實性認證，也不直接映射 MIE validation level；CTCC-E 必須另有 CTCC
重算過程與 hash 證據，且不自動產生 predictive 或 execution authority。

每份紀錄應分開保存：作者宣稱／CTCC 重算數值、回測／OOS／Forward／Live、
instrument、timeframe、sample window、樣本分母、成本假設、原始網址、作者、
取得時間、檔案 hash、license 與缺資料原因。未公開用 null 加原因，不填 0
或猜測。每筆 trade 勝率、盈利 walk-forward window 比例、promotion 門檻
不能當成同一指標；Market Making 另看 spread capture、inventory PnL、
adverse selection 與 fill quality。

聊天列出的 KA-MATS、Adaptive Regime Switch Pro、RL Tradingbot 2、
Crypto Statistical Arbitrage、NORN WEAVE、ETH Momentum Breakout，先保存為
待查原始來源的研究線索。此次讀取的聊天只有舊引用標記，未提供可重新
核驗的原始成果網址，因此不把其中的勝率／PF／回撤數字匯入已驗證資料庫，
也不沿用「CTCC 比外部更強」或完整度百分比作為工程證據。

## 5. 24/7 Demo 與 MIE 雙軌

使用者提出持續掃描、符合條件才交易，不固定成每天一次。這已納入
既有策略的 Demo Forward 目標，但 24/7 不是持續強迫下單、無限 Arm、
跳過 submission cap，亦不是穩定獲利的證明。

repo 的 Continuous Demo 是明確 opt-in；它會跳過 daily loss、每日交易數、
consecutive-loss 與 cooldown entry gates，並非只改掃描頻率。因此啟用前
仍須逐項接受原有 weekly-loss／drawdown／protection／portfolio／Arm／
reconciliation／Emergency Stop 防線。此次整理不修改 Settings 或部署 .env。

兩條路徑分開記錄：

- A：已存在、另外核准的 Demo 策略，完成隔離／啟動／read-only preflight、
  dry-run、受控 submission 與保護核對後，才進行持續 Forward。
- B：MIE Gate 3，固定 offline/shadow、computational、runtime=0、execution=false；
  新策略先經真實來源資格、candidate/protocol 封存、fresh holdout 與獨立審查。

現有 Demo 的樣本不能替代新 MIE candidate 的 OOS 證據；MIE 的合成測試
通過也不能自動啟動 Demo。24/7 運行與 Gate 3「一次正式 holdout evaluation」
是不同限制，不能互相取消。50／100／200–500 筆只是聊天中的觀察節點，
不是統計充分性或升級保證。

## 6. 開發順序與接受條件

1. 完成本機既有 Gate 3 證據鏈與批次 plan-binding 測試、manifest、發行審查。
2. 取得獨立來源／row availability 證據，再完成真實批次與計畫連結。
3. 建外部 Strategy Evidence Pack 的輸入規格與來源審查；先從可重現且
   license 明確的候選開始，不以聊天績效或最高勝率挑選。第一版純 metadata
   合約已實作；真實來源／license 審查、原始檔案綁定與重算仍待完成。
4. 在過去 development/validation 內建立 frozen candidate，記錄所有 trials、
   costs、purge/embargo，完成真正未曝光 holdout 與獨立 OOS review。
5. 另外驗證 EV、correlation/family risk 與 regime routing；經獨立 Gate 審查
   才能接 read-only shadow，不能直接接交易。
6. 分別核准 Demo Forward、Champion/Challenger review、Soak，Live Canary 與
   final acceptance 更需另行授權。Challenger 表現失敗也保留，不自動替換
   champion、不自動停掉策略或處理既有持倉。

目前 Docker 隔離／恢復與真實 OOS 仍有未完成項，詳見本機 reports。
GitHub 發行須以對應 commit 的 CI／PR 記錄為準；舊聊天「可以啟動」是
操作建議，不是此刻服務健康的證據。
本核心整合的完成標準是來源可追溯、規格與現有邊界一致、機器清單測試
通過，以及不產生新的 runtime 或交易權限。
