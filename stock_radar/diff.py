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
SIGNIFICANT_SIGNAL_FIELDS = ('status', 'suggested', 'conservative', 'extreme', 'valid_until')


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
