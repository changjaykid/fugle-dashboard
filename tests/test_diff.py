"""Unit tests for stock_radar.diff — pure signal-change detection, no I/O."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from stock_radar.diff import (significant_changes, significant_changes_against_snapshot,
                              snapshot_signals, revoked_actionable_symbols)


def make_item(symbol, status, suggested=None, valid_until=None, calculated_at=None):
    return {
        'symbol': symbol,
        'signal': {
            'status': status, 'suggested': suggested, 'conservative': None,
            'extreme': None, 'valid_until': valid_until,
            'calculated_at': calculated_at,  # deliberately NOT compared
        },
    }


class TestSignificantChanges(unittest.TestCase):
    def test_no_previous_data_and_not_actionable_is_not_reported(self):
        prev = []
        curr = [make_item('2330', 'pending')]
        self.assertEqual(significant_changes(prev, curr), [])

    def test_new_actionable_symbol_with_no_prior_snapshot_is_reported(self):
        prev = []
        curr = [make_item('2330', 'sweet', suggested=100.0)]
        changed = significant_changes(prev, curr)
        self.assertEqual([c['symbol'] for c in changed], ['2330'])

    def test_status_change_is_reported(self):
        prev = [make_item('2330', 'pending')]
        curr = [make_item('2330', 'sweet', suggested=100.0)]
        changed = significant_changes(prev, curr)
        self.assertEqual([c['symbol'] for c in changed], ['2330'])

    def test_identical_status_and_price_is_not_reported(self):
        prev = [make_item('2330', 'sweet', suggested=100.0, valid_until='2026-09-10T09:00:00+08:00')]
        curr = [make_item('2330', 'sweet', suggested=100.0, valid_until='2026-09-10T09:00:00+08:00')]
        self.assertEqual(significant_changes(prev, curr), [])

    def test_calculated_at_only_change_is_not_reported(self):
        """Regression: calculated_at is stamped fresh on every export even
        when nothing meaningfully changed -- it must NOT trigger a
        notification on its own, or the 08:55 diff step is pointless."""
        prev = [make_item('2330', 'sweet', suggested=100.0, calculated_at='2026-09-10T08:50:00+08:00')]
        curr = [make_item('2330', 'sweet', suggested=100.0, calculated_at='2026-09-10T08:55:00+08:00')]
        self.assertEqual(significant_changes(prev, curr), [])

    def test_suggested_price_change_is_reported(self):
        prev = [make_item('2330', 'sweet', suggested=100.0)]
        curr = [make_item('2330', 'sweet', suggested=99.5)]
        changed = significant_changes(prev, curr)
        self.assertEqual([c['symbol'] for c in changed], ['2330'])

    def test_valid_until_only_change_is_not_reported(self):
        """Regression (Codex review 2026-09-10): valid_until is stamped
        fresh (rolled forward) on every export even for an unchanged
        sweet/add/buy signal, exactly like calculated_at -- comparing it
        raw made every run of an otherwise-unchanged actionable signal look
        like a notify-worthy 'change' purely because its expiry window
        moved, which is a pure renewal, not something a human needs
        re-pinged about. Status changes (e.g. flipping to 'stale' once a
        signal genuinely expires) are still caught via the `status` field."""
        prev = [make_item('2330', 'sweet', suggested=100.0, valid_until='2026-09-10T09:00:00+08:00')]
        curr = [make_item('2330', 'sweet', suggested=100.0, valid_until='2026-09-10T09:05:00+08:00')]
        self.assertEqual(significant_changes(prev, curr), [])

    def test_multiple_symbols_only_changed_ones_returned(self):
        prev = [make_item('2330', 'sweet', suggested=100.0), make_item('0050', 'pending')]
        curr = [make_item('2330', 'sweet', suggested=100.0), make_item('0050', 'add', suggested=50.0)]
        changed = significant_changes(prev, curr)
        self.assertEqual([c['symbol'] for c in changed], ['0050'])

    def test_symbol_disappearing_from_current_is_not_reported_here(self):
        """A coverage regression (symbol dropped from current) is a
        different concern (health entries), not a signal-change diff."""
        prev = [make_item('2330', 'sweet', suggested=100.0)]
        curr = []
        self.assertEqual(significant_changes(prev, curr), [])


class TestSnapshotAndAgainstSnapshot(unittest.TestCase):
    def test_snapshot_signals_shape(self):
        items = [make_item('2330', 'sweet', suggested=100.0)]
        snap = snapshot_signals(items)
        self.assertEqual(snap['2330']['status'], 'sweet')
        self.assertEqual(snap['2330']['suggested'], 100.0)
        self.assertNotIn('calculated_at', snap['2330'])

    def test_against_snapshot_matches_full_list_behavior(self):
        prev_items = [make_item('2330', 'pending')]
        curr_items = [make_item('2330', 'sweet', suggested=100.0)]
        snap = snapshot_signals(prev_items)
        via_snapshot = significant_changes_against_snapshot(snap, curr_items)
        via_full_list = significant_changes(prev_items, curr_items)
        self.assertEqual([c['symbol'] for c in via_snapshot], [c['symbol'] for c in via_full_list])

    def test_against_empty_snapshot_only_reports_actionable(self):
        curr_items = [make_item('2330', 'pending'), make_item('0050', 'buy', suggested=50.0)]
        changed = significant_changes_against_snapshot({}, curr_items)
        self.assertEqual([c['symbol'] for c in changed], ['0050'])


class TestRevokedActionableSymbols(unittest.TestCase):
    """Regression (Codex review 2026-09-10): a symbol that WAS actionable
    in the last-notified snapshot but has vanished entirely from the
    current item set (delisted / dropped by sync-universe / corrected)
    must be reported as an explicit revocation, not silently dropped --
    the frontend/Discord history may still show it as actionable to a
    human who has not re-checked."""
    def test_vanished_actionable_symbol_is_reported(self):
        snap = snapshot_signals([make_item('2330', 'sweet', suggested=100.0)])
        curr = []  # 2330 no longer in universe at all
        revoked = revoked_actionable_symbols(snap, curr)
        self.assertEqual(revoked, [{'symbol': '2330', 'previous_status': 'sweet'}])

    def test_symbol_still_present_but_now_stale_is_not_a_revocation(self):
        """Still present (even downgraded to stale/blocked) is an ordinary
        status change, already covered by significant_changes_against_snapshot;
        it must NOT also show up as a 'revoked' (vanished) entry."""
        snap = snapshot_signals([make_item('2330', 'sweet', suggested=100.0)])
        curr = [make_item('2330', 'stale')]
        self.assertEqual(revoked_actionable_symbols(snap, curr), [])

    def test_vanished_non_actionable_symbol_is_not_reported(self):
        snap = snapshot_signals([make_item('2330', 'pending')])
        curr = []
        self.assertEqual(revoked_actionable_symbols(snap, curr), [])

    def test_empty_snapshot_reports_nothing(self):
        curr = [make_item('2330', 'pending')]
        self.assertEqual(revoked_actionable_symbols({}, curr), [])


if __name__ == '__main__':
    unittest.main()
