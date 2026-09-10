"""Pure signal-change detection for the 08:55 "only notify on real change"
step in STOCK_RADAR_SPEC.md's daily schedule ("只有狀態／掛價重大改變、失效
才通知"). No I/O, no Discord, no DB -- takes two already-loaded radar.json-
shaped item lists and returns which ones actually changed in a way worth
telling a human about.
"""
from __future__ import annotations

# Fields whose change is considered "significant" for notification
# purposes. Deliberately narrow: `calculated_at` changes on every export
# even when nothing meaningful moved (decide() always stamps it to `now`),
# so it must NOT be in this list or every single export would look like a
# change and defeat the entire point of the 08:55 step.
#
# `valid_until` is EXCLUDED for the same reason (Codex review,
# 2026-09-10): decide() recomputes valid_until as roughly now+max_age on
# every single export even when the underlying status/suggested price
# have not changed at all, so comparing it raw made every run of an
# unchanged sweet/add/buy signal look like a "change" purely because its
# time window rolled forward -- a pure renewal/re-freshening, not
# something a human needs pinged about again. A signal that actually
# EXPIRES (rather than merely being re-stamped with a new window) is
# still caught here: decide() flips its `status` to 'stale'/'blocked' once
# the underlying quote/valuation genuinely goes stale, and `status` IS in
# this list.
SIGNIFICANT_SIGNAL_FIELDS = ('status', 'suggested', 'conservative', 'extreme')


def _signal_key(item: dict) -> dict:
    sig = item.get('signal') or {}
    return {f: sig.get(f) for f in SIGNIFICANT_SIGNAL_FIELDS}


def significant_changes(previous_items: list[dict], current_items: list[dict]) -> list[dict]:
    """Returns the subset of current_items whose signal changed in a
    SIGNIFICANT_SIGNAL_FIELDS-relevant way versus previous_items (matched
    by symbol), OR that are newly actionable (sweet/add/buy) and were not
    present in previous_items at all. A symbol present in `previous` but
    missing from `current` is not reported here (that is a coverage
    regression, not a signal change -- callers should surface that via the
    existing health-entry mechanism instead, not this diff).
    """
    previous_by_symbol = {i['symbol']: _signal_key(i) for i in previous_items}
    changed = []
    for item in current_items:
        symbol = item['symbol']
        current_key = _signal_key(item)
        prev_key = previous_by_symbol.get(symbol)
        if prev_key is None:
            # New symbol we have no prior snapshot for at all. Only worth
            # flagging if it's already actionable -- a brand-new 'pending'
            # instrument appearing (e.g. right after sync-universe added
            # it) is not something a human needs pinged about.
            if current_key['status'] in ('sweet', 'add', 'buy'):
                changed.append(item)
            continue
        if current_key != prev_key:
            changed.append(item)
    return changed


def snapshot_signals(items: list[dict]) -> dict:
    """Compresses a full radar.json items list down to just the
    {symbol: signal-subset} shape needed by significant_changes() on the
    NEXT run, for cheap storage in Store.metadata (much smaller than
    keeping a full radar.json snapshot around)."""
    return {i['symbol']: _signal_key(i) for i in items}


def significant_changes_against_snapshot(snapshot: dict, current_items: list[dict]) -> list[dict]:
    """Same logic as significant_changes(), but compares against an
    already-compressed snapshot dict (as produced by snapshot_signals())
    instead of a full previous items list -- this is what cli.py actually
    uses, since it stores the compressed form in Store.metadata rather
    than a second full radar.json on disk."""
    changed = []
    for item in current_items:
        symbol = item['symbol']
        current_key = _signal_key(item)
        prev_key = snapshot.get(symbol)
        if prev_key is None:
            if current_key['status'] in ('sweet', 'add', 'buy'):
                changed.append(item)
            continue
        if current_key != prev_key:
            changed.append(item)
    return changed


def revoked_actionable_symbols(snapshot: dict, current_items: list[dict]) -> list[dict]:
    """Symbols that were actionable (sweet/add/buy) in the last-notified
    snapshot but have vanished from current_items ENTIRELY (delisted,
    dropped from universe by a later sync-universe, or an instrument
    correction) -- per Codex review 2026-09-10, this is a DISTINCT case
    from an ordinary coverage regression (a missing quote/valuation, which
    is already surfaced via cmd_export's health entries): a human who was
    previously told "sweet price on X" needs an explicit revocation line
    if X disappears outright, not silence, since they may still be
    watching/acting on that stale instruction. A symbol that is still
    PRESENT in current_items (even if its status has since dropped to
    'stale'/'blocked'/'pending') is NOT reported here -- that is an
    ordinary status change, already caught by
    significant_changes_against_snapshot()'s normal path.

    Returns [{'symbol': ..., 'previous_status': 'sweet'|'add'|'buy'}, ...].
    Self-resolving: callers refresh the stored snapshot to the CURRENT
    item set on every run regardless of what changed (see cli.py's
    cmd_notify_changes), so a revoked symbol -- being entirely absent from
    that current set -- naturally drops out of next run's snapshot too,
    and is reported here exactly once, not on every subsequent run.
    """
    current_symbols = {i['symbol'] for i in current_items}
    revoked = []
    for symbol, prev_key in snapshot.items():
        if prev_key.get('status') in ('sweet', 'add', 'buy') and symbol not in current_symbols:
            revoked.append({'symbol': symbol, 'previous_status': prev_key.get('status')})
    return revoked
