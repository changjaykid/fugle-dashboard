# Stock Radar 排程計畫（尚未啟用）

狀態：**草案，未接上任何 cron（system crontab 或 OpenClaw cron）**。這份文件
只是把 STOCK_RADAR_SPEC.md 的每日/每週時間表對應到實際 CLI 指令，供 Kid /
Codex 審查；啟用前需要：
1. 全市場 sync-quotes 的真實耗時/涵蓋率驗證（目前只測過 51 檔 watchlist）
2. Fugle 401 解決或明確決定「V1 不做即時掛價，只做估值研究」
3. Kid 明確指示「可以接上排程」

## 對應表（依 STOCK_RADAR_SPEC.md 時間表）

| 時間 | 任務 | 指令 |
|---|---|---|
| 清晨（如 06:30） | 母表更新 | `python3 -m stock_radar.cli sync-universe` |
| 清晨（母表後） | 財報/月營收/本益比 | `python3 -m stock_radar.cli sync-financials` |
| 清晨（母表後） | 停牌/處置風控檢查 | `python3 -m stock_radar.cli sync-risk` |
| 清晨或每日一次 | 交易日曆更新（**2026-09-10 已實作**，見下方說明） | `python3 -m stock_radar.cli sync-calendar` |
| 08:30-08:50 | 全市場報價（**尚未做全市場版，只有 --watchlist-only**） | `python3 -m stock_radar.cli sync-quotes` |
| 08:50 | 匯出 + Discord 摘要 | `sync-quotes && export --out docs/radar.json --mode live && notify-summary --radar-json docs/radar.json` |
| 08:55 | 僅重大變化才通知（**尚未實作 diff 偵測，V1 暫缺**） | TODO |
| 09:05 | 用收盤資料源二次確認（STOCK_DAY_ALL / TPEx） | `stock_radar.quotes.fetch_daily_close_all()` / `stock_radar.tpex.fetch_otc_daily_close()`（尚無獨立 CLI 子指令） |
| 每週日 | 全市場輕量檢查 | 沿用 sync-universe + sync-financials，尚無「只挑有證據變化」的差異偵測 |

## 交易日曆（2026-09-10 已實作，取代手動 --assume-market-open）
- 資料源：TWSE 官方 OpenAPI `holidaySchedule`（免費、不需金鑰），回傳當年（ROC 年度）完整休市/補假清單。
- `stock_radar/calendar.py`：`fetch_holiday_schedule()` 抓取並過濾出真正休市的日期（判斷關鍵字「放假」「補假」「市場無交易」；「最後交易日」「開始交易日」這類描述相鄰交易日的列會被排除，不會誤標成休市）；`is_trading_day(date, schedule)` 再疊加週六日一律休市的規則。
- `cli.py sync-calendar` 把結果快取進 SQLite `metadata` 表（key=`trading_calendar`）；`cli.py export` 預設會讀這份快取判斷今天是否為交易日，不再需要手動 `--assume-market-open`。
- **Fail-closed 設計**：若快取的日曆沒有涵蓋今天的 ROC 年度（例如換年後忘記重新 sync-calendar），`export` 不會假裝「開盤」，而是在 `health` 標記 `blocked` 並提示要跑 `sync-calendar`。
- `--assume-market-open` 仍保留，作為手動測試/研究時繞過日曆檢查用，正式排程不應該依賴這個 flag。
- 已知限制：這支 API 目前只回傳「當下」的 ROC 年度資料（測試過帶 `?year=` 參數無效），代表每年跨年後都需要重新 `sync-calendar` 才能拿到新年度的休市清單；建議排程加一個每日或每週跑一次 `sync-calendar` 的頻率，成本很低（單次呼叫，27 筆資料）。

## 已知未實作（誠實列出，不假裝完成）
- 全市場（2,335 檔）sync-quotes 尚未做批次/節流測試，V1 只驗證過 51 檔 watchlist 版本。mis.twse.com.tw 對單次請求的網址長度/symbol 數量上限未知，全市場需要先做分批測試。
- 08:55「只有重大變化才通知」需要保存上一輪 radar.json 快照做 diff，尚未寫。
- 重啟後排程恢復、失敗重試、通知去重：尚未設計，需等接上真正的 cron 執行器後才能測。

## 備份

`python3 -m stock_radar.cli backup --out <path>` 已實作並測試（見
tests/test_cli.py TestBackup），內部用 SQLite `.backup()` API + 完整性檢查
（`PRAGMA integrity_check`），失敗會 raise 而非靜默產生壞檔。啟用排程時建議
每日跑一次，路徑建議 `_archive/stock_radar_backups/YYYY-MM-DD.db`（沿用現有
`.gitignore` 的 `_archive/` 慣例，不進版控）。
