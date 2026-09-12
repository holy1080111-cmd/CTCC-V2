# R5 公開行情收集：原始資料包，不是交易許可

本次增量補齊四 TF OHLC、獨立 WS ticker、books5 與 open interest，並與既有
REST ticker／mark／funding 組成 bounded one-shot 公開資料包。沒有私有帳戶 API、
沒有接入既有 Demo scheduler，也不產生 G1 PASS、ORDER_ELIGIBLE 或交易許可。
測試與不可變來源／CI 的最終結果另記，不把工作樹測試當成部署驗收。

## 來源與最小介面

| 模組 | 收集／驗證入口 | 原始紀錄 |
| --- | --- | --- |
| `candle_collector` | `collect_candles`／`validate_collected_candles` | 固定 4H、1H、15m、5m，各 TF 獨立 requested count、逐頁 request／headers／complete、raw bytes／canonical hash、confirmed OHLC 全欄 |
| `ws_reference` | `parse_ws_subscription_ack`／`parse_ws_ticker_frame`／各自 validate | 嚴格 ack 和 ticker schema；原始 UTF-8 application message、來源 ts、收訊時間、contract size |
| `ws_collector` | `collect_ws_reference`／`validate_collected_ws_reference` | 自建單一固定 public socket；subscribe→ack→一筆 ticker→有界 close，訂閱內容和完整因果時間 |
| `market_aux_collector` | `collect_market_aux`／`validate_collected_market_aux` | 固定 books sz=5 和 SWAP open-interest，雙端點原始 bytes、單位、來源／返回時間、request identity |
| `public_market_collector` | `collect_public_market`／`validate_collected_public_market` | 四組來源並行；三個独立無憑證 HTTP clients，一個 owned WS；失敗取消其他工作並等待清理；完整資料包保留原子資料，不拼接舊合併 snapshot |

`PublicMarketCollectionPolicy` 明示四個 leaf policies、整批 deadline 和 client
close deadline。每個 leaf 已有界不代表整批最後仍新鮮：封包完成時再次檢查各
component 的原時間與各 TF 最新應收盤 tail。跨收盤邊界缺資料就拒絕，不把完成
時間寫回 source timestamp，也不額外重試來掩蓋第一次失敗。

## 不可放寬的邊界

- HTTP 只使用固定公開 GET；拒絕憑證、環境代理、已知明示代理／mount、
  redirect、底層 transport retries、初始化 cookie 或 event hooks。
  已收到的匿名 cookie 不會帶到後續 explicit Request。
- WS 固定 `wss://ws.okx.com:8443/ws/v5/public`，關閉 proxy／compression／自動 ping；
  不 login、重連、follow redirect、讀取舊 hub cache 或尋找下一筆「可過關」訊息。
- 原始 JSON 必須有界、無 duplicate keys／非有限數值；無效或非預期 schema 即拒。
  所有 exact-model／nested scalar 檢查必須在 serializer 之前，避免非法
  `model_copy` 物件先被自訂 serializer 轉成合法值。
- 每 TF 200–1024 根確認歷史，逐頁最多 300；不能 sort、deduplicate、補洞、
  降低需求或把未確認列變成 confirmed。首頁 header 時間固定該 TF coverage，
  後頁較晚完成不會把 coverage 往後推。原事件 append-only 還須交由 R2 驗證。
- publication barrier 若提供，必須在每個新請求之前；WS 的新 ticker source
  instant 也須越過 barrier。輸入一個舊時間本身不是「本次真 G12」的證明。
- cancellation 必須完成單次、有界、shielded cleanup 後再傳遞；cleanup 失敗
  不能產生成功資料包，也不能吞掉 caller cancellation。
- digest 只驗一致性，不認證 TLS、遠端來源、注入時鐘、帳戶完整度或本次呼叫。
  所有 source／socket binding／execution authority 維持 false。

## 時間與單位

依 [OKX 官方文件](https://app.okx.com/docs-v5/en/)：K 線 ts 是開盤時間；
ticker 和 books ts 是資料生成時間；REST mark／funding／OI ts 為各自的返回
觀測，並非值最後變動時間。Funding settlement／next time 不是 freshness。
WS ack 沒有 source ts；ticker push 沒有 connId，兩者都不能自行補造。

SWAP 的 bid／ask size、book size、OHLC vol、ticker vol24h、OI oi 以合約計；
OHLC volCcy、ticker volCcy24h、OI oiCcy 以本位幣計；OHLC volCcyQuote 是報價幣，
可選 oiUsd 是 USD。原始資料不把 missing OI 當 0，合法明示零 OI 可保留。

現有 `Ticker.volume_quote_24h` 被舊 parser 填入 SWAP `volCcy24h`，命名／單位不符。
本增量因此不直接建舊 `MarketSnapshot`，也不以 0 或 last×base 推估填值。
後續 bridge 必須版本化／表達未知 quote volume、保留明確 base volume、調整
parser／G1 非負檢查及既有 source-hash／JSON 回放相容性，然後才可接 G1。

## 單次公開 WS 診斷

本地工作樹於 2026-09-12 06:31:05.605223 UTC 完成一筆真正公開 WS read，
並從磁碟以嚴格模型重新驗證；完成時 source age 0.138223 秒，明示 max age
60 秒。沒有提供 G12 barrier，沒有帳戶、下單或重試，並非新流程 Shadow／Demo 樣本。
這是傳輸診斷，不是完整封包、時鐘校準、候選政策或來源認證已通過。

本地 `reports/entry-acceptance-20260912/ws-public-20260912-r5.json` 保留完整紀錄：
bundle SHA256 `9f9acd4be0bbd1af40ad4cae59ca03bf3900f789c14e6745d6ea80d90f1f758a`，
檔案 SHA256 `7294bb0af2d5f47c2ead439b850998a7b96f2ed7cf16d52874c7f17ed3ba0c65`。
原始 runtime artifacts 不加入公開 Git。測試中的 MockTransport／fake socket
另行標示為 synthetic，不與此一筆真公開診斷混算。

## 首次完整公開資料包診斷：拒絕，原因未確定

另一次完整四組來源診斷於 2026-09-12 06:54:03.082092–06:54:03.469854 UTC
被 `public_component_capture_failed` 拒絕。沒有 packet、帳戶請求或下單；
當時版本只保留整批錯誤，不能事後猜測是哪個端點、時鐘或資料造成。
`public-packet-20260912-r5.json` 保留原始拒絕結果，SHA256
`8513d361d54c9f9c8208f48cac72ffe5cfd1bde858eb62a84dc9f546d4d44d8b`。
此失敗不是 WS 單獨診斷成功的撤銷，也不能被後續觀測改寫。

為後續診斷增加端點級安全錯誤紀錄：只保留固定 role 與已知 leaf 的安全本地
錯誤碼；任意 remote exception／Pydantic error／raw body 不寫入錯誤紀錄，
取消的 sibling 不列作根因，caller cancellation 仍優先。未知失敗仍 unknown。

加入診斷欄位後的獨立完整資料包嘗試（07:02:54.405718–07:02:54.738995 UTC）
亦拒絕，安全紀錄定位為 `market_aux / public_market_aux_capture_invalid`；
保留在 `public-packet-20260912-r5-instrumented.json`，SHA256
`6a09f2283744b8cf3fe9f0f04958fdad886aec443c01cf9ca22135a67afaaecb`。
另一次有界、無重試的 aux-only 診斷（07:03:40.418655–07:03:40.693835 UTC）
以本地例外位置與不含 input/context 的 validation error 確認該次觸發
`future_component_timestamp`，即 source time 晚於本機 headers receipt。
紀錄 `aux-public-20260912-r5-diagnostic.json` SHA256
`751ec7cd6bef19752d2e5652642d33c9b5d540827f433e8db586dfba41fe66d8`。
這些都是分開的嘗試，不能倒推首次失敗的原因。07:04 UTC 唯讀查核 W32Time
仍為 Stopped／Manual；尚未證實偏差來自本機或遠端，不改時間、補 tolerance、
重寫 source ts 或將拒絕冒作完整公開封包成功。

## 仍未完成

完整 R5 仍缺上述版本化 MarketSnapshot bridge、可信完整 Demo 帳戶與 instrument
sources、分頁 cutoff／reconciliation revision、local uncertain 風險聯集與持久
history／peak。R6 durable 原子 event／risk ledger 和 R7 本次 G12 後 one-shot
runtime、Demo 提交／保護、Notion outbox、forensics 與真正 shadow／soak 均未由本
資料包完成。完整後續要求保留在 [Recheck 計劃](qualification_recheck_plan.md)；
[帳戶來源下一 checkpoint](qualification_account_source_plan.md) 僅設計、未實作。
