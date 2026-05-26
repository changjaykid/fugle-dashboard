import json
import datetime
import os
from pathlib import Path

# Paths
WORKSPACE = Path("/Users/kid/.openclaw/workspace")
FUGLE_DIR = WORKSPACE / "fugle-dashboard"
DASHBOARD_JSON = FUGLE_DIR / "docs" / "dashboard.json"
SIM_PORTFOLIO_JSON = FUGLE_DIR / "docs" / "sim_portfolio.json"
STOCK_PICKS_JSON = WORKSPACE / "memory" / "stock-picks.json"

def update_sim_portfolio():
    print("🔄 更新模擬倉...")
    
    # 1. Ensure files exist
    if not STOCK_PICKS_JSON.exists():
        STOCK_PICKS_JSON.parent.mkdir(parents=True, exist_ok=True)
        with open(STOCK_PICKS_JSON, 'w') as f:
            json.dump([], f)
    
    if not DASHBOARD_JSON.exists():
        print("⚠️ dashboard.json 不存在，跳過模擬倉更新")
        return

    with open(DASHBOARD_JSON, 'r') as f:
        dashboard = json.load(f)
    
    price_map = dashboard.get('priceMap', {})
    updated_at = dashboard.get('updatedAt', datetime.datetime.now().isoformat())
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')

    if not SIM_PORTFOLIO_JSON.exists():
        portfolio = {"holdings": [], "history": [], "updatedAt": updated_at}
    else:
        with open(SIM_PORTFOLIO_JSON, 'r') as f:
            portfolio = json.load(f)

    with open(STOCK_PICKS_JSON, 'r') as f:
        picks_list_all = json.load(f)

    # 2. Process stock-picks.json to enter new positions
    # 讀最近一筆 picks
    if picks_list_all and isinstance(picks_list_all, list):
        latest_entry = picks_list_all[-1]
        picks_list = latest_entry.get('picks', [])
        
        for pick in picks_list:
            sym = pick.get('symbol')
            if not sym: continue
            
            # Check if we have a price today and not already in holdings
            if sym in price_map and not any(h['s'] == sym for h in portfolio['holdings']):
                price_info = price_map[sym]
                current_price = price_info['price']
                
                new_holding = {
                    "s": sym,
                    "n": pick.get('name', sym),
                    "entry_date": today_str,
                    "entry_price": current_price,
                    "current_price": current_price,
                    "conf": pick.get('conf', 0),
                    "days": 0
                }
                portfolio['holdings'].append(new_holding)
                print(f"📈 模擬進場: {sym} {new_holding['n']} @ {current_price}")


    # 3. Update existing holdings
    new_holdings = []
    for h in portfolio['holdings']:
        sym = h['s']
        if sym in price_map:
            h['current_price'] = price_map[sym]['price']
        
        # Calculate profit %
        profit_pct = (h['current_price'] - h['entry_price']) / h['entry_price'] * 100
        h['profit_pct'] = round(profit_pct, 2)
        h['days'] += 1
        
        # Check exit conditions
        # 虧 > 8%、獲利 > 15%、或已持有 > 5 日
        exit_reason = None
        if h['profit_pct'] <= -8: exit_reason = "止損 (-8%)"
        elif h['profit_pct'] >= 15: exit_reason = "止盈 (+15%)"
        elif h['days'] >= 5: exit_reason = "時間到 (5日)"
        
        if exit_reason:
            h['exit_date'] = today_str
            h['exit_price'] = h['current_price']
            h['reason'] = exit_reason
            portfolio['history'].insert(0, h)
            print(f"📉 模擬出場: {h['s']} {h['n']} 損益 {h['profit_pct']}% 原因: {exit_reason}")
        else:
            new_holdings.append(h)
            
    portfolio['holdings'] = new_holdings
    portfolio['updatedAt'] = updated_at

    with open(SIM_PORTFOLIO_JSON, 'w') as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)
    print("✅ 模擬倉更新完成")

if __name__ == "__main__":
    update_sim_portfolio()
