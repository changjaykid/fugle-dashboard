"""Strategy v1 selection: features, setup detection and a 100-point score.

Implements STOCK_SELECTION_TRADING_STRATEGY_V1.md. It replaces nothing yet -- the existing valuation
band still decides what production recommends -- because the point of writing it is to measure it
against that, against random selection and against the index, on the five years of history now cached.

Why this shape rather than the one already in the tree. The deployed rule ranks by how cheap a stock is
against its own one-year P/E median, and on 2026-10-01 that was measured over 404 filled picks: it beat
an equal-weight basket of its own universe on 41-45% of occasions, with a negative median excess at every
horizon from five to sixty days. The factor scan that followed found the signals that did separate the
universe at a twenty-day horizon were all measures of the same thing -- where the close sits in its own
sixty-day range (+6.68% top-to-bottom decile spread, top-decile median +1.21%), distance from the sixty-
day high (+5.59%, consistent on 13 of 13 dates), twenty-day momentum (+7.37%). That is the opposite of
buying what has fallen, and it is what this document describes.

Two rules this file follows, both from the document:

  Each component answers a different question. "Momentum is high" and "RSI is strong" and "it is above
  its moving average" are one fact counted three times, and a score built that way is a momentum score
  wearing a disguise. Structure, relative strength, participation, institutional flow, sector breadth,
  fundamentals and risk are seven different questions.

  A missing input is not a bad score. When a component cannot be computed -- no flow data for that day,
  no industry peers, no financials -- it is left out and the total is renormalised over the points that
  could be judged, with `scored_out_of` recording what that was. Scoring it zero would mean a stock is
  penalised for a gap in our data rather than for anything about the stock.

Selection is not entry. A score of 92 on a stock that has already gapped 8% away from its invalidation
is a WAIT, and that judgement belongs to the entry engine, not here.
"""
import statistics

VERSION = 'selection-v1'

WEIGHTS = {'structure': 25, 'relative_strength': 15, 'participation': 15,
           'institutional': 15, 'sector': 10, 'fundamental': 10, 'risk_quality': 10}

GRADES = ((85, 'A'), (75, 'B'), (65, 'C'))

SETTINGS = {
    # --- Hard filter (document section 4): exclusions, not deductions -------------------------------
    'min_dollar_volume': 20_000_000,   # NT$20m a day on a 20-day average: below this a NT$5,000 odd-lot
                                       # order is still fine, but the price is not trustworthy enough to
                                       # score, and the spread stops being a rounding error.
    'min_history_days': 120,           # enough for a 60-day structure plus a 60-day comparison
    'max_extension_atr': 7.0,          # close this many ATRs above the 20-day average is already gone
    'max_run_up_20d': 0.60,            # +60% in a month is not an early trend
    'max_upper_shadow': 0.55,          # of the day's range, on an expansion day: a failed push
    'max_gap': 0.045,                  # a 4.5% gap is tomorrow's problem, not today's entry
    # --- Setup detection (section 6) ---------------------------------------------------------------
    'base_lookback': 60,
    'base_max_width': 0.30,            # the consolidation's own high/low range
    'breakout_window': 5,              # the break must be recent to still be early
    'breakout_volume': 1.4,            # against the 20-day median on the breaking day
    'pullback_max_depth': 0.18,        # from the swing high; deeper stops being a pullback
    'pullback_min_depth': 0.02,
    'pullback_volume_max': 0.95,       # a healthy pullback trades less, not more
    # --- Scoring -----------------------------------------------------------------------------------
    'rs_windows': (5, 20, 60),
    'flow_days': 5,
    'min_scored_fraction': 0.70,       # refuse to grade a stock when under this share of points could
                                       # be judged: a 55-point total out of 55 available is not a score.
}


# --- small helpers ----------------------------------------------------------------------------------

def _closes(bars):
    return [b[3] for b in bars if b and b[3]]


def _atr(bars, n=14):
    """Average true range over the last n bars, in price. None when there is not enough."""
    usable = [b for b in bars if b and b[3]]
    if len(usable) < n + 1:
        return None
    spans = []
    for prev, cur in zip(usable[-n - 1:-1], usable[-n:]):
        spans.append(max(cur[1] - cur[2], abs(cur[1] - prev[3]), abs(cur[2] - prev[3])))
    return statistics.mean(spans) if spans else None


def _ratio(value, low, high):
    """Map value onto 0..1 across [low, high], clamped. The only shape used for scoring."""
    if value is None or high == low:
        return None
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _swings(bars, span=3):
    """Pivot highs and lows: a bar whose high (low) is the extreme of the window centred on it."""
    usable = [b for b in bars if b and b[3]]
    highs, lows = [], []
    for i in range(span, len(usable) - span):
        window = usable[i - span:i + span + 1]
        if usable[i][1] == max(x[1] for x in window):
            highs.append((i, usable[i][1]))
        if usable[i][2] == min(x[2] for x in window):
            lows.append((i, usable[i][2]))
    return highs, lows


# --- features ---------------------------------------------------------------------------------------

def features(bars, market_closes=None, sector_closes=None, flows=None, fundamentals=None):
    """Everything the score and the setups read, computed once.

    bars: up to `base_lookback`+ daily [open, high, low, close, lots], oldest first, ending on the day.
    market_closes / sector_closes: equal-weight index levels aligned to the same days.
    flows: [[foreign_lots, trust_lots], ...] aligned to the same days, or None when not available.
    """
    s = SETTINGS
    usable = [b for b in bars if b and b[3]]
    if len(usable) < s['min_history_days'] // 2:
        return None
    closes = _closes(usable)
    close = closes[-1]
    high60 = max(b[1] for b in usable[-60:])
    low60 = min(b[2] for b in usable[-60:])
    ma20 = statistics.mean(closes[-20:])
    ma60 = statistics.mean(closes[-60:]) if len(closes) >= 60 else None
    atr = _atr(usable)
    # market.py stores volume in lots, so turnover is lots x 1,000 shares x price. Getting this wrong
    # by the thousand made the liquidity floor ask for NT$20bn a day and excluded 1,075 of 1,084 stocks.
    volumes = [b[4] * 1000 * b[3] for b in usable if b[3]]
    vol20 = statistics.mean(volumes[-20:]) if len(volumes) >= 20 else None
    vol_median = statistics.median(volumes[-60:]) if len(volumes) >= 60 else None
    today = usable[-1]
    span = today[1] - today[2]
    highs, lows = _swings(usable)

    out = {
        'close': close, 'open': today[0], 'high': today[1], 'low': today[2],
        'high_60': high60, 'low_60': low60, 'ma20': ma20, 'ma60': ma60, 'atr': atr,
        'range_position': (close - low60) / (high60 - low60) if high60 > low60 else None,
        'distance_from_high': (high60 - close) / high60 if high60 else None,
        'dollar_volume_20': vol20,
        'relative_volume': (volumes[-1] / statistics.median(volumes[-21:-1])) if len(volumes) >= 21
                           and statistics.median(volumes[-21:-1]) else None,
        'volume_vs_median': (vol20 / vol_median) if vol20 and vol_median else None,
        'upper_shadow': ((today[1] - max(today[0], close)) / span) if span > 0 else 0.0,
        'gap': ((today[0] - closes[-2]) / closes[-2]) if len(closes) >= 2 and closes[-2] else None,
        'extension_atr': ((close - ma20) / atr) if atr else None,
        'run_up_20': (close / closes[-21] - 1) if len(closes) >= 21 and closes[-21] else None,
        'swing_highs': highs, 'swing_lows': lows,
        'above_ma20': close > ma20, 'above_ma60': (ma60 is not None and close > ma60),
    }

    # The base: the consolidation the stock is breaking out of, measured before the recent window.
    base = usable[-s['base_lookback']:-s['breakout_window']] if len(usable) > s['base_lookback'] else []
    if len(base) >= 15:
        bh, bl = max(b[1] for b in base), min(b[2] for b in base)
        out['base_high'], out['base_low'] = bh, bl
        out['base_width'] = (bh - bl) / bh if bh else None

    # Relative strength: the stock's own return minus the market's, over each window.
    out['rs'] = {}
    if market_closes and len(market_closes) >= max(s['rs_windows']) + 1:
        for w in s['rs_windows']:
            if len(closes) > w and closes[-w - 1] and market_closes[-w - 1]:
                out['rs'][w] = (closes[-1] / closes[-w - 1]) - (market_closes[-1] / market_closes[-w - 1])
    out['rs_sector'] = None
    if sector_closes and len(sector_closes) >= 21 and len(closes) >= 21 and sector_closes[-21]:
        out['rs_sector'] = (closes[-1] / closes[-21]) - (sector_closes[-1] / sector_closes[-21])

    # Institutional flow, normalised by the stock's own turnover so a large cap and a small cap compare.
    out['flow'] = None
    if flows and len(flows) >= s['flow_days'] and len(usable) >= s['flow_days']:
        recent = flows[-s['flow_days']:]
        net = sum((f[0] or 0) + (f[1] or 0) for f in recent)
        traded = sum(b[4] for b in usable[-s['flow_days']:]) or None
        if traded:
            out['flow'] = {'net_lots': net, 'share_of_volume': net / traded,
                           'days_positive': sum(1 for f in recent if (f[0] or 0) + (f[1] or 0) > 0)}

    out['fundamentals'] = fundamentals or None
    return out


# --- hard filter (document section 4) ---------------------------------------------------------------

def hard_filter(f, risk=None, settings=None):
    """The reason this stock is excluded outright, or None. Not a deduction -- an exclusion."""
    s = {**SETTINGS, **(settings or {})}
    if not f:
        return '歷史資料不足，無法計算特徵'
    if f['dollar_volume_20'] is None or f['dollar_volume_20'] < s['min_dollar_volume']:
        return f"20 日均成交金額 {(f['dollar_volume_20'] or 0) / 1e6:.1f}M 低於 {s['min_dollar_volume'] / 1e6:.0f}M"
    if risk and (risk.get('events') or risk.get('cleared') is not True):
        return '處置、異常交易或重大事件待確認'
    if f['run_up_20'] is not None and f['run_up_20'] > s['max_run_up_20d']:
        return f"20 日已漲 {f['run_up_20']:.0%}，不是早期趨勢"
    if f['extension_atr'] is not None and f['extension_atr'] > s['max_extension_atr']:
        return f"距月線 {f['extension_atr']:.1f} 個 ATR，乖離過大"
    if (f['upper_shadow'] > s['max_upper_shadow'] and (f['relative_volume'] or 0) > 1.8):
        return f"爆量長上影（上影佔全日 {f['upper_shadow']:.0%}），當日買盤失敗"
    if f['gap'] is not None and abs(f['gap']) > s['max_gap']:
        return f"跳空 {f['gap']:+.1%}，今天不是進場點"
    if f['close'] < f['open'] and f['close'] <= f['low'] + (f['high'] - f['low']) * 0.2 \
            and (f['relative_volume'] or 0) > 1.5:
        return '開高走低且收在當日低檔，結構轉弱'
    return None


# --- setups (document section 6) --------------------------------------------------------------------

def setup(f, settings=None):
    """'breakout', 'pullback' or None, with why. Only the two the document says v1 trades."""
    s = {**SETTINGS, **(settings or {})}
    if not f:
        return None, '無特徵'
    base_high, width = f.get('base_high'), f.get('base_width')
    reasons = []

    # A. Breakout / early trend: out of a real base, recently, on expanded volume, still near the level.
    if base_high and width is not None and width <= s['base_max_width']:
        if f['close'] > base_high:
            if (f['relative_volume'] or 0) >= s['breakout_volume']:
                if f['atr'] and (f['close'] - base_high) / f['atr'] <= 3.0:
                    return 'breakout', (f"突破 {s['base_lookback']} 日整理區上緣 {base_high:g}"
                                        f"（區間寬度 {width:.0%}），相對量 {f['relative_volume']:.1f}，"
                                        f"距突破點 {(f['close'] - base_high) / f['atr']:.1f} ATR")
                reasons.append('突破後已離開突破位置過遠')
            else:
                reasons.append(f"突破但量能僅 {f['relative_volume'] or 0:.1f} 倍")
        else:
            reasons.append('尚未突破整理區上緣')
    else:
        reasons.append('沒有足夠緊緻的整理區')

    # B. Trend pullback: trend intact, pulled back a healthy amount on lighter volume, near support.
    if f['ma60'] and f['close'] > f['ma60'] and f['rs'].get(60, 0) > 0:
        depth = (f['high_60'] - f['close']) / f['high_60'] if f['high_60'] else None
        if depth is not None and s['pullback_min_depth'] <= depth <= s['pullback_max_depth']:
            if (f['relative_volume'] or 1.0) <= s['pullback_volume_max']:
                near = f['ma20'] and abs(f['close'] - f['ma20']) / f['close'] <= 0.06
                if near or (f['swing_lows'] and f['close'] >= f['swing_lows'][-1][1]):
                    return 'pullback', (f"趨勢在月線與季線之上、60 日相對強度為正，自高點回檔 {depth:.0%}，"
                                        f"回檔量能 {f['relative_volume'] or 1:.1f} 倍，貼近月線或前波低點")
                reasons.append('回檔位置不在月線或前波低點附近')
            else:
                reasons.append(f"回檔放量 {f['relative_volume']:.1f} 倍，不是健康回檔")
        elif depth is not None:
            reasons.append(f"回檔 {depth:.0%} 不在 {s['pullback_min_depth']:.0%}~{s['pullback_max_depth']:.0%} 之間")
    else:
        reasons.append('不在上升趨勢中（季線之下或 60 日相對強度為負）')
    return None, '；'.join(reasons[:2])


# --- score (document section 7) ---------------------------------------------------------------------

def _structure(f, kind):
    """25 points. Is the price structure itself actually good, right now?"""
    parts = []
    if f.get('base_width') is not None:
        parts.append(('整理區緊緻', 1 - _ratio(f['base_width'], 0.05, SETTINGS['base_max_width'])))
    if f['range_position'] is not None:
        parts.append(('區間位置', _ratio(f['range_position'], 0.45, 0.98)))
    if f['atr'] and f.get('base_high'):
        # Still close to the level that defines the move: early rather than extended.
        parts.append(('距突破位置', 1 - _ratio(abs(f['close'] - f['base_high']) / f['atr'], 0.0, 4.0)))
    hh = f['swing_highs'][-2:]
    ll = f['swing_lows'][-2:]
    if len(hh) == 2 and len(ll) == 2:
        parts.append(('高低點墊高', 1.0 if (hh[1][1] > hh[0][1] and ll[1][1] > ll[0][1]) else 0.25))
    if f['ma60']:
        parts.append(('均線排列', 1.0 if (f['close'] > f['ma20'] > f['ma60']) else
                      0.5 if f['close'] > f['ma60'] else 0.0))
    values = [v for _, v in parts if v is not None]
    return (statistics.mean(values), {k: v for k, v in parts}) if values else (None, {})


def _relative_strength(f):
    """15 points. Is it stronger than the market, and than its own sector?"""
    parts = []
    for w, lo, hi in ((5, -0.02, 0.06), (20, -0.05, 0.15), (60, -0.10, 0.35)):
        if w in f['rs']:
            parts.append((f'{w}日相對大盤', _ratio(f['rs'][w], lo, hi)))
    if f['rs_sector'] is not None:
        parts.append(('20日相對族群', _ratio(f['rs_sector'], -0.05, 0.12)))
    values = [v for _, v in parts if v is not None]
    return (statistics.mean(values), {k: v for k, v in parts}) if values else (None, {})


def _participation(f, kind):
    """15 points. Did real money move the price, and in the way this setup needs?"""
    parts = []
    if f['dollar_volume_20']:
        parts.append(('成交金額', _ratio(f['dollar_volume_20'] / 1e6, 20, 500)))
    if f['volume_vs_median'] is not None:
        parts.append(('量能趨勢', _ratio(f['volume_vs_median'], 0.7, 1.8)))
    if f['relative_volume'] is not None:
        # A breakout wants expansion; a pullback wants contraction. Same number, opposite readings.
        parts.append(('當日量能', _ratio(f['relative_volume'], 1.0, 2.5) if kind == 'breakout'
                      else 1 - _ratio(f['relative_volume'], 0.5, 1.3)))
    values = [v for _, v in parts if v is not None]
    return (statistics.mean(values), {k: v for k, v in parts}) if values else (None, {})


def _institutional(f):
    """15 points. Has sizeable money been on the same side, consistently?"""
    flow = f.get('flow')
    if not flow:
        return None, {}
    parts = [('佔成交比重', _ratio(flow['share_of_volume'], -0.02, 0.10)),
             ('連續同向', _ratio(flow['days_positive'], 1, SETTINGS['flow_days']))]
    values = [v for _, v in parts if v is not None]
    return (statistics.mean(values), {k: v for k, v in parts}) if values else (None, {})


def _sector(f, breadth):
    """10 points. Is the whole group turning, or is this one stock wandering up alone?"""
    if not breadth:
        return None, {}
    parts = [('族群上漲比例', _ratio(breadth.get('advancing'), 0.35, 0.80)),
             ('族群強勢比例', _ratio(breadth.get('near_high'), 0.10, 0.50))]
    values = [v for _, v in parts if v is not None]
    return (statistics.mean(values), {k: v for k, v in parts}) if values else (None, {})


def _fundamental(f):
    """10 points. Is there anything behind the price, or is it only price?"""
    fin = f.get('fundamentals')
    if not fin:
        return None, {}
    parts = []
    if fin.get('revenue_yoy') is not None:
        parts.append(('月營收年增', _ratio(fin['revenue_yoy'], -0.10, 0.40)))
    if fin.get('eps') is not None:
        parts.append(('每股盈餘為正', 1.0 if fin['eps'] > 0 else 0.0))
    if fin.get('operating_margin') is not None:
        parts.append(('營業利益率', _ratio(fin['operating_margin'], 0.0, 0.20)))
    values = [v for _, v in parts if v is not None]
    return (statistics.mean(values), {k: v for k, v in parts}) if values else (None, {})


def _risk_quality(f):
    """10 points. Even if it is strong, is this a good place to be buying it?"""
    parts = []
    if f['extension_atr'] is not None:
        parts.append(('乖離', 1 - _ratio(f['extension_atr'], 1.0, SETTINGS['max_extension_atr'])))
    parts.append(('上影線', 1 - _ratio(f['upper_shadow'], 0.15, 0.6)))
    if f['gap'] is not None:
        parts.append(('跳空', 1 - _ratio(abs(f['gap']), 0.005, SETTINGS['max_gap'])))
    if f['distance_from_high'] is not None:
        # Overhead supply: right under a 60-day high is where sellers are waiting.
        parts.append(('上方壓力', _ratio(f['distance_from_high'], 0.0, 0.08)
                      if f['distance_from_high'] < 0.02 else 1.0))
    if f['atr'] and f['close']:
        parts.append(('波動', 1 - _ratio(f['atr'] / f['close'], 0.02, 0.08)))
    values = [v for _, v in parts if v is not None]
    return (statistics.mean(values), {k: v for k, v in parts}) if values else (None, {})


def score(f, kind, breadth=None, settings=None):
    """The 100-point score, renormalised over whatever could actually be judged.

    Returns None when too little could be judged to call it a score at all."""
    s = {**SETTINGS, **(settings or {})}
    raw = {'structure': _structure(f, kind), 'relative_strength': _relative_strength(f),
           'participation': _participation(f, kind), 'institutional': _institutional(f),
           'sector': _sector(f, breadth), 'fundamental': _fundamental(f),
           'risk_quality': _risk_quality(f)}
    earned = available = 0.0
    components, missing = {}, []
    for name, weight in WEIGHTS.items():
        value, detail = raw[name]
        if value is None:
            missing.append(name)
            continue
        available += weight
        earned += value * weight
        components[name] = {'points': round(value * weight, 2), 'of': weight,
                            'detail': {k: (None if v is None else round(v, 3)) for k, v in detail.items()}}
    if available < sum(WEIGHTS.values()) * s['min_scored_fraction']:
        return None
    total = earned / available * 100
    grade = next((g for cut, g in GRADES if total >= cut), 'Ignore')
    return {'version': VERSION, 'setup': kind, 'total': round(total, 1), 'grade': grade,
            'scored_out_of': round(available, 1), 'missing': missing, 'components': components}
