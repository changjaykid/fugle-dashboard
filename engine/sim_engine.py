import json
import datetime
from pathlib import Path

WORKSPACE = Path("/Users/kid/.openclaw/workspace")
FUGLE_DIR = WORKSPACE / "fugle-dashboard"
DASHBOARD_JSON = FUGLE_DIR / "docs" / "dashboard.json"
SIM_PORTFOLIO_JSON = FUGLE_DIR / "docs" / "sim_portfolio.json"
STOCK_PICKS_JSON = WORKSPACE / "memory" / "stock-picks.json"
TW_TZ = datetime.timezone(datetime.timedelta(hours=8))


def load_json(path, default):
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"[WARN] load_json {path.name}: {e}")
    return default


def days_between(start_date, end_date):
    try:
        s = datetime.date.fromisoformat(start_date)
        e = datetime.date.fromisoformat(end_date)
        return max((e - s).days, 0)
    except Exception:
        return 0


def get_recent_picks(picks_data, today_str):
    if not isinstance(picks_data, list):
        return []
    today = datetime.date.fromisoformat(today_str)
    candidates = {today_str, (today - datetime.timedelta(days=1)).isoformat()}
    for entry in reversed(picks_data):
        if isinstance(entry, dict) and entry.get('date') in candidates:
            return entry.get('picks', []) or []
    return []


def update_sim_portfolio():
    print("🔄 更新模擬倉...")

    if not DASHBOARD_JSON.exists():
        print("⚠️ dashboard.json 不存在，跳過模擬倉更新")
        return

    dashboard = load_json(DASHBOARD_JSON, {})
    portfolio = load_json(SIM_PORTFOLIO_JSON, {"holdings": [], "history": [], "updatedAt": ""})
    picks_data = load_json(STOCK_PICKS_JSON, [])

    price_map = dashboard.get('priceMap', {})
    updated_at = dashboard.get('updatedAt', datetime.datetime.now(TW_TZ).isoformat())
    today_str = datetime.datetime.now(TW_TZ).date().isoformat()

    portfolio.setdefault('holdings', [])
    portfolio.setdefault('history', [])

    recent_picks = get_recent_picks(picks_data, today_str)
    existing_symbols = {h.get('s') for h in portfolio['holdings']}

    for pick in recent_picks:
        try:
            sym = str(pick.get('symbol', '')).strip()
            if not sym or sym in existing_symbols or sym not in price_map:
                continue
            current_price = price_map[sym].get('price')
            if not current_price:
                continue
            portfolio['holdings'].append({
                "s": sym,
                "n": pick.get('name', sym),
                "entry_date": today_str,
                "entry_price": current_price,
                "current_price": current_price,
                "conf": pick.get('conf', 0),
                "days": 0,
                "profit_pct": 0.0
            })
            existing_symbols.add(sym)
            print(f"📈 模擬進場: {sym} {pick.get('name', sym)} @ {current_price}")
        except Exception as e:
            print(f"[WARN] 模擬進場失敗: {e}")

    new_holdings = []
    for h in portfolio['holdings']:
        try:
            sym = h.get('s')
            current_price = price_map.get(sym, {}).get('price', h.get('current_price', h.get('entry_price', 0)))
            h['current_price'] = current_price
            entry_price = float(h.get('entry_price', 0) or 0)
            if entry_price <= 0:
                continue
            h['days'] = days_between(h.get('entry_date', today_str), today_str)
            profit_pct = (current_price - entry_price) / entry_price * 100
            h['profit_pct'] = round(profit_pct, 2)

            exit_reason = None
            if h['profit_pct'] <= -8:
                exit_reason = "止損 (-8%)"
            elif h['profit_pct'] >= 15:
                exit_reason = "止盈 (+15%)"
            elif h['days'] > 5:
                exit_reason = "時間到 (>5日)"

            if exit_reason:
                closed = dict(h)
                closed['exit_date'] = today_str
                closed['exit_price'] = current_price
                closed['reason'] = exit_reason
                portfolio['history'].insert(0, closed)
                print(f"📉 模擬出場: {sym} {h.get('n','')} 損益 {h['profit_pct']}% 原因: {exit_reason}")
            else:
                new_holdings.append(h)
        except Exception as e:
            print(f"[WARN] 模擬持倉更新失敗: {e}")
            new_holdings.append(h)

    portfolio['holdings'] = new_holdings
    portfolio['updatedAt'] = updated_at

    with open(SIM_PORTFOLIO_JSON, 'w', encoding='utf-8') as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)
    print("✅ 模擬倉更新完成")


if __name__ == "__main__":
    update_sim_portfolio()
