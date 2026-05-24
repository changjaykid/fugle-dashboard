#!/usr/bin/env python3
"""
Fugle Dashboard Engine
台股行情引擎 - 行情抓取、信心分析、新聞掃描
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

# ── 載入設定 ──────────────────────────────────────────────
def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

# ── Fugle Market Data API ─────────────────────────────────
class FugleClient:
    BASE = "https://api.fugle.tw/marketdata/v1.0/stock"

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {"X-API-KEY": api_key}

    def quote(self, symbol):
        """即時報價"""
        try:
            r = requests.get(f"{self.BASE}/intraday/quote/{symbol}",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[ERROR] quote {symbol}: {e}")
            return None

    def candles(self, symbol, timeframe="1"):
        """K線（分鐘）"""
        try:
            r = requests.get(f"{self.BASE}/intraday/candles/{symbol}",
                             params={"timeframe": timeframe},
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[ERROR] candles {symbol}: {e}")
            return None

    def meta(self, symbol):
        """股票基本資訊"""
        try:
            r = requests.get(f"{self.BASE}/intraday/meta/{symbol}",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[ERROR] meta {symbol}: {e}")
            return None

    def institutional(self, symbol):
        """三大法人"""
        try:
            r = requests.get(f"{self.BASE}/historical/institutional/{symbol}",
                             headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[ERROR] institutional {symbol}: {e}")
            return None

# ── 信心分數計算 ──────────────────────────────────────────
def compute_confidence(quote_data, institutional_data, news_score=50):
    """
    信心分數 0-100
    - 技術面 25%：漲跌、成交量比
    - 籌碼面 30%：外資、投信買賣超
    - 話題熱度 20%：新聞分數（外部傳入）
    - 法人動向 15%：三大法人合計
    - 類股強度 10%：預設中性
    """
    score = 50  # 基礎分

    # 技術面：漲跌幅
    tech_score = 50
    if quote_data:
        change_pct = quote_data.get("changePercent", 0) or 0
        if change_pct > 3:
            tech_score = 85
        elif change_pct > 1:
            tech_score = 70
        elif change_pct > 0:
            tech_score = 60
        elif change_pct < -3:
            tech_score = 15
        elif change_pct < -1:
            tech_score = 30
        else:
            tech_score = 45

    # 籌碼面：外資
    chip_score = 50
    if institutional_data and isinstance(institutional_data, dict):
        data = institutional_data.get("data", [])
        if data:
            latest = data[0] if isinstance(data, list) else data
            foreign = latest.get("foreignDealersBuyNet", 0) or 0
            trust = latest.get("investmentTrustBuyNet", 0) or 0
            net = foreign + trust
            if net > 5000:
                chip_score = 85
            elif net > 1000:
                chip_score = 70
            elif net > 0:
                chip_score = 60
            elif net < -5000:
                chip_score = 15
            elif net < -1000:
                chip_score = 30
            else:
                chip_score = 45

    # 綜合加權
    confidence = int(
        tech_score * 0.25 +
        chip_score * 0.30 +
        news_score * 0.20 +
        chip_score * 0.15 +  # 法人動向用籌碼面代替
        50 * 0.10            # 類股強度預設中性
    )
    return max(0, min(100, confidence))

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

# ── 新聞抓取（公開來源）────────────────────────────────────
def fetch_news_score(symbol, company_name=""):
    """
    用關鍵字搜尋新聞，回傳熱度分數 0-100
    目前用簡易 Yahoo Finance 台股新聞
    """
    try:
        query = company_name or symbol
        url = f"https://tw.stock.yahoo.com/quote/{symbol}/news"
        r = requests.get(url, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        # 簡易：有抓到回傳 60，錯誤回傳 50
        if r.status_code == 200 and symbol in r.text:
            return 60
        return 50
    except:
        return 50

# ── 主掃描流程 ────────────────────────────────────────────
def scan():
    config = load_config()
    client = FugleClient(config["api_key"])
    watchlist = config.get("watchlist", [])

    stocks = []
    for item in watchlist:
        symbol = item if isinstance(item, str) else item.get("symbol", "")
        if not symbol:
            continue

        print(f"  掃描 {symbol}...")
        quote_data = client.quote(symbol)
        meta_data = client.meta(symbol)
        inst_data = client.institutional(symbol)
        news_score = fetch_news_score(symbol)

        # 基本資訊
        name = ""
        category = ""
        description = ""
        if meta_data:
            name = meta_data.get("name", symbol)
            industry = meta_data.get("industry", "")
            category = industry

        # 報價
        price = 0
        change = 0
        change_pct = 0
        volume = 0
        if quote_data:
            price = quote_data.get("closePrice") or quote_data.get("lastPrice") or 0
            change = quote_data.get("change", 0) or 0
            change_pct = quote_data.get("changePercent", 0) or 0
            volume = quote_data.get("total", {}).get("tradeVolume", 0) if isinstance(quote_data.get("total"), dict) else 0

        confidence = compute_confidence(quote_data, inst_data, news_score)
        label, color = confidence_label(confidence)

        stocks.append({
            "symbol": symbol,
            "name": name,
            "category": category,
            "price": price,
            "change": round(change, 2),
            "changePct": round(change_pct, 2),
            "volume": volume,
            "confidence": confidence,
            "confidenceLabel": label,
            "confidenceColor": color,
            "newsScore": news_score,
            "updatedAt": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%H:%M")
        })
        time.sleep(0.3)  # 避免打爆 API

    # 輸出 dashboard.json
    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "stocks": stocks,
        "sectors": config.get("sectors", []),
        "updatedAt": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
        "marketOpen": is_market_open()
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

# ── 類股查詢（由 OpenClaw 呼叫）──────────────────────────
def query_sector(sector_name):
    """查詢類股，回傳相關股票清單（由 AI 補充分析）"""
    # 預設台股類股對應表
    SECTOR_MAP = {
        "低軌衛星": ["6491", "3714", "4961", "6239", "3030"],
        "AI伺服器": ["2317", "3045", "6669", "4938", "3231"],
        "半導體": ["2330", "2303", "2308", "2379", "3711"],
        "電動車": ["2603", "1605", "6213", "3557", "5483"],
        "ETF": ["0050", "0056", "00878", "00919", "00929"],
    }
    return SECTOR_MAP.get(sector_name, [])

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        print("🔍 開始掃描...")
        scan()
    elif cmd == "sector" and len(sys.argv) > 2:
        symbols = query_sector(sys.argv[2])
        print(json.dumps(symbols, ensure_ascii=False))
