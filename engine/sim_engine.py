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
            json.dump({}, f)
    
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
        picks_data = json.load(f)

    # 2. Process stock-picks.json to enter new positions
    # Look at last 5 days of picks
    dates = sorted(picks_data.keys(), reverse=True)[:5]
    
    modified_picks = False
    for date in dates:
        picks_list = picks_data[date]
        if not isinstance(picks_list, list): continue
        
        for pick in picks_list:
            if not isinstance(pick, dict): continue
            
            # If not entered yet and we have a price today
            if pick.get('entry_price') is None and pick.get('s') in price_map:
                price_info = price_map[pick['s']]
                current_price = price_info['price']
                
                # Check if already in holdings
                if not any(h['s'] == pick['s'] for h in portfolio['holdings']):
                    new_holding = {
                        "s": pick['s'],
                        "n": pick['n'],
                        "entry_date": today_str,
                        "entry_price": current_price,
                        "current_price": current_price,
                        "conf": pick.get('conf', 0),
                        "days": 0
                    }
                    portfolio['holdings'].append(new_holding)
                    
                    # Update stock-picks.json
                    pick['entry_price'] = current_price
                    modified_picks = True
                    print(f"📈 模擬進場: {pick['s']} {pick['n']} @ {current_price}")

    if modified_picks:
        with open(STOCK_PICKS_JSON, 'w') as f:
            json.dump(picks_data, f, ensure_ascii=False, indent=2)

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
