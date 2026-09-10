"""Unit tests for stock_radar.discord — formatting/chunking logic, pure
functions, no network (send/fetch tested separately against live channel
by the operational script, not in this offline suite)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from datetime import datetime, timedelta
from unittest import mock

from stock_radar.domain import TW
from stock_radar.discord import (
    chunk_text, format_status_line, format_daily_summary, lookup_reply,
    send_message, fetch_new_messages, extract_query, MESSAGE_CHAR_LIMIT, NO_PING,
)

NOW = datetime(2026, 9, 10, 10, 0, 0, tzinfo=TW)


def actionable_signal(now=NOW, **overrides):
    sig = {
        'status': 'sweet', 'status_label': '甜甜價', 'suggested': 3175.0,
        'action': '可考慮掛3175',
        'calculated_at': now.isoformat(),
        'valid_until': (now + timedelta(hours=1)).isoformat(),
    }
    sig.update(overrides)
    return sig


def actionable_quote(now=NOW, **overrides):
    q = {'previous_close': 3905.0, 'trial_price': 4000.0, 'as_of': now.isoformat()}
    q.update(overrides)
    return q


def actionable_valuation(now=NOW, **overrides):
    v = {'sweet': 3180.0, 'add': 3590.0, 'buy': 4080.0,
        'valid_until': (now + timedelta(hours=1)).isoformat(), 'evidence_reviewed': True}
    v.update(overrides)
    return v


class TestChunkText(unittest.TestCase):
    def test_short_text_single_chunk(self):
        self.assertEqual(chunk_text('hello'), ['hello'])

    def test_long_text_splits_on_lines(self):
        text = '\n'.join(f'line{i}' * 50 for i in range(200))
        chunks = chunk_text(text, limit=500)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 500)

    def test_never_splits_mid_line(self):
        lines = [f'row-{i}' for i in range(100)]
        text = '\n'.join(lines)
        chunks = chunk_text(text, limit=50)
        rejoined = '\n'.join(chunks)
        self.assertEqual(rejoined.split('\n'), lines)

    def test_default_limit_under_discord_cap(self):
        self.assertLess(MESSAGE_CHAR_LIMIT, 2000)

    def test_single_overlong_line_is_hard_split_not_left_oversized(self):
        """Regression (Codex review): a single line longer than `limit`
        (e.g. one very long thesis line with no newlines) must not be
        emitted as one oversized chunk -- every chunk must respect the
        limit."""
        line = 'x' * 5000
        chunks = chunk_text(line, limit=100)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 100)
        self.assertEqual(''.join(chunks), line)

    def test_overlong_line_mixed_with_normal_lines(self):
        text = 'short1\n' + ('y' * 300) + '\nshort2'
        chunks = chunk_text(text, limit=100)
        for c in chunks:
            self.assertLessEqual(len(c), 100)
        self.assertEqual(''.join(chunks).replace('\n', ''), text.replace('\n', ''))


class TestSendMessageAllowedMentions(unittest.TestCase):
    def test_send_message_always_sets_no_ping_allowed_mentions(self):
        """Regression (Codex review): research text could contain a symbol
        name or literal '@everyone'-looking substring; every send must be
        locked to no-mention regardless of content, not opt-in per call."""
        session = mock.Mock()
        session.post.return_value = mock.Mock(json=lambda: {'id': '1'}, raise_for_status=lambda: None)
        send_message('tok', 'hello @everyone', channel_id='123', session=session)
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs['json']['allowed_mentions'], NO_PING)
        self.assertEqual(kwargs['json']['allowed_mentions']['parse'], [])


class TestSendMessageReplyThreading(unittest.TestCase):
    def test_reply_to_message_id_sets_message_reference(self):
        session = mock.Mock()
        session.post.return_value = mock.Mock(json=lambda: {'id': '2'}, raise_for_status=lambda: None)
        send_message('tok', 'reply text', channel_id='123', reply_to_message_id='999', session=session)
        _, kwargs = session.post.call_args
        self.assertEqual(kwargs['json']['message_reference'], {'message_id': '999', 'channel_id': '123'})

    def test_no_reply_id_omits_message_reference(self):
        session = mock.Mock()
        session.post.return_value = mock.Mock(json=lambda: {'id': '2'}, raise_for_status=lambda: None)
        send_message('tok', 'standalone text', channel_id='123', session=session)
        _, kwargs = session.post.call_args
        self.assertNotIn('message_reference', kwargs['json'])


class TestFetchNewMessages(unittest.TestCase):
    def test_uses_after_cursor_when_given(self):
        session = mock.Mock()
        session.get.return_value = mock.Mock(json=lambda: [], raise_for_status=lambda: None)
        fetch_new_messages('tok', channel_id='123', after_id='555', session=session)
        _, kwargs = session.get.call_args
        self.assertEqual(kwargs['params']['after'], '555')

    def test_omits_after_when_none(self):
        session = mock.Mock()
        session.get.return_value = mock.Mock(json=lambda: [], raise_for_status=lambda: None)
        fetch_new_messages('tok', channel_id='123', session=session)
        _, kwargs = session.get.call_args
        self.assertNotIn('after', kwargs['params'])

    def test_returns_messages_oldest_first(self):
        """Discord returns newest-first even for `after` queries; this
        must be reversed so callers process/reply in real chronological
        order and can safely track 'last seen id' as the final item."""
        session = mock.Mock()
        session.get.return_value = mock.Mock(
            json=lambda: [{'id': '3'}, {'id': '2'}, {'id': '1'}], raise_for_status=lambda: None)
        result = fetch_new_messages('tok', channel_id='123', session=session)
        self.assertEqual([m['id'] for m in result], ['1', '2', '3'])


class TestExtractQuery(unittest.TestCase):
    def test_dollar_prefix_triggers(self):
        self.assertEqual(extract_query('$3661'), '3661')

    def test_fullwidth_dollar_prefix_triggers(self):
        self.assertEqual(extract_query('＄3661'), '3661')

    def test_chinese_cha_prefix_triggers(self):
        self.assertEqual(extract_query('查 3661'), '3661')

    def test_chinese_chaxun_prefix_with_colon_triggers(self):
        self.assertEqual(extract_query('查詢：0050'), '0050')

    def test_lookup_prefix_case_insensitive(self):
        self.assertEqual(extract_query('LOOKUP 0050'), '0050')

    def test_bare_symbol_without_trigger_does_not_match(self):
        """Regression: item 7 must not turn every bare ticker-looking
        message into a query -- only explicitly-triggered messages route
        to the CLI, or normal chat mentioning a stock number would spam
        replies."""
        self.assertIsNone(extract_query('3661'))

    def test_conversational_text_mentioning_a_ticker_does_not_match(self):
        self.assertIsNone(extract_query('今天大盤好像不錯，3661怎麼看？'))

    def test_trigger_word_alone_does_not_match(self):
        self.assertIsNone(extract_query('查'))

    def test_empty_string_does_not_match(self):
        self.assertIsNone(extract_query(''))

    def test_overlong_message_does_not_match(self):
        self.assertIsNone(extract_query('$' + 'a' * 100))

    def test_query_with_trailing_whitespace_trimmed(self):
        self.assertEqual(extract_query('$3661  '), '3661')


class TestFormatStatusLine(unittest.TestCase):
    def test_pending_item_no_valuation(self):
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': {'previous_close': 3905.0, 'trial_price': None},
            'valuation': None,
            'signal': {'status': 'pending', 'status_label': '待估值', 'suggested': None, 'action': '等待研究'},
        }
        line = format_status_line(item, now=NOW)
        self.assertIn('世芯-KY', line)
        self.assertIn('3661', line)
        self.assertIn('尚無估值', line)
        self.assertIn('待估值', line)

    def test_valued_item_shows_sab(self):
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': actionable_quote(),
            'valuation': actionable_valuation(),
            'signal': actionable_signal(),
        }
        line = format_status_line(item, now=NOW)
        self.assertIn('甜3180', line)
        self.assertIn('加3590', line)
        self.assertIn('買4080', line)
        self.assertIn('3175', line)

    def test_missing_numbers_show_dash_not_zero(self):
        item = {
            'symbol': '9999', 'name': 'X',
            'quote': {'previous_close': None, 'trial_price': None},
            'valuation': None,
            'signal': {'status': 'stale', 'status_label': '資料不足', 'suggested': None, 'action': ''},
        }
        line = format_status_line(item, now=NOW)
        self.assertIn('—', line)
        self.assertNotIn('昨收0', line)

    def test_expired_actionable_signal_is_hidden_at_format_time(self):
        """Regression (Codex review): format_status_line/format_daily_summary
        must re-check a signal's own valid_until against the real send
        time, not trust whatever status was baked into radar.json when it
        was generated (export and notify-summary can run as separate,
        time-separated steps)."""
        past = NOW - timedelta(hours=2)
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': actionable_quote(),
            'valuation': actionable_valuation(),
            'signal': actionable_signal(now=past),  # valid_until = past + 1h, already before NOW
        }
        line = format_status_line(item, now=NOW)
        self.assertNotIn('3175', line)
        self.assertIn('過期', line)

    def test_actionable_signal_from_a_different_day_is_hidden(self):
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': actionable_quote(),
            'valuation': actionable_valuation(),
            'signal': actionable_signal(now=NOW - timedelta(days=1),
                                        valid_until=(NOW + timedelta(hours=1)).isoformat()),
        }
        line = format_status_line(item, now=NOW)
        self.assertNotIn('3175', line)

    def test_suggested_above_buy_anchor_is_hidden(self):
        """Regression (Codex review 2026-09-10): _revalidate_signal must
        mirror docs/radar.js's own gate, which blanks the price whenever
        suggested exceeds the valuation's buy anchor -- a stale/corrupt
        signal claiming a price above 'buy' must not reach Discord even if
        its own status/expiry fields still look superficially fine."""
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': actionable_quote(),
            'valuation': actionable_valuation(buy=3000.0),  # suggested 3175 > buy 3000
            'signal': actionable_signal(),
        }
        line = format_status_line(item, now=NOW)
        self.assertNotIn('3175', line)
        self.assertIn('過期', line)

    def test_unreviewed_valuation_is_hidden(self):
        """Regression (Codex review 2026-09-10): a valuation whose
        evidence_reviewed is not exactly True must not reach Discord as an
        actionable price, matching radar.js's v.evidence_reviewed!==true check."""
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': actionable_quote(),
            'valuation': actionable_valuation(evidence_reviewed=False),
            'signal': actionable_signal(),
        }
        line = format_status_line(item, now=NOW)
        self.assertNotIn('3175', line)

    def test_expired_valuation_itself_is_hidden_even_if_signal_looks_fresh(self):
        """Regression (Codex review 2026-09-10): even if the SIGNAL's own
        calculated_at/valid_until look fresh, an expired underlying
        VALUATION (valuation.valid_until in the past) must still hide the
        price -- the signal was computed from a valuation that has since
        itself lapsed."""
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': actionable_quote(),
            'valuation': actionable_valuation(valid_until=(NOW - timedelta(minutes=1)).isoformat()),
            'signal': actionable_signal(),
        }
        line = format_status_line(item, now=NOW)
        self.assertNotIn('3175', line)

    def test_stale_quote_is_hidden_even_if_signal_and_valuation_look_fresh(self):
        """Regression (Codex review 2026-09-10): a quote whose as_of is
        from an earlier day (or missing) must hide the price even if the
        signal/valuation fields look otherwise fine -- mirrors radar.js's
        own !q || !Number.isFinite(timeValue(q.as_of)) || day(q.as_of)!==day(now) check."""
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': actionable_quote(now=NOW - timedelta(days=1)),
            'valuation': actionable_valuation(),
            'signal': actionable_signal(),
        }
        line = format_status_line(item, now=NOW)
        self.assertNotIn('3175', line)

    def test_missing_quote_is_hidden(self):
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': {},
            'valuation': actionable_valuation(),
            'signal': actionable_signal(),
        }
        line = format_status_line(item, now=NOW)
        self.assertNotIn('3175', line)

    def test_calculated_at_in_the_future_is_hidden(self):
        """Regression (Codex review 2026-09-10): a calculated_at claiming a
        future timestamp is invalid data (clock skew / corruption), not a
        valid fresh signal -- must be hidden, mirroring radar.js's
        calculated>now check."""
        future = NOW + timedelta(minutes=5)
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': actionable_quote(),
            'valuation': actionable_valuation(),
            'signal': actionable_signal(calculated_at=future.isoformat()),
        }
        line = format_status_line(item, now=NOW)
        self.assertNotIn('3175', line)


class TestFormatDailySummary(unittest.TestCase):
    def make_radar(self, items, health=None, mode='live'):
        return {
            'generated_at': '2026-09-10T08:50:00+08:00', 'market_date': '2026-09-10',
            'mode': mode,
            'coverage': {'universe': 2, 'stocks': 1, 'etfs': 1, 'quotes': 2, 'valued': 1},
            'health': health or [], 'items': items,
        }

    def test_test_marker_prepended(self):
        radar = self.make_radar([])
        out = format_daily_summary(radar, test_marker=True)
        self.assertTrue(out.startswith('🧪'))
        self.assertIn('測試', out)

    def test_simulation_mode_forces_test_marker_even_without_flag(self):
        """Regression (Codex review): the data's own mode field decides,
        not the caller's flag -- a caller forgetting --test must not be
        able to send simulation data unmarked."""
        radar = self.make_radar([], mode='simulation')
        out = format_daily_summary(radar, test_marker=False)
        self.assertTrue(out.startswith('🧪'))

    def test_live_mode_without_flag_has_no_marker(self):
        radar = self.make_radar([], mode='live')
        out = format_daily_summary(radar, test_marker=False)
        self.assertFalse(out.startswith('🧪'))

    def test_no_actionable_shows_message(self):
        radar = self.make_radar([{
            'symbol': '3661', 'name': '世芯-KY', 'quote': {}, 'valuation': None,
            'signal': {'status': 'pending', 'status_label': '待估值', 'suggested': None, 'action': ''},
        }])
        out = format_daily_summary(radar, now=NOW)
        self.assertIn('無符合且資料有效的可行動標的', out)
        self.assertIn('待估值1', out)

    def test_actionable_listed(self):
        radar = self.make_radar([{
            'symbol': '3661', 'name': '世芯-KY', 'quote': actionable_quote(),
            'valuation': actionable_valuation(),
            'signal': actionable_signal(),
        }])
        out = format_daily_summary(radar, now=NOW)
        self.assertIn('可行動標的', out)
        self.assertIn('世芯-KY', out)

    def test_actionable_but_expired_moves_to_other_counts_not_listed(self):
        radar = self.make_radar([{
            'symbol': '3661', 'name': '世芯-KY', 'quote': actionable_quote(),
            'valuation': actionable_valuation(),
            'signal': actionable_signal(now=NOW - timedelta(hours=2)),
        }])
        out = format_daily_summary(radar, now=NOW)
        self.assertIn('無符合且資料有效的可行動標的', out)
        self.assertNotIn('可行動標的：', out)

    def test_health_blocked_surfaced(self):
        radar = self.make_radar([], health=[{'name': '試撮', 'status': 'blocked', 'detail': '無試撮', 'as_of': None}])
        out = format_daily_summary(radar, now=NOW)
        self.assertIn('⚠️', out)
        self.assertIn('試撮', out)


class TestLookupReply(unittest.TestCase):
    def test_no_match(self):
        self.assertIn('查無', lookup_reply([]))

    def test_single_match_returns_status_line(self):
        item = {
            'symbol': '3661', 'name': '世芯-KY',
            'quote': {'previous_close': 3905.0, 'trial_price': None},
            'valuation': None,
            'signal': {'status': 'pending', 'status_label': '待估值', 'suggested': None, 'action': ''},
        }
        out = lookup_reply([item], now=NOW)
        self.assertIn('3661', out)

    def test_ambiguous_match_lists_options_not_guess(self):
        items = [
            {'symbol': '2330', 'name': '台積電A', 'kind': 'stock'},
            {'symbol': '2330A', 'name': '台積電B', 'kind': 'stock'},
        ]
        out = lookup_reply(items)
        self.assertIn('找到多筆', out)
        self.assertIn('2330', out)
        self.assertIn('2330A', out)


if __name__ == '__main__':
    unittest.main()
