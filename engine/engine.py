#!/usr/bin/env python3
"""
Fugle Dashboard Engine
台股行情引擎 - 使用台灣證交所公開 API（完全免費，無需 API key）
抓取：大盤指數 / 個股報價 / 強弱排行 / 法人買賣超
"""

import json
import sys
import time
import datetime
import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
DASHBOARD_FILE = BASE_DIR.parent / "docs" / "dashboard.json"

HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
TW_TZ = datetime.timezone(datetime.timedelta(hours=8))


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def safe_float(v, default=0.0):
    try:
        return float(str(v).replace(',', ''))
    except:
        return default


# ── TWSE 大盤指數 ─────────────────────────────────────────
def fetch_index():
    """取得加權指數即時資訊"""
    try:
        r = requests.get(
            'https://mis.twse.com.tw/stock/api/getStockInfo.jsp',
            params={'ex_ch': 'tse_t00.tw', 'json': 1, 'delay': 0},
            headers=HEADERS, timeout=10
        )
        data = r.json().get('msgArray', [])
        if not data:
            return {}
        item = data[0]
        price = safe_float(item.get('z', 0))
        prev  = safe_float(item.get('y', 0))
        change = round(price - prev, 2)
        change_pct = round(change / prev * 100, 2) if prev else 0
        vol_raw = item.get('tv', '0')
        return {
            'taiex': str(price),
            'change': ('+' if change >= 0 else '') + str(change),
            'changePct': ('+' if change_pct >= 0 else '') + str(change_pct) + '%',
            'volume': vol_raw + ' 張'
        }
    except Exception as e:
        print(f'[WARN] fetch_index: {e}')
        return {}


# ── TWSE 全市場快照（漲跌排行）──────────────────────────────
def fetch_movers():
    """
    取得上市股票當日漲跌排行
    使用 TWSE 盤後成交資料：每日收盤後更新
    """
    try:
        # 用 STOCK_DAY_ALL 取得所有股票當日收盤資料
        # 欄位: [代號, 名稱, 成交股數, 成交金額, 開盤, 最高, 最低, 收盤, 漲跌, 成交筆數]
        r = requests.get(
            'https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL',
            params={'response': 'json'},
            headers=HEADERS, timeout=15
        )
        data = r.json()
        rows = data.get('data', [])

        parsed = []
        for row in rows:
            try:
                sym   = str(row[0]).strip()
                name  = str(row[1]).strip()
                close = safe_float(str(row[7]).replace(',',''))
                if close <= 0:
                    continue
                # 第 8 欄: 漲跌 (+X.XX 或 -X.XX)
                chg_raw = str(row[8]).strip()
                change  = safe_float(chg_raw.replace('+','').replace(',',''))
                if chg_raw.startswith('-'):
                    change = -abs(change)
                prev   = close - change
                pct    = round(change / prev * 100, 2) if prev else 0
                vol    = safe_float(str(row[2]).replace(',','')) / 1000  # 股 -> 張
                parsed.append({'s': sym, 'n': name, 'price': close, 'change': change, 'changePct': pct, 'volume': vol})
            except:
                continue

        # 漲幅 Top10
        gainers = sorted([x for x in parsed if x['changePct'] > 0], key=lambda x: x['changePct'], reverse=True)[:10]
        losers  = sorted([x for x in parsed if x['changePct'] < 0], key=lambda x: x['changePct'])[:10]

        return gainers, losers, parsed

    except Exception as e:
        print(f'[WARN] fetch_movers: {e}')
        return [], [], []


# ── TWSE 法人買賣超 ──────────────────────────────────────
def fetch_institutional():
    """取得三大法人買賣超 Top10"""
    try:
        r = requests.get(
            'https://www.twse.com.tw/fund/T86',
            params={'response': 'json', 'selectType': 'ALL'},
            headers=HEADERS, timeout=15
        )
        data = r.json()
        rows = data.get('data', [])
        # 欄位: [代號, 名稱, 外資買, 外資賣, 外資淨, 投信買, 投信賣, 投信淨, 自營商..., 三大法人合計]
        parsed = []
        for row in rows:
            try:
                sym = str(row[0]).strip()
                name = str(row[1]).strip()
                foreign_net = safe_float(str(row[4]).replace(',', ''))
                trust_net = safe_float(str(row[7]).replace(',', ''))
                total_net = safe_float(str(row[13]).replace(',', ''))
                parsed.append({'s': sym, 'n': name, 'foreign': foreign_net, 'trust': trust_net, 'total': total_net})
            except:
                continue

        # 過濾掉 ETF / 反向槓桿（代號含字母或超過4碼）
        def is_stock(sym):
            return sym.isdigit() and len(sym) == 4

        stocks_only = [x for x in parsed if is_stock(x['s'])]
        top_buy  = sorted(stocks_only, key=lambda x: x['total'], reverse=True)[:10]
        top_sell = sorted(stocks_only, key=lambda x: x['total'])[:10]

        def fmt_shares(v):
            iv = int(v)
            return ('+' if iv >= 0 else '') + format(iv, ',') + '張'

        buy_result = [{
            'r': str(i+1),
            's': x['s'],
            'n': x['n'],
            'foreign': fmt_shares(x['foreign']),
            'trust': fmt_shares(x['trust']),
            'total': fmt_shares(x['total']),
            'dir': 'up'
        } for i, x in enumerate(top_buy)]

        sell_result = [{
            'r': str(i+1),
            's': x['s'],
            'n': x['n'],
            'foreign': fmt_shares(x['foreign']),
            'trust': fmt_shares(x['trust']),
            'total': fmt_shares(x['total']),
            'dir': 'dn'
        } for i, x in enumerate(top_sell)]

        return buy_result, sell_result

    except Exception as e:
        print(f'[WARN] fetch_institutional: {e}')
        return [], []


# ── TWSE 個股批次報價 ─────────────────────────────────────
def fetch_quotes_batch(symbols):
    """批次取得個股即時報價"""
    tse_syms = [f'tse_{s}.tw' for s in symbols]
    ex_ch = '|'.join(tse_syms)
    try:
        r = requests.get(
            'https://mis.twse.com.tw/stock/api/getStockInfo.jsp',
            params={'ex_ch': ex_ch, 'json': 1, 'delay': 0},
            headers=HEADERS, timeout=10
        )
        result = {}
        for item in r.json().get('msgArray', []):
            sym = item.get('c', '')
            if sym:
                result[sym] = item
        return result
    except Exception as e:
        print(f'[ERROR] fetch_quotes_batch: {e}')
        return {}


# ── 信心分數 ──────────────────────────────────────────────
def compute_confidence(change_pct):
    if change_pct > 5:   return 88
    if change_pct > 3:   return 80
    if change_pct > 1:   return 68
    if change_pct > 0:   return 58
    if change_pct == 0:  return 50
    if change_pct > -1:  return 42
    if change_pct > -3:  return 32
    if change_pct > -5:  return 22
    return 12

def confidence_label(score):
    if score >= 85: return '強力看多', '#00c853'
    if score >= 68: return '偏多',     '#69f0ae'
    if score >= 52: return '中性觀望', '#ffd740'
    if score >= 35: return '偏空',     '#ff6d00'
    return '強力看空', '#d50000'


# ── 主掃描 ───────────────────────────────────────────────
def scan():
    config = load_config()
    watchlist = config.get('watchlist', [])
    symbols = [s if isinstance(s, str) else s.get('symbol', '') for s in watchlist]
    symbols = [s for s in symbols if s]

    now = datetime.datetime.now(TW_TZ)
    updated_str = now.strftime('%H:%M')

    print('  📈 大盤指數...')
    index_data = fetch_index()

    print('  🏆 法人買賣超...')
    top_buy, top_sell = fetch_institutional()

    print('  🚀 漲跌排行...')
    gainers, losers, all_stocks = fetch_movers()
    all_map = {s['s']: s for s in all_stocks}

    print(f'  📊 個股報價 ({len(symbols)} 支)...')
    quotes = fetch_quotes_batch(symbols) if symbols else {}

    # 補上法人資料的 price/chg
    def enrich_inst(lst):
        for item in lst:
            m = all_map.get(item['s'])
            if m:
                sg = '+' if m['changePct'] >= 0 else ''
                item['price'] = str(m['price'])
                item['chg']   = f"{sg}{m['changePct']}%"
                item['dir']   = 'up' if m['changePct'] >= 0 else 'dn'
        return lst

    top_buy  = enrich_inst(top_buy)
    top_sell = enrich_inst(top_sell)

    # 個股卡
    stocks = []
    for sym in symbols:
        # 優先用即時報價，fallback 用盤後資料
        item = quotes.get(sym)
        if item:
            price     = safe_float(item.get('z', 0))
            prev      = safe_float(item.get('y', 0))
            high      = safe_float(item.get('h', 0))
            low       = safe_float(item.get('l', 0))
            volume    = int(safe_float(item.get('tv', 0)))
            name      = item.get('n', sym)
            change    = round(price - prev, 2) if price and prev else 0
            changePct = round(change / prev * 100, 2) if prev else 0
        elif sym in all_map:  # type: ignore
            m = all_map[sym]
            price = m['price']; changePct = m['changePct']
            change = m['change']; high = 0; low = 0; volume = int(m['volume'])
            name = m['n']
        else:
            print(f'  [WARN] 查無 {sym}，跳過')
            continue

        conf = compute_confidence(changePct)
        label, color = confidence_label(conf)
        stocks.append({
            'symbol': sym, 'name': name, 'category': '',
            'price': price, 'high': high, 'low': low,
            'change': change, 'changePct': changePct,
            'volume': volume, 'confidence': conf,
            'confidenceLabel': label, 'confidenceColor': color,
            'updatedAt': updated_str
        })

    # 格式化漲跌排行
    def fmt_mover(rank, m):
        d = 'up' if m['changePct'] >= 0 else 'dn'
        sg = '+' if m['changePct'] >= 0 else ''
        return {
            'r': str(rank),
            's': m['s'], 'n': m['n'],
            'price': str(m['price']),
            'chg': f"{sg}{m['changePct']}%",
            'vol': f"{m['volume']/10000:.1f}萬",
            'dir': d
        }

    payload = {
        'stocks': stocks,
        'sectors': config.get('sectors', []),
        'index': index_data,
        'topBuy': top_buy,
        'topSell': top_sell,
        'gainers': [fmt_mover(i+1, m) for i, m in enumerate(gainers)],
        'losers':  [fmt_mover(i+1, m) for i, m in enumerate(losers)],
        'updatedAt': now.isoformat(),
        'marketOpen': is_market_open(),
        'source': 'TWSE'
    }

    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f'✅ 完成：{len(stocks)} 支個股 / 大盤 / 法人 Top{len(top_buy)} / 漲幅 Top{len(gainers)}')
    return payload


def is_market_open():
    now = datetime.datetime.now(TW_TZ)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return datetime.time(9, 0) <= t <= datetime.time(13, 30)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    if cmd == 'scan':
        print('🔍 開始掃描...')
        scan()
