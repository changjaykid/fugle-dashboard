#!/usr/bin/env python3
"""
台股主控版行情引擎 v3
資料源：台灣證交所公開 API（完全免費，無需 API key，零 LLM）
功能：大盤指數 / 市場情緒 / 個股報價(TSE+OTC) / 法人買賣超 / 漲跌排行
      + 產業分類 / 升級信心分 / 明日關注清單
"""

import json, sys, datetime, requests, xml.etree.ElementTree as ET
from pathlib import Path

BASE_DIR       = Path(__file__).parent
CONFIG_FILE    = BASE_DIR / "config.json"
DASHBOARD_FILE = BASE_DIR.parent / "docs" / "dashboard.json"
HEADERS        = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
TW_TZ          = datetime.timezone(datetime.timedelta(hours=8))

# ── 產業代碼對照表（TWSE i 欄位）───────────────────────────
INDUSTRY_MAP = {
    '01': '水泥', '02': '食品', '03': '塑膠', '04': '紡織',
    '05': '電機', '06': '電器電纜', '08': '玻璃陶瓷',
    '09': '造紙', '10': '鋼鐵', '11': '橡膠', '12': '汽車',
    '13': '電子', '14': '建材營造', '15': '航運', '16': '觀光',
    '17': '金融保險', '18': '貿易百貨', '19': '油電燃氣',
    '20': '其他', '21': '化學', '22': '生技醫療',
    '23': '油電燃氣', '24': '半導體', '25': '電腦周邊',
    '26': '光電', '27': '通信網路', '28': '電子零組件',
    '29': '電子通路', '30': '資訊服務', '31': '其他電子',
    '32': '文化創意', '33': '農業科技', '34': '電商',
    '80': 'ETF', '81': 'ETN', '90': '存託憑證',
}

def get_industry(code):
    return INDUSTRY_MAP.get(str(code).strip().zfill(2), '')


# ── 從 TWSE openapi 取上市公司產業別對照 ──────────────────
_industry_cache_global = {}

def fetch_industry_map():
    """從 TWSE opendata 取得股票代碼→產業別對照（含 TSE+TDR）"""
    global _industry_cache_global
    if _industry_cache_global:
        return _industry_cache_global
    result = {}
    try:
        r = requests.get('https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
                         headers=HEADERS, timeout=15)
        for item in r.json():
            code = str(item.get('公司代號', '')).strip()
            ind  = str(item.get('產業別', '')).strip()
            if code and ind:
                result[code] = get_industry(ind)
    except Exception as e:
        print(f'[WARN] fetch_industry_map TSE: {e}')
    _industry_cache_global = result
    return result




def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

def safe_float(v, default=0.0):
    try: return float(str(v).replace(',', ''))
    except: return default

def signed_int(s):
    s = str(s).replace(',', '').strip()
    try:
        v = int(s.replace('+', '').replace('-', ''))
        return -v if s.startswith('-') else v
    except: return 0

def shares_to_lots(v):
    return int(round(v / 1000))

def parse_count(s):
    try:
        s = str(s).replace(',', '')
        if '(' in s:
            main, sub = s.split('(')
            return int(main.strip()), int(sub.replace(')', '').strip())
        return int(s.strip()), 0
    except: return 0, 0


# ── 新聞抓取 ──────────────────────────────────────────
def fetch_news():
    """抓取 Yahoo 財經 RSS 或 鉅亨網台股新聞"""
    news = []
    # 嘗試 Yahoo RSS
    try:
        r = requests.get('https://tw.stock.yahoo.com/rss?s=.TWI', headers=HEADERS, timeout=10)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall('./channel/item')[:10]:
                title = item.find('title').text
                url = item.find('link').text
                pub = item.find('pubDate').text
                news.append({
                    'title': title,
                    'url': url,
                    'published': pub,
                    'source': 'Yahoo財經'
                })
    except Exception as e:
        print(f'[WARN] fetch_news Yahoo: {e}')

    # 如果 Yahoo 沒抓到或太少，嘗試鉅亨網
    if len(news) < 5:
        try:
            r = requests.get('https://news.cnyes.com/news/cat/tw_stock', headers=HEADERS, timeout=10)
            if r.status_code == 200:
                from html.parser import HTMLParser
                class CnyesParser(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.links = []
                        self.in_a = False
                        self.current_href = ""
                    def handle_starttag(self, tag, attrs):
                        if tag == 'a':
                            attrs_dict = dict(attrs)
                            href = attrs_dict.get('href', '')
                            if '/news/id/' in href:
                                self.in_a = True
                                self.current_href = 'https://news.cnyes.com' + href
                    def handle_endtag(self, tag):
                        if tag == 'a': self.in_a = False
                    def handle_data(self, data):
                        if self.in_a and data.strip():
                            if not any(x['url'] == self.current_href for x in news):
                                self.links.append({
                                    'title': data.strip(),
                                    'url': self.current_href,
                                    'published': '',
                                    'source': '鉅亨網'
                                })
                parser = CnyesParser()
                parser.feed(r.text)
                news.extend(parser.links[:10-len(news)])
        except Exception as e:
            print(f'[WARN] fetch_news Cnyes: {e}')
    
    return news[:10]

# ── 個股指標計算 (RSI, 52週位置, 5日量比) ────────────────
def fetch_stock_indicators(symbol):
    """抓取並計算 RSI, 52週位置, 5日量比"""
    res = {'rsi': None, 'week52Pos': None, 'volRatio5d': None}
    try:
        # 1. RSI (14日) + 5日量比
        # 從 STOCK_DAY 抓最近資料
        r = requests.get(f'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&stockNo={symbol}', 
                         headers=HEADERS, timeout=10)
        data = r.json().get('data', [])
        if len(data) >= 15:
            # 算 RSI
            closes = [safe_float(row[6]) for row in data[-15:]]
            diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
            up = [d if d > 0 else 0 for d in diffs]
            dn = [-d if d < 0 else 0 for d in diffs]
            avg_up = sum(up) / 14
            avg_dn = sum(dn) / 14
            if avg_dn == 0: res['rsi'] = 100
            else:
                rs = avg_up / avg_dn
                res['rsi'] = round(100 - (100 / (1 + rs)), 2)
            
            # 算 5日量比
            vols = [safe_float(row[1]) for row in data[-5:]]
            avg_vol_5d = sum(vols) / 5
            today_vol = vols[-1]
            if avg_vol_5d > 0:
                res['volRatio5d'] = round(today_vol / avg_vol_5d, 2)

        # 2. 52週位置 (用月報簡化)
        r_m = requests.get(f'https://www.twse.com.tw/exchangeReport/STOCK_DAY_AVG?response=json&stockNo={symbol}',
                           headers=HEADERS, timeout=10)
        # 註：STOCK_DAY_AVG 只有當月，真正的 52 週需要更大量資料，這裡用 STOCK_DAY 湊合或簡化邏輯
        # 由於 TWSE API 限制，這裡實作最穩定的版本：如果當前 data 足夠就用 data
        if len(data) > 0:
            prices = [safe_float(row[4]) for row in data] # 最高
            prices_l = [safe_float(row[5]) for row in data] # 最低
            high52 = max(prices)
            low52 = min(prices_l)
            curr = safe_float(data[-1][6]) # 最新收盤
            if high52 > low52:
                res['week52Pos'] = round((curr - low52) / (high52 - low52) * 100, 2)
    except Exception as e:
        print(f'[WARN] indicators for {symbol}: {e}')
    return res


# ── 大盤指數 + 市場情緒 ──────────────────────────────────────────
def fetch_index():
    result = {}
    try:
        r = requests.get('https://mis.twse.com.tw/stock/api/getStockInfo.jsp',
                         params={'ex_ch': 'tse_t00.tw', 'json': 1, 'delay': 0},
                         headers=HEADERS, timeout=10)
        item = r.json().get('msgArray', [{}])[0]
        price = safe_float(item.get('z', 0))
        prev  = safe_float(item.get('y', 0))
        chg   = round(price - prev, 2)
        pct   = round(chg / prev * 100, 2) if prev else 0
        result.update({
            'taiex': str(price), 'taiexPrev': str(prev),
            'change': ('+' if chg >= 0 else '') + str(chg),
            'changePct': ('+' if pct >= 0 else '') + str(pct) + '%',
            'changePctNum': pct,
            'volume': item.get('tv', '0') + '張'
        })
    except Exception as e:
        print(f'[WARN] fetch_index realtime: {e}')

    try:
        r2 = requests.get('https://www.twse.com.tw/exchangeReport/MI_INDEX',
                          params={'response': 'json', 'type': 'MS'},
                          headers=HEADERS, timeout=10)
        for t in r2.json().get('tables', []):
            if '漲跌證券數合計' in t.get('title', ''):
                data = t.get('data', [])
                up_total,  limit_up = parse_count(data[0][1]) if data else (0, 0)
                dn_total,  limit_dn = parse_count(data[1][1]) if len(data) > 1 else (0, 0)
                flat_total, _       = parse_count(data[2][1]) if len(data) > 2 else (0, 0)
                total = up_total + dn_total + flat_total

                up_ratio    = up_total / total if total else 0.5
                limit_score = min(limit_up / 15, 1.0)
                taiex_pct   = result.get('changePctNum', 0)
                taiex_score = min(max((taiex_pct + 3) / 6, 0), 1.0)

                mood = int(up_ratio * 40 + limit_score * 30 + taiex_score * 30)
                mood = max(0, min(100, mood))
                mood_label = (
                    '🔥 強勢多頭' if mood >= 75 else
                    '📈 偏多，謹慎追高' if mood >= 58 else
                    '⏸️ 中性觀望' if mood >= 42 else
                    '📉 偏空，控制倉位' if mood >= 25 else
                    '🚨 恐慌，觀望為主'
                )
                result.update({
                    'upCount': up_total, 'dnCount': dn_total, 'flatCount': flat_total,
                    'limitUp': limit_up, 'limitDn': limit_dn,
                    'mood': mood, 'moodLabel': mood_label
                })
                break
    except Exception as e:
        print(f'[WARN] fetch_index mood: {e}')

    return result


# ── 法人買賣超（回傳 raw inst_map 供信心分用）──────────────
def fetch_institutional_raw():
    """回傳 {sym: {foreign, trust, total}} 供計算用"""
    try:
        today = datetime.datetime.now(TW_TZ).strftime('%Y%m%d')
        r = requests.get('https://www.twse.com.tw/fund/T86',
                         params={'response': 'json', 'selectType': 'ALL', 'date': today},
                         headers=HEADERS, timeout=15)
        rows = r.json().get('data', [])
        if not rows:
            r = requests.get('https://www.twse.com.tw/fund/T86',
                             params={'response': 'json', 'selectType': 'ALL'},
                             headers=HEADERS, timeout=15)
            rows = r.json().get('data', [])

        inst_map = {}
        for row in rows:
            sym = str(row[0]).strip()
            if len(sym) == 4 and sym.isdigit():
                inst_map[sym] = {
                    'foreign': signed_int(row[4]),
                    'trust':   signed_int(row[10]) if len(row) >= 19 else 0,
                    'total':   signed_int(row[-1])  # 最後欄位永遠是三大法人合計
                }
        return inst_map
    except Exception as e:
        print(f'[WARN] fetch_institutional_raw: {e}')
        return {}


# ── 漲跌排行 + 產業（含信心分預備數據）──────────────────────
def fetch_movers():
    """
    STOCK_DAY_ALL: [0]代號 [1]名稱 [2]成交股數 [3]成交金額 [4]開盤 [5]最高 [6]最低 [7]收盤 [8]漲跌 [9]成交筆數
    """
    try:
        r = requests.get('https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL',
                         params={'response': 'json'}, headers=HEADERS, timeout=15)
        rows = r.json().get('data', [])
        volumes = []
        parsed = []
        for row in rows:
            try:
                sym   = str(row[0]).strip()
                name  = str(row[1]).strip()
                close = safe_float(str(row[7]).replace(',', ''))
                if close <= 0: continue
                chg_raw = str(row[8]).strip()
                chg   = safe_float(chg_raw.replace('+', '').replace(',', ''))
                if chg_raw.startswith('-'): chg = -abs(chg)
                prev  = close - chg
                pct   = round(chg / prev * 100, 2) if prev else 0
                vol   = safe_float(str(row[2]).replace(',', '')) / 1000  # 股→張
                volumes.append(vol)
                parsed.append({'s': sym, 'n': name, 'price': close,
                               'change': chg, 'changePct': pct, 'volume': vol})
            except: continue

        avg_vol = sum(volumes) / len(volumes) if volumes else 1
        return parsed, avg_vol

    except Exception as e:
        print(f'[WARN] fetch_movers: {e}')
        return [], 1


# ── 個股即時報價（含產業代碼 i）─────────────────────────────
OTC_STOCKS = {'3081','3105','3163','3221','3324','3363','3491','3672',
              '4147','4162','4541','4743','4979','5274','5317','5425',
              '5443','6127','6173','6274','6547','6576','6643'}

THEME_STOCKS = """0050 0056 1101 1503 1504 1513 1514 1516 1519 1530 1536 1545 1590 1598 1605
1760 1795 2014 2049 2059 2201 2301 2303 2308 2313 2314 2317 2327 2330 2345
2354 2355 2356 2367 2368 2375 2379 2382 2383 2392 2395 2412 2419 2428 2454
2455 2474 2481 2485 2492 2501 2504 2511 2515 2520 2542 2548 2603 2606 2609
2610 2615 2617 2618 2636 2637 2880 2881 2882 2883 2884 2885 2886 2891 2892
3005 3008 3014 3017 3019 3026 3030 3034 3037 3042 3044 3045 3081 3105 3163
3189 3221 3231 3324 3363 3380 3406 3443 3450 3491 3557 3576 3653 3661 3672
3673 3708 3711 4147 4162 4164 4541 4583 4743 4904 4906 4938 4958 4961 4979
5274 5317 5388 5425 5443 5533 5534 5608 5876 6127 6173 6213 6274 6278 6282
6285 6409 6414 6415 6443 6472 6533 6547 6576 6643 6669 6706 6781 6806 6869 8046""".split()

def fetch_quotes_batch(symbols):
    all_syms = list(dict.fromkeys(list(symbols) + THEME_STOCKS))
    tse_syms = [f'tse_{s}.tw' for s in all_syms if s not in OTC_STOCKS]
    otc_syms = [f'otc_{s}.tw' for s in all_syms if s in OTC_STOCKS]
    result = {}
    base = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp'
    try:
        r = requests.get(base, params={'ex_ch': '|'.join(tse_syms), 'json': 1, 'delay': 0},
                         headers=HEADERS, timeout=15)
        for item in r.json().get('msgArray', []):
            sym = item.get('c', '')
            if sym: result[sym] = item
        if otc_syms:
            r2 = requests.get(base, params={'ex_ch': '|'.join(otc_syms), 'json': 1, 'delay': 0},
                               headers=HEADERS, timeout=15)
            for item in r2.json().get('msgArray', []):
                sym = item.get('c', '')
                if sym: result[sym] = item
    except Exception as e:
        print(f'[ERROR] fetch_quotes_batch: {e}')
    return result


# ── 升級版信心分（多維度）────────────────────────────────
def calc_confidence(changePct, volume, avg_vol, foreign_net, max_foreign, taiex_pct):
    """
    信心分 0-100（純算法，無 LLM）
    法人籌碼  35%：外資淨買占市場最大值比例
    量能放大  25%：今日量 / 市場平均量（最多2倍）
    漲幅合理  20%：1-9%最佳，漲跌停/平盤扣分
    相對強弱  20%：跑贏大盤加分
    """
    # 法人分
    if max_foreign > 0 and foreign_net > 0:
        inst_score = min(foreign_net / max_foreign, 1.0) * 100
    elif foreign_net < 0:
        inst_score = max(50 + foreign_net / max_foreign * 50, 0) if max_foreign > 0 else 30
    else:
        inst_score = 50

    # 量能分
    vol_ratio = volume / avg_vol if avg_vol > 0 else 1
    vol_score = min(vol_ratio / 2, 1.0) * 100  # 2倍均量=滿分

    # 漲幅合理分
    if 1 <= changePct < 9.5:
        pct_score = min(changePct / 5, 1.0) * 100  # 1-5%爬升，5%以上滿分
    elif changePct >= 9.5:
        pct_score = 60  # 漲停，明天可能繼續也可能回落
    elif changePct > 0:
        pct_score = 40  # 微漲
    elif changePct == 0:
        pct_score = 30
    elif changePct > -5:
        pct_score = 20
    else:
        pct_score = 10

    # 相對強弱
    rel_score = 70 if changePct > taiex_pct else 40

    conf = int(inst_score * 0.35 + vol_score * 0.25 + pct_score * 0.20 + rel_score * 0.20)
    return max(0, min(100, conf))

def conf_label(score):
    if score >= 80: return '🚀 強力看多', '#00c853'
    if score >= 65: return '📈 偏多',     '#69f0ae'
    if score >= 50: return '⏸️ 中性觀望', '#ffd740'
    if score >= 35: return '📉 偏空',     '#ff6d00'
    return '🚨 強力看空', '#d50000'


# ── 主掃描 ────────────────────────────────────────────
def scan():
    config    = load_config()
    watchlist = config.get('watchlist', [])
    symbols   = [s if isinstance(s, str) else s.get('symbol', '') for s in watchlist]
    symbols   = [s for s in symbols if s]

    now = datetime.datetime.now(TW_TZ)

    print('  📈 大盤指數 + 市場情緒...')
    index_data = fetch_index()
    taiex_pct  = index_data.get('changePctNum', 0)

    print('  📊 漲跌排行...')
    all_stocks, avg_vol = fetch_movers()
    all_map = {x['s']: x for x in all_stocks}

    print('  🏭 產業分類...')
    industry_map = fetch_industry_map()
    for sym, m in all_map.items():
        m['industry'] = industry_map.get(sym, m.get('industry', '其他'))

    print('  🏆 法人買賣超...')
    inst_map = fetch_institutional_raw()

    # 計算最大外資淨買（用於正規化）
    max_foreign = max((abs(v['foreign']) for v in inst_map.values()), default=1)

    # 為 all_map 裡每支股票計算升級信心分
    for sym, m in all_map.items():
        inst = inst_map.get(sym, {})
        m['conf'] = calc_confidence(
            m['changePct'], m['volume'], avg_vol,
            inst.get('foreign', 0), max_foreign, taiex_pct
        )
        m['foreign'] = inst.get('foreign', 0)

    print(f'  🔍 個股報價 ({len(symbols)} + {len(THEME_STOCKS)} 題材股)...')
    quotes = fetch_quotes_batch(symbols) if symbols else {}

    # 產業優先用 openapi，少數補用即時報價 i 欄
    industry_cache = dict(industry_map)
    for sym, item in quotes.items():
        ic = str(item.get('i', '')).strip()
        if ic:
            industry_cache[sym] = get_industry(ic)

    # 法人 Top10
    inst_stocks = [{'s': sym, **inst_map[sym], 'conf': all_map.get(sym, {}).get('conf', 50),
                    'changePct': all_map.get(sym, {}).get('changePct', 0),
                    'price': all_map.get(sym, {}).get('price', 0),
                    'n': all_map.get(sym, {}).get('n', sym)}
                   for sym in inst_map if len(sym) == 4 and sym.isdigit()]

    top_buy  = sorted(inst_stocks, key=lambda x: x['total'], reverse=True)[:10]
    top_sell = sorted(inst_stocks, key=lambda x: x['total'])[:10]

    def fmt_shares(v):
        lots = shares_to_lots(v)
        return ('+' if lots >= 0 else '') + format(lots, ',') + '張'

    def build_inst(lst, side):
        result = []
        for i, x in enumerate(lst):
            ind = industry_cache.get(x['s'], all_map.get(x['s'], {}).get('industry', ''))
            sg  = '+' if x['changePct'] >= 0 else ''
            dir_ = 'up' if x['changePct'] >= 0 else 'dn'
            result.append({
                'r': str(i+1), 's': x['s'], 'n': x['n'],
                'industry': ind,
                'foreign': fmt_shares(x['foreign']),
                'trust':   fmt_shares(x['trust']),
                'total':   fmt_shares(x['total']),
                'conf':    x['conf'],
                'price':   str(x['price']),
                'chg':     f"{sg}{x['changePct']}%",
                'dir':     dir_
            })
        return result

    top_buy_out  = build_inst(top_buy, 'up')
    top_sell_out = build_inst(top_sell, 'dn')

    # 漲跌排行
    gainers = sorted([x for x in all_stocks if x['changePct'] > 0],
                     key=lambda x: x['changePct'], reverse=True)[:10]
    losers  = sorted([x for x in all_stocks if x['changePct'] < 0],
                     key=lambda x: x['changePct'])[:10]

    def fmt_mover(rank, m):
        sg = '+' if m['changePct'] >= 0 else ''
        ind = industry_cache.get(m['s'], '')
        return {
            'r': str(rank), 's': m['s'], 'n': m['n'],
            'industry': ind,
            'price': str(m['price']),
            'chg':  f"{sg}{m['changePct']}%",
            'vol':  f"{m['volume']/10000:.1f}萬",
            'conf': m.get('conf', 50),
            'dir':  'up' if m['changePct'] >= 0 else 'dn'
        }

    # 明日關注清單（法人今日買超 + 有漲但沒漲停 + 量能放大）
    tomorrow_watch = []
    for sym, m in all_map.items():
        if not (len(sym) == 4 and sym.isdigit()): continue
        inst = inst_map.get(sym, {})
        foreign_net = inst.get('foreign', 0)
        if (foreign_net > 0 and
            0 < m['changePct'] < 9.5 and
            m['volume'] > avg_vol * 1.2):
            ind = industry_cache.get(sym, '')
            conf = m.get('conf', 50)
            tomorrow_watch.append({
                's': sym, 'n': m['n'], 'industry': ind,
                'price': m['price'],
                'chg': ('+' if m['changePct'] >= 0 else '') + f"{m['changePct']}%",
                'foreign': ('+' if foreign_net >= 0 else '') + format(shares_to_lots(foreign_net), ',') + '張',
                'conf': conf
            })

    tomorrow_watch = sorted(tomorrow_watch, key=lambda x: x['conf'], reverse=True)[:8]

    # 技術突破型關注清單（蓄勢待發）
    # 邏輯：52週低位 + RSI中低位翻轉 + 今日爆量 + 收紅 → 主力悄悄建倉訊號
    tech_watch = []
    for sym, m in all_map.items():
        if not (len(sym) == 4 and sym.isdigit()): continue
        rsi = m.get('rsi', 50)
        week52pos = m.get('week52Pos', 50)
        vol_ratio = m.get('volRatio5d', 1)
        chg_pct = m['changePct']
        price = m.get('price', 0)
        ind = industry_cache.get(sym, '')
        
        # 篩選條件：
        # 1. 52週位置低於45%（低基期）
        # 2. RSI 在 35-60（不超買，有動能空間）
        # 3. 量比 >= 1.8（今日量高於近期均量）
        # 4. 今日收紅（0 < changePct < 9.5，未漲停）
        # 5. 不在避開名單
        avoid_syms = {x['s'] for x in avoid_list}
        if (week52pos < 45 and
            35 <= rsi <= 60 and
            vol_ratio >= 1.8 and
            0 < chg_pct < 9.5 and
            sym not in avoid_syms):
            # 評分：條件越多越高分
            score = 0
            if week52pos < 30: score += 30
            elif week52pos < 40: score += 20
            else: score += 10
            if 40 <= rsi <= 55: score += 25  # RSI 黃金區間
            elif 35 <= rsi < 40: score += 15
            else: score += 10
            if vol_ratio >= 3: score += 25
            elif vol_ratio >= 2: score += 15
            else: score += 5
            if chg_pct >= 2: score += 20
            elif chg_pct >= 1: score += 10
            
            inst = inst_map.get(sym, {})
            foreign_net = inst.get('foreign', 0)
            foreign_str = ''
            if foreign_net > 0:
                foreign_str = '+' + format(shares_to_lots(foreign_net), ',') + '張'
                score += 10  # 法人也有買，加分
            
            tech_watch.append({
                's': sym, 'n': m['n'], 'industry': ind,
                'price': price,
                'chg': ('+' if chg_pct >= 0 else '') + f"{chg_pct}%",
                'rsi': round(rsi, 1),
                'week52Pos': round(week52pos, 1),
                'volRatio': round(vol_ratio, 1),
                'foreign': foreign_str,
                'score': score
            })
    
    tech_watch = sorted(tech_watch, key=lambda x: x['score'], reverse=True)[:8]

    # 強勢延續型（今日漲幅 ≥5%，RSI/量能支撐，明日可能繼續）
    momentum_watch = []
    for sym, m in all_map.items():
        if not (len(sym) == 4 and sym.isdigit()): continue
        chg_pct = m['changePct']
        rsi = m.get('rsi', 50)
        vol_ratio = m.get('volRatio5d', 1)
        price = m.get('price', 0)
        ind = industry_cache.get(sym, '')
        avoid_syms = {x['s'] for x in avoid_list}
        if (5 <= chg_pct < 9.5 and
            rsi <= 75 and          # RSI 未過熱
            vol_ratio >= 1.5 and   # 量能支撐
            sym not in avoid_syms):
            score = 0
            if chg_pct >= 8: score += 30
            elif chg_pct >= 6: score += 20
            else: score += 10
            if rsi <= 65: score += 20   # RSI 有空間繼續漲
            if vol_ratio >= 3: score += 25
            elif vol_ratio >= 2: score += 15
            else: score += 5
            inst = inst_map.get(sym, {})
            foreign_net = inst.get('foreign', 0)
            foreign_str = ''
            if foreign_net > 0:
                foreign_str = '+' + format(shares_to_lots(foreign_net), ',') + '張'
                score += 15  # 法人買超加分（強勢+法人 = 最強訊號）
            week52pos = m.get('week52Pos', 50)
            if week52pos < 70: score += 10  # 非高位，有繼續空間
            momentum_watch.append({
                's': sym, 'n': m['n'], 'industry': ind,
                'price': price,
                'chg': f"+{chg_pct}%",
                'rsi': round(rsi, 1),
                'week52Pos': round(week52pos, 1),
                'volRatio': round(vol_ratio, 1),
                'foreign': foreign_str,
                'score': score
            })
    momentum_watch = sorted(momentum_watch, key=lambda x: x['score'], reverse=True)[:8]

    # 漲停板預備隊（今日漲停 ≥9.5%，明日二板候選）
    limit_up_watch = []
    for sym, m in all_map.items():
        if not (len(sym) == 4 and sym.isdigit()): continue
        chg_pct = m['changePct']
        price = m.get('price', 0)
        vol_ratio = m.get('volRatio5d', 1)
        week52pos = m.get('week52Pos', 50)
        ind = industry_cache.get(sym, '')
        avoid_syms = {x['s'] for x in avoid_list}
        if (chg_pct >= 9.5 and sym not in avoid_syms):
            score = 0
            if chg_pct >= 9.8: score += 30  # 真漲停（封板）
            else: score += 15
            if vol_ratio >= 5: score += 30   # 封板量大，籌碼集中
            elif vol_ratio >= 3: score += 20
            else: score += 10
            if week52pos < 60: score += 20   # 非歷史高位，有繼續空間
            elif week52pos < 80: score += 10
            inst = inst_map.get(sym, {})
            foreign_net = inst.get('foreign', 0)
            foreign_str = ''
            if foreign_net > 0:
                foreign_str = '+' + format(shares_to_lots(foreign_net), ',') + '張'
                score += 20  # 漲停+法人買 = 強烈二板訊號
            limit_up_watch.append({
                's': sym, 'n': m['n'], 'industry': ind,
                'price': price,
                'chg': f"+{chg_pct}%",
                'rsi': round(m.get('rsi', 50), 1),
                'week52Pos': round(week52pos, 1),
                'volRatio': round(vol_ratio, 1),
                'foreign': foreign_str,
                'score': score
            })
    limit_up_watch = sorted(limit_up_watch, key=lambda x: x['score'], reverse=True)[:8]

    # priceMap（題材/類股用）
    price_map = {}
    for sym, item in quotes.items():
        price = safe_float(item.get('z', 0))
        prev  = safe_float(item.get('y', 0))
        if not price:
            m = all_map.get(sym)
            if m: price, pct, chg = m['price'], m['changePct'], m['change']
            else: continue
        else:
            chg = round(price - prev, 2) if prev else 0
            pct = round(chg / prev * 100, 2) if prev else 0
        sg = '+' if pct >= 0 else ''
        price_map[sym] = {
            'price': price, 'change': chg, 'changePct': pct,
            'chgStr': f'{sg}{pct}%', 'dir': 'up' if pct >= 0 else 'dn',
            'industry': industry_cache.get(sym, '')
        }
    for sym, m in all_map.items():
        if sym not in price_map:
            sg = '+' if m['changePct'] >= 0 else ''
            price_map[sym] = {
                'price': m['price'], 'change': m['change'], 'changePct': m['changePct'],
                'chgStr': f"{sg}{m['changePct']}%", 'dir': 'up' if m['changePct'] >= 0 else 'dn',
                'industry': industry_cache.get(sym, m.get('industry', ''))
            }

    # 自選股卡
    stocks = []
    for sym in symbols:
        item = quotes.get(sym)
        if item:
            price = safe_float(item.get('z', 0))
            prev  = safe_float(item.get('y', 0))
            high  = safe_float(item.get('h', 0))
            low   = safe_float(item.get('l', 0))
            vol   = int(safe_float(item.get('tv', 0)))
            name  = item.get('n', sym)
            chg   = round(price - prev, 2) if price and prev else 0
            pct   = round(chg / prev * 100, 2) if prev else 0
            ind   = get_industry(item.get('i', ''))
        elif sym in all_map:
            m = all_map[sym]
            price, pct, chg, high, low, vol, name = (
                m['price'], m['changePct'], m['change'], 0, 0, int(m['volume']), m['n'])
            ind = industry_cache.get(sym, '')
        else:
            print(f'  [WARN] 查無 {sym}')
            continue
        inst = inst_map.get(sym, {})
        conf = calc_confidence(pct, vol/1000 if vol > 1000 else vol, avg_vol,
                               inst.get('foreign', 0), max_foreign, taiex_pct)
        label, color = conf_label(conf)
        stocks.append({
            'symbol': sym, 'name': name, 'category': ind,
            'price': price, 'high': high, 'low': low,
            'change': chg, 'changePct': pct,
            'volume': vol, 'confidence': conf,
            'confidenceLabel': label, 'confidenceColor': color,
            'updatedAt': now.strftime('%H:%M')
        })

    # 避開清單
    avoid_list = []
    for sym, m in all_map.items():
        if not (len(sym) == 4 and sym.isdigit()): continue
        inst = inst_map.get(sym, {})
        reason = ""
        if inst.get('total', 0) < -10000: reason = "法人大賣"
        elif m['changePct'] < -5: reason = "大跌"
        elif m['volume'] > avg_vol * 2 and m['changePct'] < 0: reason = "爆量長黑"
        
        if reason:
            avoid_list.append({
                's': sym, 'n': m['n'], 'industry': industry_cache.get(sym, ''),
                'price': m['price'], 'changePct': m['changePct'],
                'reason': reason, 'conf': m.get('conf', 50)
            })
    avoid_list = sorted(avoid_list, key=lambda x: x['changePct'])[:8]

    # 量能異常榜
    vol_surge = []
    market_avg_vol = avg_vol # 這裡的 avg_vol 是全市場平均成交量(張)
    for sym, m in all_map.items():
        if not (len(sym) == 4 and sym.isdigit()): continue
        vol_ratio = m['volume'] / market_avg_vol if market_avg_vol > 0 else 0
        if vol_ratio > 3 and m['changePct'] > 0:
            vol_surge.append({
                's': sym, 'n': m['n'], 'industry': industry_cache.get(sym, ''),
                'price': m['price'], 'changePct': m['changePct'],
                'volRatio': round(vol_ratio, 1), 'conf': m.get('conf', 50)
            })
    vol_surge = sorted(vol_surge, key=lambda x: x['volRatio'], reverse=True)[:8]

    # 最強/最弱類股
    sector_map = {}
    sector_stocks_map = {}  # 新增：收集各類股個股
    for sym, m in all_map.items():
        ind = m.get('industry')
        if not ind or ind == '其他': continue
        if ind not in sector_map:
            sector_map[ind] = []
            sector_stocks_map[ind] = []
        sector_map[ind].append(m['changePct'])
        sector_stocks_map[ind].append({
            's': sym, 'n': m.get('n', ''),
            'chg': ('+' if m['changePct'] >= 0 else '') + f"{m['changePct']}%",
            'changePct': m['changePct'],
            'price': m.get('price', 0)
        })
    
    sector_strength = []
    for ind, pcts in sector_map.items():
        avg_pct = round(sum(pcts) / len(pcts), 2)
        # 取該類股漲幅前5支
        stocks_sorted = sorted(sector_stocks_map.get(ind, []), key=lambda x: x['changePct'], reverse=True)
        top_stocks = stocks_sorted[:5]
        sector_strength.append({
            'industry': ind, 'avgPct': avg_pct, 'stocks_count': len(pcts),
            'dir': 'up' if avg_pct >= 0 else 'dn',
            'topStocks': top_stocks
        })
    
    sector_strength = sorted(sector_strength, key=lambda x: x['avgPct'], reverse=True)
    top_sectors = sector_strength[:5]
    bot_sectors = sorted(sector_strength, key=lambda x: x['avgPct'])[:5]
    sector_out = top_sectors + bot_sectors

    print('  📰 抓取財經新聞...')
    news = fetch_news()

    print('  🔥 動態題材分析...')
    themes = []
    try:
        # 用 sectorStrength 找漲幅前5
        top_sects = sorted([s for s in sector_out if s['dir'] == 'up'], key=lambda x: x['avgPct'], reverse=True)[:5]
        # 用 top_buy 找法人最愛類股
        inst_sects = {}
        for s in top_buy:
            ind = industry_cache.get(s['s'], '')
            if ind:
                if ind not in inst_sects: inst_sects[ind] = []
                inst_sects[ind].append(s)
        
        theme_names = set([s['industry'] for s in top_sects]) | set(inst_sects.keys())
        icon_map = {'半導體':'⚡', '金融保險':'🏦', '航運':'🚢', '電子':'💻', '生技醫療':'💊'}
        
        for name in list(theme_names)[:6]:
            sect_data = next((s for s in sector_out if s['industry'] == name), {'avgPct': 0})
            reason = "法人買超" if name in inst_sects else "強勢上漲"
            # 該類股內的 topBuy 股票
            sect_top = [s for s in top_buy if industry_cache.get(s['s']) == name][:3]
            avg_conf = sum([s['conf'] for s in sect_top]) / len(sect_top) if sect_top else 50
            
            themes.append({
                'name': name,
                'icon': icon_map.get(name, '📊'),
                'avgPct': sect_data['avgPct'],
                'conf': round(avg_conf, 1),
                'reason': reason,
                'topStocks': [{
                    's': s['s'], 'n': s.get('n', s['s']),
                    'conf': s.get('conf', 50),
                    'chg': ('+' if s.get('changePct', 0) >= 0 else '') + str(s.get('changePct', 0)) + '%'
                } for s in sect_top]
            })
    except Exception as e:
        print(f'[WARN] themes analysis: {e}')

    # 更新 stocks 指標
    for s in stocks:
        indices = fetch_stock_indicators(s['symbol'])
        s.update(indices)

    payload = {
        'stocks':        stocks,
        'sectors':       config.get('sectors', []),
        'index':         index_data,
        'news':          news,
        'themes':        themes,
        'topBuy':        top_buy_out,
        'topSell':       top_sell_out,
        'gainers':       [fmt_mover(i+1, m) for i, m in enumerate(gainers)],
        'losers':        [fmt_mover(i+1, m) for i, m in enumerate(losers)],
        'tomorrowWatch': tomorrow_watch,
        'techWatch': tech_watch,
        'momentumWatch': momentum_watch,
        'limitUpWatch': limit_up_watch,
        'volSurge':      vol_surge,
        'sectorStrength': sector_out,
        'avoidList':     avoid_list,
        'priceMap':      price_map,
        'updatedAt':     now.isoformat(),
        'marketOpen':    is_market_open(),
        'source':        'TWSE'
    }

    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f'✅ 完成：個股{len(stocks)} / 法人Top{len(top_buy_out)} / '
          f'漲Top{len(gainers)} / 明日關注{len(tomorrow_watch)} / '
          f'爆量{len(vol_surge)} / 避開{len(avoid_list)} / '
          f'priceMap{len(price_map)} / 情緒{index_data.get("mood","?")}')

    return payload


def is_market_open():
    now = datetime.datetime.now(TW_TZ)
    if now.weekday() >= 5: return False
    t = now.time()
    return datetime.time(9, 0) <= t <= datetime.time(13, 30)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'scan'
    if cmd == 'scan':
        print('🔍 開始掃描...')
        scan()
