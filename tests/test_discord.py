"""Unit tests for stock_radar.discord: sends and edits never ping, and every kind has a channel."""
import unittest
from unittest import mock

from stock_radar.discord import send_message, NO_PING


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


class EditMessageTests(unittest.TestCase):
    """The morning report is one message a day: 08:55 must replace 08:50's content, never post a second."""

    class _Session:
        def __init__(self):
            self.calls = []

        def patch(self, url, json=None, headers=None, timeout=None):
            self.calls.append((url, json))

            class R:
                status_code = 200

                @staticmethod
                def raise_for_status():
                    return None

                @staticmethod
                def json():
                    return {'id': '42', 'content': json['content']}
            return R()

    def test_edit_targets_the_message_and_keeps_mentions_locked(self):
        from stock_radar.discord import edit_message, NO_PING, DISCORD_CHANNEL
        session = self._Session()
        out = edit_message('token', '42', '新內容', session=session)
        url, payload = session.calls[0]
        self.assertTrue(url.endswith(f'/channels/{DISCORD_CHANNEL}/messages/42'))
        self.assertEqual(payload['content'], '新內容')
        self.assertEqual(payload['allowed_mentions'], NO_PING)
        self.assertEqual(out['id'], '42')


class ChannelRoutingTests(unittest.TestCase):
    """Reports can be split per STOCK_AGENT.md 15; an unconfigured kind stays on the approved channel."""

    def test_kinds_fall_back_and_configured_ids_are_used(self):
        import json, tempfile
        from pathlib import Path
        from stock_radar.discord import channel_for, DISCORD_CHANNEL
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'channels.json'
            path.write_text(json.dumps({'daily': '123456', 'alert': None, 'review': 'not-an-id'}))
            self.assertEqual(channel_for('daily', config_path=path), '123456')
            self.assertEqual(channel_for('alert', config_path=path), DISCORD_CHANNEL)
            self.assertEqual(channel_for('review', config_path=path), DISCORD_CHANNEL)
            self.assertEqual(channel_for('status', config_path=path), DISCORD_CHANNEL)
            self.assertEqual(channel_for('daily', config_path=Path(folder) / 'missing.json'), DISCORD_CHANNEL)
        with self.assertRaises(ValueError):
            channel_for('trading')
