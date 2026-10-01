"""What to buy, and at what price. Two models, so they can be compared instead of argued about.

The agent's original entry model -- buy what is cheap against its own one-year P/E or P/B median, at a
support level below today's close -- was measured on 2026-10-01 over 404 filled picks and beat an
equal-weight basket of the same universe on 41-45% of occasions, with a median excess return that was
negative at every horizon from five to sixty days. No exit rule repairs that, so this is where the fix
has to happen.

The factor scan that followed ranked the whole liquid universe by thirteen candidate signals every five
trading days and measured what each decile did next. (It also found a bug in its own first run: sorting
`(signal, forward_return)` pairs broke ties on the outcome, which manufactured a 10-point spread for
dividend yield out of a block of stocks all tied at zero. Ties now break on the symbol.)

At a twenty-day horizon four related formulations agreed, with roughly monotone decile curves:

    mom_20        top decile +3.47% excess, spread +7.37%, 10/13 dates positive
    range_pos_60  +3.68%, spread +6.68%, 10/13          <- top-decile MEDIAN +1.21%
    near_high_60  +2.62%, spread +5.59%, 13/13          <- top-decile MEDIAN +0.37%
    above_ma60    +2.00%, spread +4.87%, 11/13

Only the two range measures have a positive top-decile median, which is what matters when the portfolio
holds five names rather than fifty: a mean lifted by a few large winners is not something five positions
can be expected to capture. At sixty days every momentum measure reverses hard (mom_20's top decile
median goes to -12.21%), so whatever this is, it is short-lived.

That is the opposite of what the agent was doing, and it also condemns the old entry price. Resting a
limit below the close means that in an uptrend you are filled precisely when the trend breaks -- the
strategy was selecting for strength and then buying only the ones that stopped being strong.

None of this is established. Thirteen signal dates spanning sixty-five trading days, measured over
twenty- and sixty-day windows, overlap almost completely: the effective number of independent
observations is two or three, not thirteen. The whole window is one regime, a market that rose 27.92%
equal-weight, and momentum is exactly what works in a trend. So this is a hypothesis with a measured
direction, tested below against the incumbent on the same engine and the same costs, and the forward
paper record is what will actually settle it.
"""
import statistics

VERSION = 'entry-v2'

MODELS = ('value_band', 'strength')

# ---------------------------------------------------------------------------------------------------
# REJECTED, 2026-10-01. Kept so the hypothesis is not tested a third time, and because the way it failed
# is the useful part. Run end to end on the same book, the same bars and the same costs as the incumbent:
#
#   disjoint window     strength    value_band    random 5    equal-weight   0050
#   2026-03-25~05-13      (see below)   +3.23%      +3.08%      +14.95%     +26.05%
#   2026-05-14~06-30                    +7.59%      +6.12%      +10.60%     +13.85%
#   2026-07-01~08-17                    -0.14%      -2.25%       -4.50%      -4.07%
#   2026-08-18~09-30                    -1.50%      +1.20%       +5.56%      +6.82%
#
# The strength model returned +35.37% over the full window and looked like an answer, then returned
# +9.92%, -21.76%, -18.07%, -9.90% excess as the start date moved forward. One good segment, nothing
# after it.
#
# The incumbent value model is statistically indistinguishable from drawing five symbols at random from
# the same liquid universe, and both lose to an equal-weight basket of that universe in three windows of
# four. Five names cannot capture a right-skewed return distribution: the mean is made by a few large
# winners a five-slot portfolio will usually not own, and round-trip costs plus 8% stops take the rest.
#
# And the benchmark that actually matters is worse news. Buying 0050 and doing nothing returned +47.05%
# over the same 130 days against +25.03% for the equal-weight universe, because this was a large-cap-led
# market -- so a picker drawing from 600 liquid symbols was fishing in the weaker pond all along.
#
# Nothing here is deployed: `model` stays 'value_band', which is what production already does. What would
# change the answer is more history (the cache holds 250 days of one regime) and a broker fee that lets
# the account hold twenty names instead of five, not another signal.
# ---------------------------------------------------------------------------------------------------

SETTINGS = {
    # Which model picks and prices the entries. 'strength' is measured and rejected; see above.
    'model': 'value_band',
    # --- strength ---------------------------------------------------------------------------------
    # Where the close must sit inside its own 60-day range, as a fraction. 0.8 is roughly the top two
    # deciles of range_pos_60, which is as far as the measurement supports going: the top decile alone
    # is about fifty names, and asking for more than that is asking the thirteen dates for precision
    # they do not have.
    'min_range_position': 0.80,
    # And within a tenth of the 60-day high, which is the same idea measured a different way; requiring
    # both is a cheap way of not relying on either formulation alone.
    'max_distance_from_high': 0.10,
    # A stock cannot be strong on no participation. Twenty-day dollar volume against its own sixty-day
    # median: this is a sanity floor, not the dollar_vol factor (whose spread came from one decile and
    # was positive on only 46% of dates).
    'min_relative_turnover': 0.7,
    # How the order is filled. Measured the hard way: a limit at yesterday's close fills only when the
    # stock pulls back to it, so in an uptrend it systematically buys the names that stopped going up --
    # the same adverse selection the old support-limit rule suffered from, reproduced. A strength entry
    # has to be willing to pay the next open. 'limit' keeps the old behaviour for comparison.
    'fill': 'open',
    # Only for fill='limit': where to rest it, as a fraction of the close.
    'entry_limit_fraction': 1.0,
    # --- shared -----------------------------------------------------------------------------------
    # How many names the scan proposes each day, cheapest/strongest first. The message shows five.
    'slots': 5,
}


def turnover(bars):
    """Twenty-day dollar volume against the sixty-day median, or None when there is too little data."""
    values = [b[4] * b[3] for b in bars if b and b[3]]
    if len(values) < 40:
        return None
    recent = statistics.mean(values[-20:])
    base = statistics.median(values)
    return recent / base if base else None


def strength(bars):
    """Where today's close sits in its own sixty-day range, and how far it is from the high.

    `bars` is up to sixty daily [open, high, low, close, lots], oldest first, ending on the signal day."""
    usable = [b for b in bars if b and b[3]]
    if len(usable) < 40:
        return None
    close = usable[-1][3]
    high = max(b[1] for b in usable)
    low = min(b[2] for b in usable)
    if high <= low:
        return None
    recent = usable[-20:]
    return {'close': close, 'high_60': high, 'low_60': low,
            'low_20': min(b[2] for b in recent),
            'range_position': (close - low) / (high - low),
            'distance_from_high': (high - close) / high,
            'relative_turnover': turnover(usable)}


def strength_candidate(symbol, name, kind, bars, settings=None):
    """A strength entry, or None with the reason it is not one. Priced at the close, never below it."""
    s = {**SETTINGS, **(settings or {})}
    measure = strength(bars)
    if not measure:
        return None, '近 60 日有效交易日不足 40 天'
    if measure['range_position'] < s['min_range_position']:
        return None, (f"收盤位於 60 日區間的 {measure['range_position']:.0%}，"
                      f"低於 {s['min_range_position']:.0%}")
    if measure['distance_from_high'] > s['max_distance_from_high']:
        return None, f"距 60 日高點 {measure['distance_from_high']:.1%}，超過 {s['max_distance_from_high']:.0%}"
    pace = measure['relative_turnover']
    if pace is not None and pace < s['min_relative_turnover']:
        return None, f"近 20 日成交金額僅為 60 日中位的 {pace:.0%}，參與度不足"
    return {'symbol': symbol, 'name': name, 'kind': kind,
            'fill': s['fill'],
            'entry': round(measure['close'] * s['entry_limit_fraction'], 4),
            # The stop references the 20-day low, not the 60-day low. For a stock sitting at the top of
            # its 60-day range the 60-day low is a third of the price away, which is not a stop; the
            # nearest level that says "this stopped being strong" is where it has recently been.
            'support': measure['low_20'],
            'close': measure['close'], 'high_60': measure['high_60'],
            'low_20': measure['low_20'],
            'range_position': round(measure['range_position'], 4),
            'distance_from_high': round(measure['distance_from_high'], 4),
            'relative_turnover': None if pace is None else round(pace, 3),
            # Strength says nothing about fair value, so there is no fair value to cap a target with.
            'fair': None,
            'rank': measure['range_position']}, None
