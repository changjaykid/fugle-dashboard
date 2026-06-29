# 台股主控版引擎 — Agent 行為規則

> 本文件是 Agent 長期行為規則，不是單次修復紀錄。若與程式實際行為衝突，以程式碼為準，並需先更新本文件再修改程式。
>
> 版本：2026-06-29 v2.1

---

## 角色定位

TWSE 公開 API 資料抓取引擎。純 Python，不走 LLM，不需 API key。唯一職責：爬取 → 處理 → 寫 `dashboard.json` → 推 GitHub Pages。

## 核心目標

每個交易日 09:00–14:00 每小時更新一次大盤指數、法人買賣超、個股漲跌、模擬倉，確保下游報告腳本有最新數據可讀。

---

## ✅ 已實作且現役

- TWSE `STOCK_DAY_ALL` 自動判斷回傳格式（JSON 或 CSV），兩種均可解析
- 每個交易日 09:00–14:00 每小時更新（週末 `DOW ≥ 6` 直接 exit）
- `dashboard.json` 是所有台股報告腳本的唯一數據來源
- `sim_engine.py` 同步更新模擬倉 `sim_portfolio.json`
- 執行後 `git push` 推 GitHub Pages

---

## 📝 應寫入永久規則

- TWSE API 格式不穩定：Content-Type 可能為 `application/json` 或 `text/csv`，engine 必須同時支援兩種格式
- `dashboard.json` 結構不得任意變更（下游 4 支報告腳本依賴固定 key）
- Fugle API key 不得寫入 git-tracked 文件（使用本機環境變數或非 tracked config）
- 週末不執行（`DOW ≥ 6` exit 0）

---

## 🔮 未來規劃/待接入

- 引擎失敗時的 Discord 錯誤通知（目前失敗只寫 `run.log`，無主動告警）
- 盤後額外更新機制（14:00 之後的最終數據確認）

---

## 使用 Scripts / Cron / State / Report

| 項目 | 內容 |
|---|---|
| Script | `fugle-dashboard/engine/run_and_push.sh` → `engine.py` + `sim_engine.py` |
| Cron | system crontab 09:00–14:00（週一-五）每小時 |
| Output | `fugle-dashboard/docs/dashboard.json`（下游唯一數據來源）|
| Sim | `fugle-dashboard/docs/sim_portfolio.json` |
| Log | `fugle-dashboard/engine/run.log` |
| Discord | 無（目前缺口，見未來規劃）|

---

## 出錯時處理流程

1. 失敗只記 `run.log`，目前無主動告警
2. 下游早報（08:30）若讀到空 dashboard.json 會直接輸出錯誤
3. 管家發現異常先看 `run.log`，判斷是 TWSE API 格式問題還是 git push 失敗
4. TWSE API 格式切換（CSV/JSON）：`engine.py` 已有容錯邏輯，通常自動恢復

---

## 不應寫進本文件的內容

- 單次 API 格式 bug 的修復細節（存 DATA_PIPELINE.md）
- 各次 git push 失敗的臨時處理紀錄
