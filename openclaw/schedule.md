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
| 08:30-08:50 | 全市場報價（**尚未做全市場版，只有 --watchlist-only**） | `python3 -m stock_radar.cli sync-quotes` |
| 08:50 | 匯出 + Discord 摘要 | `sync-quotes && export --out docs/radar.json --mode live && notify-summary --radar-json docs/radar.json` |
| 08:55 | 僅重大變化才通知（**尚未實作 diff 偵測，V1 暫缺**） | TODO |
| 09:05 | 用收盤資料源二次確認（STOCK_DAY_ALL / TPEx） | `stock_radar.quotes.fetch_daily_close_all()` / `stock_radar.tpex.fetch_otc_daily_close()`（尚無獨立 CLI 子指令） |
| 每週日 | 全市場輕量檢查 | 沿用 sync-universe + sync-financials，尚無「只挑有證據變化」的差異偵測 |

## 已知未實作（誠實列出，不假裝完成）
- 全市場（2,335 檔）sync-quotes 尚未做批次/節流測試，V1 只驗證過 51 檔 watchlist 版本。mis.twse.com.tw 對單次請求的網址長度/symbol 數量上限未知，全市場需要先做分批測試。
- 08:55「只有重大變化才通知」需要保存上一輪 radar.json 快照做 diff，尚未寫。
- 交易日曆判斷（是否為國定假日/臨時休市）尚未實作；目前 `export --assume-market-open` 是手動 flag，不是真的日曆查詢。生產排程啟用前必須先接上真正的交易日曆來源（TWSE 有公告休市日期的頁面，尚未串接）。
- 重啟後排程恢復、失敗重試、通知去重：尚未設計，需等接上真正的 cron 執行器後才能測。

## 備份

`python3 -m stock_radar.cli backup --out <path>` 已實作並測試（見
tests/test_cli.py TestBackup），內部用 SQLite `.backup()` API + 完整性檢查
（`PRAGMA integrity_check`），失敗會 raise 而非靜默產生壞檔。啟用排程時建議
每日跑一次，路徑建議 `_archive/stock_radar_backups/YYYY-MM-DD.db`（沿用現有
`.gitignore` 的 `_archive/` 慣例，不進版控）。
