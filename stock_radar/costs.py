"""What a Taiwan equity round trip actually costs. Every return this agent reports is net of this.

The tracking ledger reported gross percentages and said so in a footnote, which is how a +1.35% week
survives contact with reality as something closer to +0.9%. Nothing downstream of here is allowed to
quote a return that has not been through `round_trip`.

Three terms, and the third is the one that decides position size on a small account:

  fee   0.1425% of consideration, each side, before the broker's discount.
  tax   securities transaction tax, sell side only. 0.3% on ordinary shares, 0.1% on domestic equity
        ETFs. (Bond ETFs are exempt; this agent holds neither.)
  floor most brokers will not charge less than NT$20 for an order, discount or not. Below about
        NT$14,035 of consideration the floor is larger than the rate, so a small order's real fee rate
        rises as the order shrinks: NT$5,000 pays 0.40%, NT$2,000 pays 1.00%. `rate_at` states it
        plainly and `MIN_ORDER_VALUE` is derived from it rather than chosen.

FEE_DISCOUNT defaults to 1.0 -- no discount -- because overstating costs cannot produce a strategy that
looks profitable and is not. Set it to the broker's actual factor (e.g. 0.28 for 2.8 折) in
`.runtime` config once confirmed; the same goes for FEE_FLOOR if the broker's minimum is not NT$20.
"""
FEE_RATE = 0.001425
FEE_DISCOUNT = 1.0
FEE_FLOOR = 20.0
TAX_RATE = {'stock': 0.003, 'etf_equity': 0.001, 'etf_other': 0.001}

# The consideration at which the rate finally exceeds the floor. Below it, every extra dollar of
# position is free, so an order smaller than this is paying for nothing.
MIN_ORDER_VALUE = round(FEE_FLOOR / (FEE_RATE * FEE_DISCOUNT), 2)


def fee(value, discount=FEE_DISCOUNT, floor=FEE_FLOOR):
    """Brokerage on one side. Taiwan brokers truncate to the dollar, so this does too."""
    return max(float(floor), float(int(value * FEE_RATE * discount)))


def tax(value, kind):
    if kind not in TAX_RATE:
        raise ValueError('不支援的標的類型，無法計算證交稅')
    return float(int(value * TAX_RATE[kind]))


def buy_cost(shares, price, **kw):
    """Cash leaving the account to acquire `shares`: consideration plus fee. No tax on the buy side."""
    value = shares * price
    return {'value': round(value, 2), 'fee': fee(value, **kw), 'tax': 0.0,
            'cash': round(value + fee(value, **kw), 2)}


def sell_cost(shares, price, kind, **kw):
    """Cash arriving: consideration less fee and transaction tax."""
    value = shares * price
    f, t = fee(value, **kw), tax(value, kind)
    return {'value': round(value, 2), 'fee': f, 'tax': t, 'cash': round(value - f - t, 2)}


def round_trip(shares, entry, exit_price, kind, **kw):
    """The only honest statement of a completed trade: cash in, cash out, and the return on cash."""
    bought, sold = buy_cost(shares, entry, **kw), sell_cost(shares, exit_price, kind, **kw)
    profit = sold['cash'] - bought['cash']
    return {'cost_in': bought['cash'], 'cash_out': sold['cash'],
            'fees': bought['fee'] + sold['fee'], 'tax': sold['tax'],
            'profit': round(profit, 2), 'return_pct': round(profit / bought['cash'] * 100, 4),
            'gross_pct': round((exit_price / entry - 1) * 100, 4)}


def rate_at(value, kind='stock', **kw):
    """The real round-trip cost of an order of this size, as a percentage. Used for sizing, and for
    saying why an order was refused rather than silently placing one that cannot win."""
    if value <= 0:
        return float('inf')
    return round((fee(value, **kw) * 2 + tax(value, kind)) / value * 100, 4)


def breakeven_move(value, kind='stock', **kw):
    """How far the price has to travel before the trade is worth nothing, in percent."""
    return rate_at(value, kind, **kw)
