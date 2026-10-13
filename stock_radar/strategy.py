"""How much to buy. The entry and exit rules live in swing.py; this is the sizing they share.

Sizing is equal weight with a risk ceiling, and that is a consequence of the account size rather than a
preference. Every size below is a fraction of current equity, never a fixed amount, so the account
scales. The brokerage floor is what decides small orders (costs.py): at NT$1 an odd-lot order the
account can hold as many names as it can sensibly follow, so the limit is the slot count.
"""
from . import costs

SETTINGS = {
    # Twenty slots at equal weight: 5% of equity each. The scan proposes up to ten a day and each order
    # rests five sessions, so the account is rarely short of candidates and never one name heavy.
    'max_positions': 20,
    # The ceiling a single name may reach including gains, so one winner cannot become the account.
    'max_position_fraction': 0.30,
    # Equal weight is cut down if it would risk more than this between entry and stop. At the 10% stop
    # a 5% position risks 0.5%, so this binds only if the slot count is lowered a long way.
    'max_risk_fraction': 0.015,
    # The most a position may pay to get in and out again, as a fraction of its value. The rule's
    # measured average is +1.88% a trade after a 0.585% round trip; an order that costs more than 1%
    # is giving away half the edge to the fee floor.
    'max_cost_rate': 0.01,
    # The user's first real deposit is NT$100,000, so the simulation runs that size.
    'starting_cash': 100000,
}


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
        # With a stop the risk is known, and capped here.
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
