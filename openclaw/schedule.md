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
| 08:55 | 僅重大變化才通知（**2026-09-10 已實作**，見下方章節） | `python3 -m stock_radar.cli notify-changes --radar-json docs/radar.json` |
| 09:05 | 用收盤資料源二次確認（STOCK_DAY_ALL / TPEx） | `stock_radar.quotes.fetch_daily_close_all()` / `stock_radar.tpex.fetch_otc_daily_close()`（尚無獨立 CLI 子指令） |
| 每週日 | 全市場輕量檢查 | 沿用 sync-universe + sync-financials，尚無「只挑有證據變化」的差異偵測 |

## 交易日曆（2026-09-10 已實作，取代手動 --assume-market-open）
- 資料源：TWSE 官方 OpenAPI `holidaySchedule`（免費、不需金鑰），回傳當年（ROC 年度）完整休市/補假清單。
- `stock_radar/calendar.py`：`fetch_holiday_schedule()` 抓取並過濾出真正休市的日期（判斷關鍵字「放假」「補假」「市場無交易」；「最後交易日」「開始交易日」這類描述相鄰交易日的列會被排除，不會誤標成休市）；`is_trading_day(date, schedule)` 再疊加週六日一律休市的規則。
- `cli.py sync-calendar` 把結果快取進 SQLite `metadata` 表（key=`trading_calendar`）；`cli.py export` 預設會讀這份快取判斷今天是否為交易日，不再需要手動 `--assume-market-open`。
- **Fail-closed 設計**：若快取的日曆沒有涵蓋今天的 ROC 年度（例如換年後忘記重新 sync-calendar），`export` 不會假裝「開盤」，而是在 `health` 標記 `blocked` 並提示要跑 `sync-calendar`。
- `--assume-market-open` 仍保留，作為手動測試/研究時繞過日曆檢查用，正式排程不應該依賴這個 flag。
- 已知限制：這支 API 目前只回傳「當下」的 ROC 年度資料（測試過帶 `?year=` 參數無效），代表每年跨年後都需要重新 `sync-calendar` 才能拿到新年度的休市清單；建議排程加一個每日或每週跑一次 `sync-calendar` 的頻率，成本很低（單次呼叫，27 筆資料）。

## Fugle vs 全市場涵蓋範圍（2026-09-10 已實作，誠實分離两種要求）
- **不宣稱全市場即時**：Fugle 免費額度未見到文件化的歸限次數，因此 `sync-quotes --source fugle` 預設只允許 50 檔以內（`FUGLE_CANDIDATE_CAP`），超過直接 SystemExit 拒絕，不會静默只拓部分。实務上建議只對 `--watchlist-only`（已核准估值提案的候選標的）走 Fugle；若真的確認方案涵蓋全市場，才加 `--allow-full-market-fugle` 手動覆蓋。
- **全市場改用日更新，不是即時**：全市場（2,335 檔）用免費、無需key的 mis.twse.com.tw（預設來源），來源本身無限制，但本專案排程設計為每日一次而非即時推送；不得在任何文件或訊息中宣稱全市場即時行情。
- **單飛行鎖+節流**：`stock_radar/runtime.py` 提供 `single_flight_lock`（基於 `fcntl.flock` 的跨進程鎖，非阻塞，定位在 `_state/fugle_sync.lock`）與 `ThrottledSession`（預設每次請求間隔0.34秒，約 3 req/s），確保兩個重疊的 cron tick 不會同時扒壻 Fugle 額度；鎖忙碌時直接干淨 fallback 回 mis.twse，不排隊不重試。
- 全市場（2,335 檔）sync-quotes 尚未做批次/節流測試，V1 只驗證過 51 檔 watchlist 版本。mis.twse.com.tw 對單次請求的網址長度/symbol 數量上限未知，全市場需要先做分批測試。
## 08:55 重大變化通知（2026-09-10 已實作）
- `stock_radar/diff.py`：純函式 `significant_changes_against_snapshot()`，比對現在 radar.json 的 `signal.{status,suggested,conservative,extreme,valid_until}` 與上一次通知時存下來的快取（`snapshot_signals()` 壓縮後只存 `{symbol: signal子集}`，不是存一份完整的舊 radar.json）。`calculated_at` 故意不列入比對欄位，否則每次 export 都會被視為改變。
- `cli.py notify-changes` 把壓縮後的快取存在 `Store.meta('last_notified_signals')`，每次執行都會更新為當前完整狀態（不論有沒有發送），避免下一輪比對到舊基準。沒有重大變化時不會發 Discord。

## 通知去重（outbox）+ 有限重試（2026-09-10 已實作）
- `stock_radar/store.py` 新增 `record_delivery()` / `find_sent_delivery()` / `recent_delivery_failures()`，實用早已存在於 schema 的 `deliveries` table（本輪第一次有實際寫入這中的code路徑）。
- `cli.py` 的 `_send_with_outbox()`：發送前先以 `sha256(channel + 訊息內容)` 當天確認是否已發送過（`find_sent_delivery`，以當天局夜為期限，避免相同文字在不同天重發被永久擋住），重複就直接跳過。未重複才實際發送，每次嘗試（成或敗）都寫一列 `deliveries`，失敗最大重試 3 次（預設，指數後退 2 秒基數），全部失敗後 raise，不靜默吞掉。
- `notify-summary`、`notify-changes` 兩個指令都已接上這套 outbox。

## 備份還原（2026-09-10 已實作）
- `stock_radar/store.py` 新增 `Store.restore(backup_path, target_db_path, force=False)`（staticmethod）：備份檔先做 `PRAGMA integrity_check`，確認過才寫入 target，寫入後再檢查一次；target 若已存在且非空，需 `force=True`（CLI：`--force`）才允許覆蓋，避免一行命令誤蓋正式 DB。
- CLI：`python3 -m stock_radar.cli restore --backup <path> --target <path> [--force]`。

## Discord 單股查詢（實際訊息路由，2026-09-10 已實作）
- 之前的 `discord-lookup --post` 只涵蓋「人手動執行 CLI」的情況，不算真正的互動式查詢（使用者在 Discord 敲字不會真的觸發任何东西）。
- 新指令 `discord-poll`：真實輪詢 Discord 頻道，用 `stock_radar/discord.py` 新增的 `extract_query()`（需要明確觸發詞：`$`、`＄`、`查`、`查詢`、`lookup`，否則普通聳天提到股票代號不會被誕成查詢，避免剔小鬼制式回覆）判斷哪些訊息是真的圖查詢。
- 已確認述節的訊息（`author.bot=True`）一律忽略，避免 bot 自己發的訊息觸發自己回覆造成無限迴圈。
- 回覆用 `send_message(..., reply_to_message_id=...)` 真實埠在觸發訊息下方（Discord 真正的 reply thread），不是獨立無關連的新訊息。
- `fetch_new_messages()` 用 Discord 自己的 `after` 訊息id 遊標（存在 `Store.meta('last_discord_poll_id')`），避免不同 process invocation 重複回答同一條訊息。
- 真實網路驗證（ 2026-09-10）：對變頻道 1493898877970153532 跑 `discord-poll --dry-run` 真實拉回 50 條最近訊息，0 條觸發查詢（因為當下沒人敲 `$3661` 式訊息，符合預期）。
- 尚未連接到實际排程（需 Kid 授權才接上），但 CLI 本身已可隨時手動測試。

## 重啟後恢復（2026-09-10 現況說明，尚未接真正 cron/service）
- 本專案所有 `sync-*` 指令都是**幂等**的上寫操作（upsert / 重新算完整取代），重跑一次不會產生重複資料，這意味一個 cron 執行器重啟後只要重新頂定時排程就能恢復，不需要額外的狀態從進變。
- `single_flight_lock` 確保重啟後若舊進程殘留額外鎖檔，新進程會乾淨 skip 而非永乞卡住（非阻塞，不排隊）。
- outbox dedup 確保重啟後重跑同天同內容的通知不會重發。
- 本專案尚未建立任何 launchd/systemd service 或 system crontab 項目（依據Kid明確要求：未授權不接上真正排程），實際服務重啟行行成本將依未來接排程時選用的執行器（system crontab 或 OpenClaw cron）它自己的恢復機制，本專案素埋了幂等性 + 鎖 + outbox 三項基礎。

## 備份

`python3 -m stock_radar.cli backup --out <path>` 已實作並測試（見
tests/test_cli.py TestBackup），內部用 SQLite `.backup()` API + 完整性檢查
（`PRAGMA integrity_check`），失敗會 raise 而非靜默產生壞檔。啟用排程時建議
每日跑一次，路徑建議 `_archive/stock_radar_backups/YYYY-MM-DD.db`（沿用現有
`.gitignore` 的 `_archive/` 慣例，不進版控）。
