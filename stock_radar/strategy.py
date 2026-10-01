"""Entry geometry, exit geometry and position size. Python decides; the weekly review only argues.

The valuation engine says what a stock is worth and buy_plan says where to buy it. Neither of them ever
said when to sell, and no valuation in this database has ever carried a sell price: 5051 rows, 0 with
`sell`, `reduce` or `avoid` set. An agent that only buys cannot grow an account, so this is the missing
half.

Everything that could be argued about is a key in SETTINGS, and every key carries the measurement or the
constraint it came from. The weekly review reads the result and proposes changes to these numbers; it
cannot write them (same arrangement as the crypto brain, which the user chose).

Two measurements shaped this file, both taken on 2026-10-01 over the 106 priced entries this agent had
actually issued:

  Stop distance (entry to the 20-day low) has a median of 4.22% but a p10 of 0.84%, with 14 of 106 under
  1% and 3 of them negative -- the entry was already below the 20-day low. A raw "stop at support" is
  therefore sometimes narrower than the round-trip cost, so a floor is required.

  Target distance (entry to fair value) has a median of 33.22%. Buying at 85% of fair should only need
  17.6%, but the entry is a support somewhere below the buy cap, so it is usually much further. Asking
  a Taiwan equity for 33% means the target is rarely reached and the strategy becomes all stops and no
  targets. So the first target is a multiple of the risk taken, capped at fair value, rather than fair
  value itself.

Sizing is equal weight, not risk-parity, and that is a consequence of the account size rather than a
preference. At NT$100,000 the brokerage floor makes an order below NT$14,035 pay more than its rate
(costs.MIN_ORDER_VALUE), so the usable band for five positions is NT$14,035 to NT$20,000 -- narrow
enough that risk-parity sizing would reject roughly a third of entries for having a stop wider than
5.3%, purely because 0.75% of equity divided by that stop falls under the fee floor. Equal weight with a
risk ceiling keeps the same discipline without the infeasible region, and it still scales with the
account: every size below is a fraction of current equity, never a fixed amount.
"""
from . import costs

VERSION = 'stock-strategy-v1'

SETTINGS = {
    # --- Size -------------------------------------------------------------------------------------
    # 100,000 / 14,035 = 7.1 fee-efficient positions, so five leaves headroom and keeps each position
    # large enough to matter. Raising this makes every order smaller and the fee floor bite sooner.
    'max_positions': 5,
    # Equal weight: each new position targets equity / max_positions.
    # The ceiling a single name may reach including gains, so one winner cannot become the account.
    'max_position_fraction': 0.30,
    # If equal weight would risk more than this fraction of equity between entry and stop, the order is
    # cut down until it does not. At the measured median stop (4.22%) a 20% position risks 0.84%, so
    # this binds only on the wide-stop tail rather than on ordinary entries.
    'max_risk_fraction': 0.015,
    # The most a position may pay to get in and out again. At a 1.5R target on an 8% stop the trade is
    # playing for a 12% move, and even a coin-flip win rate on that is about +2% a trade gross, so a 1%
    # round trip takes half of it -- that is the limit of what is worth placing. Below this the order is
    # refused and the reason names the rate, rather than a value that would hide why.
    'max_cost_rate': 0.01,
    # --- Stop -------------------------------------------------------------------------------------
    # The structural stop is the 20-day low, which is the same level buy_plan already calls `support`
    # and already treats as invalidation. These two bound it.
    # Floor: chosen from how far these entries actually go against you before working, measured over 872
    # of them on 2026-10-01. Within 20 trading days the adverse excursion exceeds 4% on 40% of entries,
    # 6% on 29%, 8% on 23%, 10% on 16% and 12% on 11%. A 4% stop is therefore hit by ordinary noise two
    # times in five, and each hit pays the round trip. 8% is where the stop stops being an accident
    # without becoming the whole position. This is a measurement of the entries, not of the returns.
    'min_stop_fraction': 0.08,
    # Ceiling: the p90 of that same 20-day adverse excursion is 12.4%. Past it the stop is wider than
    # almost all noise, and equal weight would breach max_risk_fraction, so the entry is refused rather
    # than quietly resized into something too small to place.
    'max_stop_fraction': 0.12,
    # --- Target -----------------------------------------------------------------------------------
    # How a position is left. This is a structural choice, not a number, and the replay over
    # 2026-03-24..09-30 says it matters far more than any parameter:
    #   'target_stop' a fixed target at a multiple of the risk, with the structural stop below.
    #   'fair'        no stop below the entry; sell when the price reaches the valuation's fair value.
    #   'trail'       sell at fair, but give back no more than `trail_fraction` from the peak.
    #   'time'        no stop and no target: the position is held for max_holding_days and then sold.
    #                 This is how the cost of the stop gets separated from the cost of the selection.
    #   'hold'        never sell. Diagnostic only: it measures the entry rule on its own.
    'exit_mode': 'target_stop',
    # A multiple of the risk actually taken, for 'target_stop'. At an 8% stop this asks for a 12% move,
    # against a measured median distance to fair value of 33% -- so it is reachable, which larger
    # multiples are not. The replay's best cell also sits here; that is a coincidence and not evidence,
    # because the cell next to it (1.75) returns -1.4% on 22 trades.
    'target_r_multiple': 1.5,
    # For 'trail': how much of the best price reached may be given back before leaving.
    'trail_fraction': 0.10,
    # The valuation's own fair value is a ceiling on the target: this agent does not forecast above it.
    'cap_target_at_fair': True,
    # --- Hold -------------------------------------------------------------------------------------
    # Dead capital is a cost that no stop measures. 0 disables the time stop.
    'max_holding_days': 60,
    # Once a position has gained this much, the stop moves up to the entry plus its round-trip cost, so
    # a winner cannot become a loser. Off: across the 108-cell replay, 0.08 was actively worse than
    # either neighbour (median +2.54% against +7.40% off and +8.61% at 0.15), and off is
    # indistinguishable from 0.15, so the ratchet earns nothing and costs a moving part.
    'breakeven_after_gain': 0,
    # Starting capital for the simulated account. The user's first real deposit is NT$100,000, so the
    # simulation runs the same size: a strategy that only works at a size you will not fund is not a
    # result. Every other size in this file is a fraction of current equity, so the account scales.
    'starting_cash': 100000,
}


def stop_for(entry, support, settings=None):
    """The exit that says the reason for the entry is gone, or None when it cannot be placed.

    `support` is the level buy_plan used. It is widened to the floor when it is too close to be
    distinguishable from costs, and refused when it is so far that an equal-weight position would
    breach the risk ceiling."""
    s = {**SETTINGS, **(settings or {})}
    if not entry or entry <= 0:
        return None
    raw = (entry - support) / entry if support and support < entry else 0.0
    fraction = max(raw, s['min_stop_fraction'])
    if fraction > s['max_stop_fraction']:
        return None
    return {'price': round(entry * (1 - fraction), 4), 'fraction': round(fraction, 5),
            'widened': fraction > raw}


def target_for(entry, stop_price, fair=None, settings=None):
    """The first exit that pays for the risk. A multiple of the distance to the stop, never above fair."""
    s = {**SETTINGS, **(settings or {})}
    if not entry or not stop_price or stop_price >= entry:
        return None
    risk = entry - stop_price
    price = entry + risk * s['target_r_multiple']
    capped = False
    if s['cap_target_at_fair'] and fair and fair > entry and price > fair:
        price, capped = fair, True
    return {'price': round(price, 4), 'r_multiple': round((price - entry) / risk, 3),
            'move_pct': round((price / entry - 1) * 100, 3), 'capped_at_fair': capped}


def plan_for(entry, support, fair=None, settings=None):
    """Entry, stop and target together, or None with the reason it cannot be traded."""
    s = {**SETTINGS, **(settings or {})}
    if s['exit_mode'] in ('fair', 'trail', 'hold', 'time'):
        # No stop below the entry, so there is no stop distance to refuse an entry for. 'fair' aims at
        # the valuation's fair value and needs one; a trailing stop needs no target at all, and demanding
        # one silently bought nothing at all under the strength entry model, which has no fair value.
        if s['exit_mode'] == 'fair' and not (fair and fair > entry):
            return None, '沒有高於進場價的合理價，無法設定出場'
        return {'entry': entry, 'stop': None, 'target': fair if fair else None,
                'stop_fraction': None, 'stop_widened': False,
                'r_multiple': None,
                'target_move_pct': round((fair / entry - 1) * 100, 3) if fair else None,
                'target_capped_at_fair': True, 'exit_mode': s['exit_mode']}, None
    stop = stop_for(entry, support, s)
    if not stop:
        return None, f"停損距離超過 {s['max_stop_fraction']:.0%}，等價格靠近支撐再評估"
    target = target_for(entry, stop['price'], fair, s)
    if not target:
        return None, '無法定出有效目標價'
    # A target that cannot clear its own costs is not a target.
    floor = costs.rate_at(s['max_position_fraction'] * 100000)
    if target['move_pct'] <= floor:
        return None, f"目標僅 {target['move_pct']:.2f}%，不足以覆蓋來回成本 {floor:.2f}%"
    return {'entry': entry, 'stop': stop['price'], 'target': target['price'],
            'stop_fraction': stop['fraction'], 'stop_widened': stop['widened'],
            'r_multiple': target['r_multiple'], 'target_move_pct': target['move_pct'],
            'target_capped_at_fair': target['capped_at_fair'], 'exit_mode': s['exit_mode']}, None


def size_for(equity, cash, entry, stop_price, settings=None, kind='stock'):
    """Shares to buy, or 0 with the reason none can be.

    Equal weight, reduced to respect the risk ceiling, clipped to the cash actually available, and
    refused outright below the order size at which the brokerage floor stops mattering. The share count
    is exact: a multiple of 1,000 is a lot order and anything else is an odd-lot order, which is how the
    account can hold a NT$18,000 position in a NT$600 stock at all."""
    s = {**SETTINGS, **(settings or {})}
    if not entry or entry <= 0:
        return 0, '進場價無效'
    if stop_price is not None and stop_price >= entry:
        return 0, '停損價不低於進場價'
    budget = min(equity / s['max_positions'], equity * s['max_position_fraction'])
    if stop_price:
        # With a stop, the risk is known and capped. Without one (exit_mode 'fair'/'trail'/'hold') the
        # whole position is at risk in principle, so equal weight is the only limit there is -- which is
        # a reason to prefer a stop, not a reason to size as if one existed.
        budget = min(budget, (equity * s['max_risk_fraction']) / ((entry - stop_price) / entry))
    shares = int(budget / entry)
    if shares < 1:
        return 0, f"單股 {entry:g} 元已超過單一部位上限 {budget:,.0f} 元"
    # The fee is charged on top, so the cash test uses the real outgoing amount.
    while shares > 0 and costs.buy_cost(shares, entry)['cash'] > cash:
        shares -= 1
    if shares < 1:
        return 0, f"現金 {cash:,.0f} 元不足一股"
    # The test is the round-trip cost RATE, not the order value, because a position above one lot is two
    # orders and pays the NT$20 floor twice. 1,875 shares of a NT$10 stock costs 0.73% and buys NT$18,750;
    # dropping the 875-share tail to make it one order costs 0.70% and buys NT$10,000 -- nearly the same
    # rate for 47% less position, so the value test would have chosen worse. Whole lots are tried only as
    # a fallback, and if neither clears the ceiling the entry is refused with the rate that failed.
    lots, odd = divmod(shares, costs.LOT)
    ceiling = s['max_cost_rate'] * 100
    for count in [shares] + ([lots * costs.LOT] if lots and odd else []):
        rate = costs.rate_at(count * entry, kind, shares=count)
        if rate <= ceiling:
            return count, None
    worst = costs.rate_at(shares * entry, kind, shares=shares)
    return 0, (f"下單 {shares} 股（{shares * entry:,.0f} 元）來回成本 {worst:.2f}%，"
               f"超過上限 {ceiling:.2f}%；手續費低標 {costs.FEE_FLOOR:.0f} 元在這個金額下決定一切")


def exit_check(position, bar, held_days, settings=None):
    """Why this position should be closed today, or None to hold. Checked against one day's own bar.

    Order matters and is deliberately pessimistic: when a bar spans both the stop and the target there
    is no way to know from a daily bar which came first, so the stop is taken. A backtest that resolves
    that ambiguity in its own favour is the main way a paper record flatters itself."""
    s = {**SETTINGS, **(settings or {})}
    mode = position.get('plan', {}).get('exit_mode') or s['exit_mode']
    high, low = bar[1], bar[2]
    if mode == 'hold':
        return None
    if mode == 'time':
        # Nothing but the clock. Falls through to the holding check below.
        pass
    stop = None if mode == 'time' else (position.get('stop_now') or position.get('stop'))
    if stop and low <= stop:
        return {'reason': 'stop' if mode == 'target_stop' else 'trail',
                'price': min(stop, bar[0]), 'why': f"跌破停損 {stop:g}"}
    target = None if mode == 'time' else position.get('target')
    if target and high >= target:
        return {'reason': 'target', 'price': max(target, bar[0]),
                'why': f"達到目標 {target:g}"}
    # Trading days, counted from the calendar rather than an index into a rolling cache: the live
    # account outlives the 250-day price cache, so a position's age cannot be a cache offset.
    if s['max_holding_days'] and held_days is not None and held_days >= s['max_holding_days']:
        return {'reason': 'time', 'price': bar[3],
                'why': f"持有 {held_days} 個交易日未達目標也未停損，資金另有用途"}
    return None


def ratchet(position, bar, settings=None):
    """Update the live stop. Never downwards: a stop that can loosen is not a stop.

    'target_stop' moves it to breakeven-plus-costs once the position has earned the right.
    'trail' keeps it a fixed distance under the best price the position has seen."""
    s = {**SETTINGS, **(settings or {})}
    mode = position.get('plan', {}).get('exit_mode') or s['exit_mode']
    if mode in ('hold', 'time'):
        return None
    entry = position['entry']
    current = position.get('stop_now') or position.get('stop')
    position['peak'] = max(position.get('peak') or entry, bar[1])
    if mode == 'trail':
        trailed = round(position['peak'] * (1 - s['trail_fraction']), 4)
        return max(current or 0, trailed) or None
    if mode != 'target_stop' or not s['breakeven_after_gain'] or not current:
        return current
    if bar[1] >= entry * (1 + s['breakeven_after_gain']):
        protected = entry * (1 + costs.rate_at(position['shares'] * entry) / 100)
        return max(current, round(protected, 4))
    return current
