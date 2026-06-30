# 台股資料管道說明 DATA_PIPELINE.md

> 建立：2026-06-26（根據 price=0 bug 根因分析後補充）
> 目的：防止未來 session 重踩相同問題

---

## 架構概覽

```
TWSE API
  └── STOCK_DAY_ALL（漲跌排行 + 名稱 + 收盤價）
  └── T86（三大法人買賣超）
  └── TWSE openapi（產業分類）
        ↓
  engine.py fetch_movers()   ← ⚠️ 格式不穩定，見下方說明
        ↓
  all_map {sym: {n, price, changePct, volume, conf}}
        ↓
  inst_map（法人資料）
        ↓
  top_buy / top_sell / volSurge / tomorrowWatch
        ↓
  dashboard.json（推 GitHub Pages）
        ↓
  stock_morning_report.sh    ← 讀 dashboard.json + enrich
  stock_night_report.sh      ← 讀 dashboard.json + enrich
```

---

## ⚠️ TWSE API 格式問題（已知，2026-06-24 發現）

### 問題描述
TWSE `STOCK_DAY_ALL` API 的 content-type 不穩定：
- 正常時：`application/json`，回傳 JSON 格式
- 異常時：`text/csv;charset=utf-8`，回傳 CSV 格式

### 影響
原版 engine.py 只呼叫 `r.json()`，遇到 CSV 時直接 exception → catch → 回傳空 list → `all_map = {}` → 法人股票的 `price = 0`、`name = sym`（代號本身）→ 早報/夜報出現「2610 2610」與「參考價 0.0」

### 修復方案（2026-06-24 已實施）

#### engine.py — `fetch_movers()` 修改

```python
def _parse_movers_rows(rows, sym_col, name_col, vol_col, close_col, chg_col):
    """共用解析邏輯，接受行列表與欄位索引"""
    ...

def fetch_movers():
    r = requests.get('https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL', ...)
    ct = r.headers.get('content-type', '')

    if 'json' in ct or r.text.strip().startswith('{'):
        # JSON 格式：欄位順序 [sym(0), name(1), vol(2), ..., close(7), chg(8)]
        rows = r.json().get('data', [])
        parsed, volumes = _parse_movers_rows(rows, 0, 1, 2, 7, 8)

    elif 'csv' in ct or 'text' in ct:
        # CSV 格式：欄位順序 [date(0), sym(1), name(2), vol(3), ..., close(8), chg(9)]
        reader = csv.reader(io.StringIO(r.text))
        next(reader, None)  # 跳標題
        rows = [row for row in reader if row]
        parsed, volumes = _parse_movers_rows(rows, 1, 2, 3, 8, 9)
```

#### 早報/夜報 — `enrich_candidates()` 安全門

```python
def fetch_twse_price_name_map():
    """直接從 TWSE STOCK_DAY_ALL 抓最新 price/name，支援 JSON 和 CSV"""
    ...

def enrich_candidates(picks):
    """
    若 picks 中有 price <= 0 或 name == symbol（代號重複），
    從 TWSE 重新抓取補齊。
    禁止 fallback 成 0。
    """
    needs = [p for p in picks if price <= 0 or name == sym]
    if not needs:
        return picks  # 正常，不需要 enrich
    tw_map = fetch_twse_price_name_map()
    # 補 price, 補 name
    ...
```

#### 安全門（發送前檢查）

```python
bad_price = [p for p in picks if float(p.get('price','0')) <= 0]
bad_name  = [p for p in picks if p.get('name','') in ('', p.get('symbol',''))]
if bad_price or bad_name:
    # 不推送，回報失敗原因
    sys.exit(1)
```

---

## T86 法人資料

- API：`https://www.twse.com.tw/fund/T86`
- 格式：穩定 JSON
- 欄位：`row[0]` = 代號，`row[1]` = 名稱，`row[4]` = 外資淨買，`row[10]` = 投信淨買，`row[-1]` = 三大法人合計

---

## dashboard.json 欄位說明

| 欄位 | 來源 | 說明 |
|------|------|------|
| `topBuy` | T86 × all_map | 法人買超 Top10，含 price/n/chg/conf |
| `topSell` | T86 × all_map | 法人賣超 Top10 |
| `gainers` / `losers` | STOCK_DAY_ALL | 漲跌排行 |
| `volSurge` | STOCK_DAY_ALL | 爆量股票 |
| `tomorrowWatch` | engine 內部計算 | 明日關注清單 |
| `sectorStrength` | 聚合計算 | 類股強弱 |
| `avoidList` | engine 內部計算 | 今日避開清單 |
| `picks` | 選股結果 | 早晚報選股 |
| `updatedAt` | 執行時間戳 | 最後更新時間 |

---

## 常見問題排查

### price 全部 0 / name 顯示代號
1. 確認 `dashboard.json` 的 `updatedAt` 是否為今日
2. 查 `fugle-dashboard/engine/run.log` 是否有 `[INFO] fetch_movers: 收到 CSV 格式`
3. 如果 all_map 為空，代表 STOCK_DAY_ALL 解析失敗
4. 手動測試：`curl -s 'https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json' -I | grep content-type`

### 夜報候選股 price 仍為 0（enrich 失敗）
1. 查看 stderr：是否有 `[WARN] fetch_twse_price_name_map`
2. 確認 TWSE 連線正常：`curl -s 'https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json' | head -c 100`
3. 安全門會攔截並 exit 1，不推送半成品
