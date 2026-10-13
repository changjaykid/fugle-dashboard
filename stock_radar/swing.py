"""The one set of entry and exit rules: buy a pullback at the lower band, sell it at the upper band.

Chosen by the user on 2026-10-13 after twenty-five selection methods and three entry styles against six
exit styles were measured over 2022-08 .. 2026-09 (STOCK_FLOW_REVIEW.md section 12, code in
tools/selection_study/). What it replaces, Strategy v1's breakout, bought the close of a breakout day and
priced a target from the width of the base -- a level the stock had often never traded at -- and measured
no better than drawing ten names at random.

The rule, in the order it is applied:

  Universe   listed ordinary shares, NT$20m a day over twenty days, price at least NT$10, 120 sessions of
             history, nothing under a disposition or abnormal-trading notice.
  Market     no new orders while 0050 closes under its own 120-day average. Every method measured did
             worse in those stretches, this one included.
  Trend      above the 60-day average, the 20-day above the 60-day, and a 60-day return better than the
             equal-weight liquid market's.
  Pullback   3-10% under its 20-day high, within 3% of the 20-day average, on volume no heavier than its
             own median: a rest inside a trend, not a break of it.
  Money      foreign plus investment-trust net buying over the last five sessions, ranked by that buying
             as a share of what traded. On its own this measured as noise; as a condition on a pullback
             it was the difference between this rule and the plain pullback.
  Entry      a limit at the lower Bollinger band (20 days, 2 deviations), never above the close, resting
             five sessions. If the price never comes down to it, nothing is bought.
  Exit       reaching the upper band (recomputed each evening) does not sell: it arms a trailing exit,
             and the position is sold at the close once it has fallen 3% from the best price since it
             was bought. A 10% stop under the price paid and forty sessions at most cover the rest. A bar
             that spans the stop is the stop.

Measured, net of a full round trip (0.585%), bear stretches excluded, per trade:
  this rule                +1.41%   61-74% winners by year   about 17 sessions held
  random entries, same     +0.11%
  2023 +1.09  2024 +0.01  2025 +2.23  2026 +5.95   (2022 had 31 trades and is not counted)
2024 was flat and 2026 was unusually good, so the average flatters it.

The exit. The user asked for both a high win rate and a large gain per trade. Selling at the band won
68% of the time but cut the winners short (+1.41%); holding twenty sessions earned more (+1.63%) and won
half the time. Letting a position that reached the band run until it gives back 3% from its best price
measured between them on win rate and above both on money (same entries and stop, 2022-08 .. 2026-07):

  sell at the band             +1.41%  67.6% won  16.7 sessions  0.084%/session
  band, then 3% off the high   +1.88%  61.6% won  18.6 sessions  0.101%/session   <- this
  band, then 5% off the high   +2.06%  54.5% won  20.8 sessions  0.099%/session
  hold twenty sessions         +1.63%  50.3% won  18.1 sessions  0.090%/session
  2023 +2.19 (64%)  2024 +0.16 (54%)  2025 +2.39 (68%)  2026 +6.85 (68%)

This file, replayed over the same days by tools/swing_replay.py with prices on the tick grid: +2.00% a
trade, 62.3% won, 18.7 sessions, 1,283 trades; 2023 +2.29, 2024 +0.24, 2025 +2.45, 2026 +7.05.

Every function here takes plain lists so the nightly scan, the account and the replay share one
arithmetic.
"""
import statistics

from . import costs

VERSION = 'swing-v1'

SETTINGS = {
    'min_dollar_volume': 20_000_000,
    'min_price': 10.0,
    'min_history': 120,
    'market_symbol': '0050',
    'market_average': 120,
    'pullback_min': 0.03,
    'pullback_max': 0.10,
    'near_average': 0.03,
    'max_relative_volume': 1.0,
    'flow_days': 5,
    'band_days': 20,
    'band_width': 2.0,
    'order_days': 5,          # sessions a limit rests before it is withdrawn
    'stop_fraction': 0.10,
    'max_holding_days': 40,
    'min_target_gain': 0.006, # a band this close to the price paid cannot pay the round trip
    'give_back': 0.03,        # once the band is reached, sell at a close this far under the best price
    'picks': 10,
}


def _filled(closes):
    """Closes with a missing session carried forward, so averages do not skip suspended days."""
    out, last = [], None
    for c in closes:
        last = c if c else last
        out.append(last)
    return out


def bands(closes, settings=None):
    """(lower, middle, upper) of the Bollinger band on the last `band_days` closes, or None."""
    s = {**SETTINGS, **(settings or {})}
    window = [c for c in _filled(closes)[-s['band_days']:] if c]
    if len(window) < s['band_days']:
        return None
    middle = statistics.fmean(window)
    spread = statistics.pstdev(window) * s['band_width']
    return middle - spread, middle, middle + spread


def market_open_for_entries(index_closes, settings=None):
    """True when 0050 closes above its own 120-day average. New orders are placed only then."""
    s = {**SETTINGS, **(settings or {})}
    closes = [c for c in _filled(index_closes) if c]
    if len(closes) < s['market_average']:
        return False
    return closes[-1] > statistics.fmean(closes[-s['market_average']:])


def features(bars, flows):
    """What the rule reads for one symbol on the last day of `bars`.

    bars: [open, high, low, close, lots] or None per session, oldest first, ending on the day.
    flows: [foreign_lots, trust_lots] or None per session, aligned to the same sessions."""
    if len(bars) < 61 or not bars[-1] or not bars[-1][3]:
        return None
    closes = _filled([b[3] if b else None for b in bars])
    if closes[-61] is None:
        return None
    close = closes[-1]
    lots = [(b[4] or 0) if b else 0 for b in bars]
    turnover = [(b[3] * b[4] * 1000) for b in bars[-20:] if b and b[3] and b[4] is not None]
    prior = lots[-21:-1]
    median_prior = statistics.median(prior) if prior else 0
    recent = [f for f in flows[-SETTINGS['flow_days']:]]
    bought = sum(((f[0] or 0) + (f[1] or 0)) for f in recent if f)
    traded = sum(lots[-SETTINGS['flow_days']:])
    highs = [b[1] for b in bars[-20:] if b and b[1]]
    band = bands(closes)
    return {
        'close': close,
        'history': sum(1 for b in bars if b and b[3]),
        'ma20': statistics.fmean(closes[-20:]),
        'ma60': statistics.fmean(closes[-60:]),
        'high_20': max(highs) if highs else None,
        'return_60': close / closes[-61] - 1,
        'dollar_volume_20': statistics.fmean(turnover) if turnover else 0.0,
        'relative_volume': (lots[-1] / median_prior) if median_prior else None,
        'flow_lots': bought,
        'flow_share': (bought / traded) if traded else None,
        'band': band,
    }


# How many of judge()'s tests a stock passed: NEAR is in a trend, stronger than the market and inside the
# pullback zone; PASSED is all of them.
NEAR, PASSED = 8, 12


def judge(f, market_return_60, risk=None, settings=None):
    """(True, why, PASSED) for a buy, or (False, why not, how far it got). The first failed test is the reason.

    How far it got is the number of tests passed. A stock that reached `NEAR` -- in a trend, stronger than
    the market, inside the pullback zone -- and failed only on where it sits, its volume or its buyers is
    what the page calls close; one that failed earlier is not close to anything."""
    s = {**SETTINGS, **(settings or {})}
    if not f:
        return False, '歷史資料不足', 0
    if f['history'] < s['min_history']:
        return False, f"上市不足 {s['min_history']} 個交易日", 1
    if f['dollar_volume_20'] < s['min_dollar_volume']:
        return False, f"20 日均成交金額 {f['dollar_volume_20'] / 1e6:.0f}M 不足 {s['min_dollar_volume'] / 1e6:.0f}M", 2
    if f['close'] < s['min_price']:
        return False, f"股價低於 {s['min_price']:g} 元", 3
    if risk and (risk.get('events') or risk.get('cleared') is not True):
        return False, '處置、異常交易或重大事件待確認', 4
    if not (f['close'] > f['ma60'] and f['ma20'] > f['ma60']):
        return False, '不在上升趨勢（季線之下或月線未在季線之上）', 5
    if market_return_60 is not None and f['return_60'] <= market_return_60:
        return False, f"60 日漲幅 {f['return_60']:.1%} 未強於大盤 {market_return_60:.1%}", 6
    depth = (f['high_20'] - f['close']) / f['high_20'] if f['high_20'] else None
    if depth is None or not (s['pullback_min'] <= depth <= s['pullback_max']):
        return False, f"距 20 日高點 {depth or 0:.1%}，不在 {s['pullback_min']:.0%}～{s['pullback_max']:.0%} 的回檔區", 7
    if abs(f['close'] / f['ma20'] - 1) > s['near_average']:
        return False, f"離月線 {f['close'] / f['ma20'] - 1:+.1%}，不在月線附近", 8
    if f['relative_volume'] is None or f['relative_volume'] > s['max_relative_volume']:
        return False, f"回檔量能 {f['relative_volume'] or 0:.1f} 倍，賣壓未縮", 9
    if f['flow_lots'] <= 0:
        return False, f"近 {s['flow_days']} 日法人賣超 {f['flow_lots']:,.0f} 張", 10
    if not f['band']:
        return False, '布林通道資料不足', 11
    return True, (f"上升趨勢中回檔 {depth:.1%}、貼近月線，量縮 {f['relative_volume']:.1f} 倍；"
                  f"近 {s['flow_days']} 日法人買超 {f['flow_lots']:,.0f} 張（佔成交 {f['flow_share']:.1%}）"), PASSED


TICKS = ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0), (float('inf'), 5.0))


def tick(price, down=True):
    """A price the exchange will accept: TWSE tick sizes, rounded down (buy limits, stops) or to nearest."""
    step = next(size for bound, size in TICKS if price < bound)
    count = int(price / step + 1e-9) if down else round(price / step)
    return round(count * step, 2)


def plan(f, settings=None):
    """The order: a limit at the lower band (never above the close), the upper band it aims at, the stop.

    Every price is on the exchange's tick grid, because an order the broker would reject is not a plan."""
    s = {**SETTINGS, **(settings or {})}
    lower, middle, upper = f['band']
    limit = tick(min(lower, f['close']))
    target = tick(upper, down=False)
    return {'limit': limit, 'target': target, 'middle': round(middle, 2),
            'stop': tick(limit * (1 - s['stop_fraction'])),
            'target_pct': round((target / limit - 1) * 100, 2),
            'below_close_pct': round((1 - limit / f['close']) * 100, 2)}


def exit_today(position, bar, held, target, settings=None):
    """(reason, price, why) when the position leaves today, or None to hold. Updates `position`.

    `position` carries 'entry', and this keeps 'peak' (the best high since the fill) and 'armed' (the
    upper band has been reached) on it. `target` is the upper band as of the previous close, a level the
    market could see before the session. `held` counts sessions from the fill, the fill day being 1, and
    nothing exits on that day. The stop is tested before anything else: a daily bar cannot say whether
    its low came before its high."""
    s = {**SETTINGS, **(settings or {})}
    if not bar or not bar[3]:
        return None
    entry = position['entry']
    open_, high, low, close = bar[0] or bar[3], bar[1] or bar[3], bar[2] or bar[3], bar[3]
    position['peak'] = max(position.get('peak') or entry, high)
    if held <= 1:
        return None
    stop = position.get('stop') or entry * (1 - s['stop_fraction'])
    if low <= stop:
        return 'stop', min(stop, open_), f"跌破停損 {stop:.2f}"
    if target and target > entry * (1 + s['min_target_gain']) and high >= target:
        position['armed'] = True
    if position.get('armed') and close < position['peak'] * (1 - s['give_back']):
        return 'target', close, (f"已碰布林上緣，自高點 {position['peak']:g} 回落 "
                                 f"{1 - close / position['peak']:.1%}，收盤賣出")
    if held >= s['max_holding_days']:
        return 'time', close, f"持有 {held} 個交易日，時間到出場"
    return None


def fill(bar, limit):
    """The price a resting buy limit got on this bar, or None. Below the open it fills at the open."""
    if not bar or not bar[2] or bar[2] > limit:
        return None
    return min(limit, bar[0]) if bar[0] else limit


def scan(prices, flows, instruments, risk=lambda symbol: None, settings=None):
    """Every judged stock for the last day of `prices`: buys first, ranked by buying share.

    prices: [(day, {symbol: bar})] oldest first, at least 121 sessions. flows: [(day, {symbol: [f, t]})].
    instruments: {symbol: {'name', 'industry', ...}} -- the ordinary shares to consider."""
    s = {**SETTINGS, **(settings or {})}
    days = [d for d, _ in prices]
    flow_by_day = dict(flows)
    window = prices[-(s['min_history'] + 1):]
    index_closes = [(t.get(s['market_symbol']) or [None] * 4)[3] for _, t in prices[-s['market_average']:]]
    gate = market_open_for_entries(index_closes, s)
    rows = {}
    for sym in instruments:
        bars = [t.get(sym) for _, t in window]
        own = [flow_by_day.get(d, {}).get(sym) for d in days[-len(window):]]
        rows[sym] = features(bars, own)
    liquid = [f['return_60'] for f in rows.values()
              if f and f['dollar_volume_20'] >= s['min_dollar_volume']]
    market_60 = statistics.fmean(liquid) if liquid else None
    traded = sum(1 for sym in instruments if prices[-1][1].get(sym))
    buys, waits, tally = [], [], {'universe': traded, 'judged': 0, 'buy': 0}
    for sym, f in rows.items():
        if not f:
            continue
        tally['judged'] += 1
        ok, why, reached = judge(f, market_60, risk(sym), s)
        inst = instruments[sym]
        row = {'symbol': sym, 'name': inst.get('name') or sym,
               'industry': inst.get('industry_code') or inst.get('industry') or '',
               'close': f['close'], 'why': why, 'flow_lots': f['flow_lots'],
               'flow_share': f['flow_share'], 'relative_volume': f['relative_volume'], 'reached': reached}
        if ok:
            buys.append({**row, **plan(f, s)})
        elif reached >= NEAR:
            waits.append(row)
    buys.sort(key=lambda r: -(r['flow_share'] or 0))
    waits.sort(key=lambda r: (-r['reached'], -(r['flow_share'] or 0)))
    tally['buy'] = len(buys)
    return {'version': VERSION, 'day': days[-1], 'market_open': gate,
            'market_return_60': market_60, 'tally': tally,
            'candidates': buys[:s['picks']] if gate else [],
            'held_back': [] if gate else buys[:s['picks']],
            'near': waits[:10], 'settings': s}


def target_for(bars, settings=None):
    """The upper band as of the last bar: tomorrow's sell limit for a position already held."""
    b = bands([x[3] if x else None for x in bars], settings)
    return round(b[2], 2) if b else None


def follow(prices, k, symbol, limit, settings=None):
    """What one order placed at the close of session k did afterwards, from stored bars alone.

    {'status': 'unfilled'} when the limit never traded within `order_days`, {'status': 'open'} when it
    filled and has not left yet, or the closed trade with its net return after a full round trip. Used
    by the journal and the replay, so the forward record and the backtest are one arithmetic."""
    s = {**SETTINGS, **(settings or {})}
    cost = costs.FEE_RATE * 2 + costs.TAX_RATE['stock']
    last = min(k + 1 + s['order_days'], len(prices))
    for j in range(k + 1, last):
        price = fill(prices[j][1].get(symbol), limit)
        if price is None:
            continue
        held, position = 0, {'entry': price}
        for m in range(j, len(prices)):
            bar = prices[m][1].get(symbol)
            if not bar:
                continue
            held += 1
            back = [prices[x][1].get(symbol) for x in range(max(0, m - 25), m)]
            out = exit_today(position, bar, held, target_for(back, s), s)
            if out:
                return {'status': 'closed', 'fill_day': prices[j][0], 'fill': price,
                        'exit_day': prices[m][0], 'exit': round(out[1], 2), 'how': out[0],
                        'held': held, 'net_pct': round((out[1] / price - 1 - cost) * 100, 2)}
        return {'status': 'open', 'fill_day': prices[j][0], 'fill': price, 'held': held}
    return {'status': 'unfilled' if last - (k + 1) >= s['order_days'] else 'pending'}
