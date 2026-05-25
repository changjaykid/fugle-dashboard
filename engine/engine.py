#!/usr/bin/env python3
"""
Fugle Dashboard Engine
台股行情引擎 - 使用台灣證交所公開 API（完全免費，無需 API key）
"""

import json
import os
import sys
import time
import datetime
import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
DASHBOARD_FILE = BASE_DIR.parent / "docs" / "dashboard.json"

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

# ── 載入設定 ──────────────────────────────────────────────
def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

# ── TWSE 公開 API ─────────────────────────────────────────
class TWClient:
    STOCK_INFO = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    STOCK_DAY   = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
    STOCK_DAY_OTC = "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_print.php"

    def quote_batch(self, symbols):
        """批次取得多支股票即時報價（TWSE）"""
        # 先嘗試全部當上市查
        tse_syms = [f'tse_{s}.tw' for s in symbols]
        ex_ch = '|'.join(tse_syms)
        try:
            r = requests.get(self.STOCK_INFO, params={'ex_ch': ex_ch, 'json': 1, 'delay': 0},
                             headers=HEADERS, timeout=10)
            r.raise_for_status()
            data = r.json().get('msgArray', [])
            result = {}
            for item in data:
                sym = item.get('c', '')
                if sym:
                    result[sym] = item
            return result
        except Exception as e:
            print(f"[ERROR] quote_batch: {e}")
            return {}

    def daily_candles(self, symbol):
        """取得近月日 K 線（收盤後資料）"""
        try:
            r = requests.get(self.STOCK_DAY,
                             params={'response': 'json', 'stockNo': symbol},
                             headers=HEADERS, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[ERROR] daily_candles {symbol}: {e}")
            return None

# ── 解析 TWSE 報價資料 ────────────────────────────────────
def parse_quote(item):
    """
    TWSE getStockInfo 欄位：
    z = 成交價, y = 昨收, h = 最高, l = 最低, o = 開盤
    tv = 成交量, n = 名稱, c = 代碼
    """
    def safe_float(v):
        try:
            return float(v)
        except:
            return 0.0

    price      = safe_float(item.get('z', 0))
    prev_close = safe_float(item.get('y', 0))
    high       = safe_float(item.get('h', 0))
    low        = safe_float(item.get('l', 0))
    volume     = safe_float(item.get('tv', 0))  # 張
    name       = item.get('n', '')

    change     = round(price - prev_close, 2) if price and prev_close else 0
    change_pct = round(change / prev_close * 100, 2) if prev_close else 0

    return {
        'price': price,
        'prevClose': prev_close,
        'high': high,
        'low': low,
        'volume': int(volume),
        'name': name,
        'change': change,
        'changePct': change_pct,
    }

# ── 信心分數計算 ──────────────────────────────────────────
def compute_confidence(change_pct, volume=0):
    """
    簡易信心分 0-100
    主要看漲跌幅，量能輔助
    """
    if change_pct > 5:
        score = 88
    elif change_pct > 3:
        score = 80
    elif change_pct > 1:
        score = 68
    elif change_pct > 0:
        score = 58
    elif change_pct == 0:
        score = 50
    elif change_pct > -1:
        score = 42
    elif change_pct > -3:
        score = 32
    elif change_pct > -5:
        score = 22
    else:
        score = 12
    return max(0, min(100, score))

def confidence_label(score):
    if score >= 75:
        return "強力看多", "#00c853"
    elif score >= 60:
        return "偏多", "#69f0ae"
    elif score >= 45:
        return "中性觀望", "#ffd740"
    elif score >= 30:
        return "偏空", "#ff6d00"
    else:
        return "強力看空", "#d50000"

# ── 主掃描流程 ────────────────────────────────────────────
def scan():
    config = load_config()
    client = TWClient()
    watchlist = config.get("watchlist", [])
    symbols = [s if isinstance(s, str) else s.get("symbol", "") for s in watchlist]
    symbols = [s for s in symbols if s]

    if not symbols:
        print("⚠️  watchlist 為空，無股票可掃描")
        symbols = []

    print(f"  批次查詢 {len(symbols)} 支股票...")
    quotes = client.quote_batch(symbols)

    stocks = []
    now_tw = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    updated_at = now_tw.strftime("%H:%M")

    for symbol in symbols:
        item = quotes.get(symbol, {})
        if not item:
            # 可能是 OTC 股票，或非交易時間
            print(f"  [WARN] 查無 {symbol} 資料，跳過")
            continue

        q = parse_quote(item)
        confidence = compute_confidence(q['changePct'], q['volume'])
        label, color = confidence_label(confidence)

        stocks.append({
            "symbol": symbol,
            "name": q['name'],
            "category": "",
            "price": q['price'],
            "prevClose": q['prevClose'],
            "high": q['high'],
            "low": q['low'],
            "change": q['change'],
            "changePct": q['changePct'],
            "volume": q['volume'],
            "confidence": confidence,
            "confidenceLabel": label,
            "confidenceColor": color,
            "updatedAt": updated_at
        })

    # 輸出 dashboard.json
    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stocks": stocks,
        "sectors": config.get("sectors", []),
        "updatedAt": now_tw.isoformat(),
        "marketOpen": is_market_open(),
        "source": "TWSE"
    }
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"✅ 掃描完成，{len(stocks)} 支股票")
    return payload

def is_market_open():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    if now.weekday() >= 5:
        return False
    t = now.time()
    return datetime.time(9, 0) <= t <= datetime.time(13, 30)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        print("🔍 開始掃描...")
        scan()
