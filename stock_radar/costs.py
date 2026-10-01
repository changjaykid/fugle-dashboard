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
# Fugle (user-confirmed 2026-10-01): NT$20 a round-lot order, NT$1 an odd-lot order. The difference is
# not a detail. A NT$20 floor made every position under NT$14,035 pay more than its rate, which is why
# a NT$100,000 account could hold about seven names; at NT$1 that threshold is NT$702, so it can hold as
# many as it can sensibly follow. The concentration this agent was stuck with -- five names, unable to
# catch a return distribution whose mean is made by a few large winners -- was a consequence of a fee
# floor that does not apply to how it would actually trade.
FEE_FLOOR = 20.0        # round lots, and after-hours fixed-price orders
FEE_FLOOR_ODD = 1.0     # odd lots (盤中零股)
TAX_RATE = {'stock': 0.003, 'etf_equity': 0.001, 'etf_other': 0.001}

# The consideration at which the rate finally exceeds the floor. Below it, every extra dollar of
# position is free, so an order smaller than this is paying for nothing.
MIN_ORDER_VALUE = round(FEE_FLOOR / (FEE_RATE * FEE_DISCOUNT), 2)
MIN_ODD_ORDER_VALUE = round(FEE_FLOOR_ODD / (FEE_RATE * FEE_DISCOUNT), 2)


LOT = 1000


def fee(value, discount=FEE_DISCOUNT, floor=FEE_FLOOR):
    """Brokerage on ONE order. Taiwan brokers truncate to the dollar, so this does too."""
    return max(float(floor), float(int(value * FEE_RATE * discount)))


def order_split(shares):
    """The orders a share count really becomes. Round lots and odd lots trade in separate books.

    2,034 shares is not one order: it is two round lots and 34 odd shares, placed separately, and the
    NT$20 floor applies to each. The 34-share tail of a NT$10 stock is NT$340 of stock paying NT$20 of
    fee, which is 5.9% -- so this is not a labelling detail, it is the difference between a position that
    can pay for itself and one that cannot."""
    lots, odd = divmod(int(shares), LOT)
    return [n for n in (lots * LOT, odd) if n]


def fee_for(shares, price, **kw):
    """Brokerage for a position of `shares`, across however many orders that really is.

    Each component carries its own floor: the round-lot part NT$20, the odd-lot part NT$1. A 2,034-share
    position is two orders and pays both, which is NT$21 rather than the NT$40 a single floor implied."""
    floor = kw.pop('floor', None)
    total = 0.0
    for n in order_split(shares):
        own = floor if floor is not None else (FEE_FLOOR if n % LOT == 0 else FEE_FLOOR_ODD)
        total += fee(n * price, floor=own, **kw)
    return total


def order_type(shares):
    lots, odd = divmod(int(shares), LOT)
    return 'lot' if lots and not odd else 'odd_lot' if odd and not lots else 'mixed'


def tax(value, kind):
    if kind not in TAX_RATE:
        raise ValueError('不支援的標的類型，無法計算證交稅')
    return float(int(value * TAX_RATE[kind]))


def buy_cost(shares, price, **kw):
    """Cash leaving the account to acquire `shares`: consideration plus fee. No tax on the buy side."""
    value = shares * price
    f = fee_for(shares, price, **kw)
    return {'value': round(value, 2), 'fee': f, 'tax': 0.0, 'cash': round(value + f, 2),
            'orders': order_split(shares), 'order_type': order_type(shares)}


def sell_cost(shares, price, kind, **kw):
    """Cash arriving: consideration less fee and transaction tax."""
    value = shares * price
    f, t = fee_for(shares, price, **kw), tax(value, kind)
    return {'value': round(value, 2), 'fee': f, 'tax': t, 'cash': round(value - f - t, 2),
            'orders': order_split(shares), 'order_type': order_type(shares)}


def round_trip(shares, entry, exit_price, kind, **kw):
    """The only honest statement of a completed trade: cash in, cash out, and the return on cash."""
    bought, sold = buy_cost(shares, entry, **kw), sell_cost(shares, exit_price, kind, **kw)
    profit = sold['cash'] - bought['cash']
    return {'cost_in': bought['cash'], 'cash_out': sold['cash'],
            'fees': bought['fee'] + sold['fee'], 'tax': sold['tax'],
            'profit': round(profit, 2), 'return_pct': round(profit / bought['cash'] * 100, 4),
            'gross_pct': round((exit_price / entry - 1) * 100, 4)}


def rate_at(value, kind='stock', shares=None, **kw):
    """The real round-trip cost of a position of this size, as a percentage. Used for sizing, and for
    saying why an order was refused rather than silently placing one that cannot win.

    Given `shares` it accounts for the position really being two orders, each with its own floor;
    without it, it assumes a single round-lot order, which is the conservative reading."""
    if value <= 0:
        return float('inf')
    charge = (fee_for(shares, value / shares, **kw) if shares else fee(value, **kw)) * 2
    return round((charge + tax(value, kind)) / value * 100, 4)


def breakeven_move(value, kind='stock', **kw):
    """How far the price has to travel before the trade is worth nothing, in percent."""
    return rate_at(value, kind, **kw)
