> 2026-09-21 更新：聯發科已改為有來源、明列假設的 2027 情境估值（2330／2635／2945）；個股研究定錨不受每日規則覆蓋。異常規則估值分流為待研究，公開買價留空，避免將模型問題解釋成股價一定過高。詳見 STOCK_CLOSEOUT_20260921.md；其他方法與排程維持，最新研究最早於10/5到期。

# 台股服務：架構與運作（2026-09-18）

使用者目標：看網站或 Discord 就知道該不該買、買哪幾檔、用什麼價位買。
日常計算全部用 Python，不呼叫 LLM。真實下單永遠由使用者決定。
**判斷規則由使用者（透過 Codex）調整**；本文件指出規則在哪裡，改規則時不需要動架構。

## 每日流程（舊 Mac launchd，`tools/stock_service.py`，台北時間）

| 時間 | 任務 | 做什麼 |
|---|---|---|
| 每日 06:30 | refresh | 母表、日曆、財報、風險 → 公開收盤 → 關注股結構（非必要）→ **全市場資料** → **全市場掃描** → 成效評估 → 匯出 → 更新網站 |
| 交易日 08:50 | notify | 富果查候選清單的**試撮價** → 匯出 → 快照 → 記錄建議（**不發訊息**） |
| 交易日 08:55 | recheck | 同上，並送出**當天唯一一則**盤前訊息（此時的試撮價最接近開盤） |
| 每日 18:00 | close | 同 refresh（證交所 T86 法人資料約 15:00 後才公布，所以不能排在 14:30） |
| 週六 10:00 | weekly | 本週回顧＋成效追蹤 |
| 週日 20:00 | outlook | 下週觀察清單（全市場，只推薦有研究的） |

- 時間防護：launchd 補跑的事件若超過排定時間 5 分鐘，直接略過，不發過時的早報。
- 單一執行鎖 `service.lock`，同一時間只跑一個任務。
- **緊急停止**：`touch ~/kid-agent-data/stock-radar/PAUSED` 會停止所有任務；`rm` 即恢復。
- **失敗通知**：notify、refresh、close、weekly、outlook 失敗都會送出【系統異常】到 Discord（`alert()`）。
- **非必要步驟**：`price_structure` 和 `track` 失敗只記錄，不會中斷流程（`secondary()`）。
- 保留期限：資料庫備份保留最近 14 份，盤前快照保留 120 天。
- 重開機：舊 Mac 已設定自動登入 kid，登入後 LaunchAgents 會自動恢復。

## 資料流

```
證交所 MI_INDEX / BWIBBU_d / T86（依日期，一次請求涵蓋全市場）
  → market.py 寫入 market_days（成交 250 天、本益比 250 天、法人 10 天）
  → market_scan.py
      1. 成交量門檻（20 日平均張數）            ← 第一關，冷門股不往下走
      2. 技術面 → fact `technical`
      3. 估值：個股 valuation_engine.compute；ETF etf_valuation
      4. store.publish_rule_valuation（review_mode=rule-engine-v1，不宣稱已研究）
      5. 08:50 候選清單 → meta `morning_candidates`
  → buy_plan.store_plan（以收盤價判斷：買進區／不追／等止穩／跌破…）
  → 08:50：富果試撮 → domain.decide（行情、五檔、ETF 溢價）→ buy_plan.gate_signal
  → research_brief.morning（Discord）／public_export + docs/radar.js（網站）
```

## 判斷規則在哪裡（Codex 改這些即可）

| 規則 | 位置 | 目前值 |
|---|---|---|
| 成交量門檻 | `market.py` `LIQUIDITY_LOTS`、`LIQUIDITY_DAYS` | 1,000 張／20 日（使用者確認） |
| 歷史長度 | `market.py` `KEEP` | 250 個交易日 |
| 個股合理價 | `valuation_engine.compute` | 過去四季 EPS × 一年本益比中位數，不加成長 |
| 本益比不可靠改用淨值 | `valuation_engine.MAX_FAIR_PE` | 中位數 > 50 |
| 金融、循環產業用淨值 | `valuation_engine.BOOK_INDUSTRIES` | 金融、航運、鋼鐵、塑膠、水泥…… |
| 三段價位 | `valuation_engine.BANDS` | 合理價的 65%／75%／85% |
| 最少樣本 | `valuation_engine.MIN_SAMPLES` | 120 |
| ETF 價位 | `market_scan.etf_valuation` | 一年收盤價的 p10／p25／中位數 |
| ETF 溢價上限 | `domain.ETF_PREMIUM_MAX` | 1% |
| 排除的 ETF | `market_scan.DECAYING_SUFFIX` | L、R、U |
| 評價過高／估值脫節 | `buy_plan.GAP_LOW`、`GAP_HIGH` | 買進上限 < 60 日低 × 0.85／合理價 > 60 日高 × 1.3 |
| 等止穩 | `market_scan.technical` `requires_stabilization` | 收盤低於月線且近 5 日外資＋投信賣超 |
| **推薦需要研究** | `research_brief.researched` | 個股需有效的 research_detail（thesis、risks、效期）；ETF 依 ETF 規則 |
| 富果名額 | `market_scan.CANDIDATE_CAP`、`STOCK_SLOTS`、`ETF_SLOTS` | 30（個股 20、ETF 6），有研究的優先 |
| Discord 推薦數 | `research_brief.MAX_STOCKS`、`MAX_ETFS` | 5、3（使用者確認） |

以上除了標注「使用者確認」的項目，都是 Claude 依 Skill 規格暫定的，尚未回測。

## 分工（2026-09-26 使用者定案）

- **Python 規則引擎是主體**：掃全市場、決定今天哪些可以買、給價位。推薦不需要 LLM。
- **LLM 只加深度**：對規則已選出的少數標的寫分析，顯示在 Discord 的「深度：」行與網站的「深度分析」標籤。沒有分析不影響推薦。
- 這一點取代 `.agents/skills/tw-stock-price-analysis` 中「08:50 只列研究有效標的」的寫法（Skill 本身規定使用者最新決策優先）。
- 能不用 LLM 就不用：每天最多 5 檔（`research_llm.LIMIT`）、只研究已在買進區或一步之遙的標的、研究保留 21 天不重做。

## Wake Engine（2026-09-26，對齊 STOCK_AGENT.md 第 6 節）

`research_llm.wake()` 是 deterministic 的喚醒判斷，沒有事件就不呼叫模型：
- `entered_buy_zone`：計畫為 conditional／wait_stabilize 且尚無有效深度分析。
- `setup_broken`：已有分析但計畫轉為 broken／overvalued（跌破支撐或評價過高）。
- `valuation_moved`：便宜程度與做研究當時相差 10 個百分點以上（`MOVED`）。
- 其餘一律不喚醒；已分析且設定未變的標的會沿用 21 天。安靜日 = 0 次呼叫。
每次喚醒原因寫入 `research_detail.wake_reason`，每天最多 5 檔。

## 每日自動研究（2026-09-26 起）

- 排程：每天 18:00 收盤掃描後，`stock_radar.research_llm` 挑選並研究，失敗不影響網站與通知（`secondary()`）。
- 選股（無 LLM）：由上面的 Wake Engine 決定，同級以「比自己一年常態便宜」排序，每天最多 5 檔。ETF 依 ETF 規則，不需個股研究。
- 模型：透過共用 Runtime 的 `integrations/research_cli.py`（Anthropic claude-sonnet-5），使用**獨立帳本** `.runtime/stock-research.sqlite3`，每日上限 12 次，不會動用 Crypto 的 48 次。每檔每天最多嘗試 2 次。
- 提示只含本 repo 已查證的數字（收盤、估值與其說明、月線／支撐／壓力／60 日高低、近五日法人、最近一季 EPS、月營收年增）。明確禁止引入其他數字或新聞。
- 輸出欄位對齊 STOCK_AGENT.md 第 10 節可由模型判斷的部分：description、thesis、why_now、**why_not**、**next_trigger**、catalysts、risks、invalidation。DECISION／ENTRY ZONE／IDEAL LIMIT／TARGET／價格失效條件由 Python 規則提供，不花 token。
- 回覆驗證：欄位與字數、清單數量（多的截斷、少的退回）、**數字必須來自提供的事實**（允許四捨五入與由事實推導的比率，否則以 `unverified_numbers` 退回）。過長文字在句號處截斷，不浪費已付費的呼叫。
- 寫入 `research_detail`（含 `research_mode=llm-v1`、provider／model、成本估算、facts_used），效期 21 天。`researched()` 只決定要不要顯示深度內容，不影響推薦。
- 實測：每檔成本估算約 $0.031，3 檔約 $0.09／天。

## 個股研究（推薦的必要條件）

- 存放在 fact `research_detail`（thesis、risks、buy_reasons、as_of、valid_until……）；匯入流程見 `research_brief.install`。
- 目前只有原本 10 檔有研究，**2026-10-06 起陸續到期**；到期後個股不會被推薦，網站和 Discord 會提示「有效個股研究 N 檔」。
- 規則篩選通過但沒有研究的標的，列在「待研究（不是推薦）」。研究流程要自動化（LLM）還是人工，等使用者決定。

## Discord 訊息的狀態（08:55，一天一則）

- 【今天可考慮進場】：有研究（或是 ETF）＋試撮通過。每檔列出：掛價、最高價、價位依據、看好、風險、有效到幾點。
- 【今天不進場】：試撮檢查確實有執行，但有研究的標的都沒有通過。
- 【無法確認】：候選清單超過 30 小時、今天沒有報價、富果失敗改用 MIS（分不出試撮價），或查到的不足 8 成。**這不是不進場。**
- 【系統異常】：排程出錯。

網站：「今天的結論」與 Discord 用同一套規則，但依據的是收盤計畫；開盤前的最終確認只在 Discord。資料超過 30 小時沒更新時，網站會顯示「系統可能停止運作」。

## 部署與測試

- 主力 Mac：`./scripts/deploy_patch.sh <patch> "<訊息>" [--site]`。流程：備份 → 套用 → 舊 Mac 上跑全部測試（Python＋`node --test tests/test_frontend.cjs`）→ 失敗自動退回 → commit →（加 `--site`）推送網站。
- 網站的 JS 版本號＝radar.js SHA-256 前 12 碼，寫在 `docs/index.html`。
- 首次全市場回填約 510 個請求（每次間隔 2.5 秒）；之後每次排程只需 3 個。

## 收盤檢討（每天 18:00 之後，純 Python）

`stock_radar/close_review.py`：以當天的全市場 K 線檢討當日建議——觸及掛價幾檔、收盤相對掛價的平均、未觸及的最低價、未列入建議但當天漲逾 3%（`MISSED_MOVE`）的標的。發到 review 頻道並存入 meta `close_review`。不宣稱「不追是正確的」，只陳述市場實際走勢。

## 盤中重新判斷（交易日 09:05／10:00／11:00／12:00／13:20，純 Python）

`stock_radar/intraday.py`：用證交所免費即時報價（盤中沒有試撮判讀問題，不耗富果額度、不用 LLM）重算計畫。事件有三種：
- `reached_entry`：現價觸及承接參考價。
- `support_lost`：現價低於研究支撐 2%（`DROP`）。
- `entered_zone`：原本不追的標的回落進入買進區。

**一天最多一則盤中訊息**：只有出現 `URGENT`（`support_lost`、`reached_entry`）時才發，發出的那一則會把當下所有事件一起列出（含 `entered_zone`）；之後同一天不再發（meta `intraday_seen`）。只有 `entered_zone` 的日子完全不發，留到隔天盤前清單。
五個時段照常檢查（免費報價、不耗額度），只是不再各自發訊息。launchd label `local.kid.stock.intraday`。

## Discord 頻道路由

`stock_radar/channels.json` 可分別指定 daily／alert／review／status 的頻道 ID；未填則全部回到既有頻道。盤前用 daily、盤中與系統異常用 alert、收盤檢討與週報用 review。

## 已知限制

- 上櫃（TPEx）股票與 ETF 尚未納入；ETN 不評價。
- 7/10 這天日曆記為交易日，但證交所查無資料，每次排程會多重試 2 個請求，無害。
- 富果免費方案的額度沒有公開文件；每天 2 次 × 30 檔 × 2 個請求。
- 規則沒有回測；成效追蹤（`track.py`，5／20／60 日）會開始累積資料。
