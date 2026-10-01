"""A simulated account that keeps real money's constraints. One engine, two drivers.

The point of this file is that the backtest and the live paper account are the same code. A backtest
that uses different accounting from the thing it is meant to predict is a story, and the user asked for
no stories: "不要幻想、不要做假資料". So `Book` holds plain state and knows nothing about where it came
from -- the backtest drives it over stored history in memory, the daily close job loads it from the
database, advances it one day, and saves it back.

What it refuses to pretend:

  Cash is finite, and sale proceeds are not available until T+2. Taiwan settles on the second trading
  day, so money from today's sale cannot buy anything until then. Without this the account quietly
  compounds faster than a real one.

  Fees and transaction tax leave the account on every trade, through `costs`, and the reported return is
  return on cash deployed -- not the price move. A position's own fees are part of its result.

  A limit order fills only if the day's low actually reached it, and at the limit price (or the open,
  when the market gapped past it in our favour). An order that was never reachable never happened.

  Nothing is marked at a price that was not traded. Equity uses the last close on record; a symbol with
  no bar that day keeps its previous mark and is flagged, rather than being revalued by assumption.

Odd lots are first-class: `shares` is an exact count, so a NT$18,000 position in a NT$600 stock is 30
shares. A multiple of 1,000 is a lot order and anything else is an odd-lot order, recorded as such on
every trade so the record cannot later be mistaken for round-lot trading.
"""
import json
import uuid

from . import costs

SETTLEMENT_DAYS = 2  # T+2, in trading days


class Book:
    def __init__(self, cash, starting_cash=None, positions=None, pending=None, trades=None,
                 started_day=None, closed=None):
        self.cash = float(cash)
        self.starting_cash = float(starting_cash if starting_cash is not None else cash)
        self.positions = dict(positions or {})
        self.pending = list(pending or [])      # [{'release_day','cash','symbol'}]
        self.trades = list(trades or [])
        self.closed = list(closed or [])        # completed round trips
        self.started_day = started_day

    # --- settlement ----------------------------------------------------------------------------
    def settle(self, day):
        """Credit sale proceeds whose settlement day has arrived. Returns what was credited."""
        due = [p for p in self.pending if p['release_day'] <= day]
        self.pending = [p for p in self.pending if p['release_day'] > day]
        for p in due:
            self.cash += p['cash']
        return due

    def available(self):
        """Cash that may be spent today. Unsettled proceeds are not it."""
        return round(self.cash, 2)

    def unsettled(self):
        return round(sum(p['cash'] for p in self.pending), 2)

    # --- trading -------------------------------------------------------------------------------
    def fill_price(self, bar, limit, side):
        """The price a limit order actually got, or None when it could not have filled.

        A buy at `limit` needs the day's low to reach it; if the day opened below it the fill is the
        open, because the market was already better than the order."""
        low, high, open_ = bar[2], bar[1], bar[0]
        if side == 'buy':
            if low > limit:
                return None
            return min(limit, open_) if open_ < limit else limit
        if high < limit:
            return None
        return max(limit, open_) if open_ > limit else limit

    def buy(self, symbol, name, kind, shares, price, day, at, plan=None, entry_index=0):
        if symbol in self.positions:
            raise ValueError('已有持倉，不重複建立')
        charge = costs.buy_cost(shares, price)
        if charge['cash'] > self.cash:
            raise ValueError('現金不足')
        self.cash = round(self.cash - charge['cash'], 2)
        self.positions[symbol] = {
            'symbol': symbol, 'name': name, 'kind': kind, 'shares': shares, 'entry': price,
            'entry_day': day, 'entry_at': at, 'entry_index': entry_index,
            'cost_in': charge['cash'], 'entry_fee': charge['fee'],
            'stop': (plan or {}).get('stop'), 'target': (plan or {}).get('target'),
            'stop_now': (plan or {}).get('stop'), 'mark': price, 'mark_day': day,
            'order_type': charge['order_type'], 'orders': charge['orders'], 'plan': plan or {}}
        self.trades.append({
            'id': uuid.uuid4().hex, 'at': at, 'day': day, 'symbol': symbol, 'name': name,
            'side': 'buy', 'shares': shares, 'price': price, 'value': charge['value'],
            'fee': charge['fee'], 'tax': 0.0, 'cash': -charge['cash'],
            'order_type': charge['order_type'], 'orders': charge['orders'],
            'stop': (plan or {}).get('stop'), 'target': (plan or {}).get('target'),
            'cash_after': self.cash})
        return self.positions[symbol]

    def sell(self, symbol, price, day, at, reason, release_day=None, exit_index=None):
        position = self.positions.pop(symbol)
        shares = position['shares']
        proceeds = costs.sell_cost(shares, price, position['kind'])
        trip = costs.round_trip(shares, position['entry'], price, position['kind'])
        # T+2: the money exists but cannot be spent yet.
        if release_day and release_day > day:
            self.pending.append({'release_day': release_day, 'cash': proceeds['cash'], 'symbol': symbol})
        else:
            self.cash = round(self.cash + proceeds['cash'], 2)
        record = {**position, 'exit': price, 'exit_day': day, 'exit_at': at, 'exit_reason': reason,
                  'exit_fee': proceeds['fee'], 'tax': proceeds['tax'], 'cash_out': proceeds['cash'],
                  'profit': trip['profit'], 'return_pct': trip['return_pct'],
                  'gross_pct': trip['gross_pct'],
                  'held_days': (exit_index - position['entry_index'])
                  if exit_index is not None and position.get('entry_index') is not None else None}
        self.closed.append(record)
        self.trades.append({
            'id': uuid.uuid4().hex, 'at': at, 'day': day, 'symbol': symbol,
            'name': position['name'], 'side': 'sell', 'shares': shares, 'price': price,
            'value': proceeds['value'], 'fee': proceeds['fee'], 'tax': proceeds['tax'],
            'cash': proceeds['cash'], 'order_type': proceeds['order_type'],
            'orders': proceeds['orders'], 'reason': reason,
            'profit': trip['profit'], 'return_pct': trip['return_pct'],
            'settles_on': release_day, 'cash_after': self.cash})
        return record

    # --- valuation -----------------------------------------------------------------------------
    def mark(self, bars, day):
        """Mark open positions to the day's close. A symbol without a bar keeps its last mark."""
        stale = []
        for symbol, position in self.positions.items():
            bar = bars.get(symbol)
            if bar and bar[3]:
                position['mark'], position['mark_day'] = bar[3], day
            else:
                stale.append(symbol)
        return stale

    def holdings_value(self):
        return round(sum(p['shares'] * p['mark'] for p in self.positions.values()), 2)

    def equity(self):
        """Cash, money on its way, and holdings at their last traded price. Before exit costs."""
        return round(self.cash + self.unsettled() + self.holdings_value(), 2)

    def net_equity(self):
        """Equity if every position were sold at its mark today, after the fees and tax that would cost.
        This is the number a person should compare against the NT$100,000 they put in."""
        after = self.cash + self.unsettled()
        for p in self.positions.values():
            after += costs.sell_cost(p['shares'], p['mark'], p['kind'])['cash']
        return round(after, 2)

    def unrealised(self):
        out = {}
        for symbol, p in self.positions.items():
            trip = costs.round_trip(p['shares'], p['entry'], p['mark'], p['kind'])
            out[symbol] = {'profit': trip['profit'], 'return_pct': trip['return_pct'],
                           'gross_pct': trip['gross_pct']}
        return out

    def realised(self):
        return round(sum(c['profit'] for c in self.closed), 2)

    def fees_paid(self):
        return round(sum(t['fee'] + t['tax'] for t in self.trades), 2)

    def state(self):
        return {'cash': self.cash, 'starting_cash': self.starting_cash, 'positions': self.positions,
                'pending': self.pending, 'started_day': self.started_day}

    # --- persistence ---------------------------------------------------------------------------
    @classmethod
    def load(cls, store):
        meta = store.meta('paper_account') or {}
        if not meta:
            return None
        positions = {r[0]: json.loads(r[1]) for r in
                     store.db.execute('SELECT symbol,payload FROM paper_positions')}
        closed = [json.loads(r[0]) for r in store.db.execute(
            "SELECT payload FROM paper_trades WHERE side='closed' ORDER BY at")]
        trades = [json.loads(r[0]) for r in store.db.execute(
            "SELECT payload FROM paper_trades WHERE side IN ('buy','sell') ORDER BY at")]
        # Keyword arguments on purpose: passing these positionally once put `closed` into `started_day`,
        # which silently turned the closed-trade count into the length of a date string.
        return cls(meta['cash'], starting_cash=meta.get('starting_cash'), positions=positions,
                   pending=meta.get('pending'), trades=trades, closed=closed,
                   started_day=meta.get('started_day'))

    def save(self, store):
        with store.transaction():
            store.db.execute('DELETE FROM paper_positions')
            for symbol, position in self.positions.items():
                store.db.execute('INSERT INTO paper_positions VALUES(?,?)',
                                 (symbol, json.dumps(position, ensure_ascii=False, sort_keys=True)))
            known = {r[0] for r in store.db.execute('SELECT id FROM paper_trades')}
            rows = [('buy' if t['side'] == 'buy' else 'sell', t) for t in self.trades if t['id'] not in known]
            for side, t in rows:
                store.db.execute('INSERT INTO paper_trades VALUES(?,?,?,?,?,?)',
                                 (t['id'], t['at'], t['day'], t['symbol'], side,
                                  json.dumps(t, ensure_ascii=False, sort_keys=True)))
            for c in self.closed:
                key = f"closed:{c['symbol']}:{c['exit_day']}:{c['entry_day']}"
                if key in known:
                    continue
                store.db.execute('INSERT OR REPLACE INTO paper_trades VALUES(?,?,?,?,?,?)',
                                 (key, c['exit_at'], c['exit_day'], c['symbol'], 'closed',
                                  json.dumps(c, ensure_ascii=False, sort_keys=True)))
            store.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',
                             ('paper_account', json.dumps(
                                 {'cash': self.cash, 'starting_cash': self.starting_cash,
                                  'pending': self.pending, 'started_day': self.started_day},
                                 ensure_ascii=False, sort_keys=True)))


def performance(book):
    """The numbers a person actually asks: did it make money, how often, and what did it cost."""
    closed = book.closed
    wins = [c for c in closed if c['profit'] > 0]
    losses = [c for c in closed if c['profit'] <= 0]
    equity = book.net_equity()
    out = {'starting_cash': book.starting_cash, 'net_equity': equity,
           'total_return_pct': round((equity / book.starting_cash - 1) * 100, 2),
           'realised_profit': book.realised(), 'fees_and_tax': book.fees_paid(),
           'open_positions': len(book.positions), 'closed_trades': len(closed),
           'wins': len(wins), 'losses': len(losses),
           'win_rate_pct': round(len(wins) / len(closed) * 100, 1) if closed else None,
           'cash': book.available(), 'unsettled': book.unsettled(),
           'holdings_value': book.holdings_value()}
    if wins:
        out['average_win_pct'] = round(sum(c['return_pct'] for c in wins) / len(wins), 2)
    if losses:
        out['average_loss_pct'] = round(sum(c['return_pct'] for c in losses) / len(losses), 2)
    reasons = {}
    for c in closed:
        reasons[c['exit_reason']] = reasons.get(c['exit_reason'], 0) + 1
    out['exit_reasons'] = reasons
    return out
