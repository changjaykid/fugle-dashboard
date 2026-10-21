"""Discord sends and edits for Stock Radar. Every message goes to a channel from channel_for().

The bot token is not read here (this package stays independently testable); callers pass it in.
"""
from __future__ import annotations

DISCORD_CHANNEL = '1493898877970153532'
# STOCK_AGENT.md 15 wants daily / alert / review / status split across channels. Until those channels
# exist, every kind resolves to the one approved channel: routing is configuration, not new permission.
CHANNEL_KINDS = ('daily', 'alert', 'review', 'status')


def channel_for(kind, *, config_path=None):
    """Channel id for a message kind, from stock_radar/channels.json when present."""
    import json as _json
    from pathlib import Path as _Path
    if kind not in CHANNEL_KINDS:
        raise ValueError('unknown_channel_kind')
    path = _Path(config_path) if config_path else _Path(__file__).with_name('channels.json')
    try:
        configured = _json.loads(path.read_text()).get(kind)
    except (OSError, ValueError):
        configured = None
    return configured if isinstance(configured, str) and configured.isdigit() else DISCORD_CHANNEL


API_BASE = 'https://discord.com/api/v10'
MESSAGE_CHAR_LIMIT = 1900  # leave headroom under Discord's 2000 hard cap
# Never let a research-text symbol/name (or an accidental '@everyone'/'@here'
# substring) actually ping anyone. Every outbound message uses this.
NO_PING = {'parse': []}


def _headers(bot_token: str) -> dict:
    return {'Authorization': f'Bot {bot_token}', 'User-Agent': 'DiscordBot (StockRadar, 1.0)'}


def send_message(bot_token: str, text: str, *, channel_id: str = DISCORD_CHANNEL,
                 session=None, timeout=15) -> dict:
    """Sends one message of at most MESSAGE_CHAR_LIMIT characters; a longer one is a loud Discord 400,
    never a silent truncation. Mentions are always locked off (NO_PING)."""
    import requests
    http = session or requests
    resp = http.post(f'{API_BASE}/channels/{channel_id}/messages',
                     json={'content': text, 'allowed_mentions': NO_PING}, headers=_headers(bot_token),
                     timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def edit_message(bot_token: str, message_id: str, text: str, *, channel_id: str = DISCORD_CHANNEL,
                 session=None, timeout=15) -> dict:
    """Replaces the content of one already-sent message. The morning report is a single message per
    trading day, sent by the 08:55 run; this exists so a later correction replaces it rather than
    posting a second, contradictory version. Same no-ping lock as send_message."""
    import requests
    http = session or requests
    resp = http.patch(f'{API_BASE}/channels/{channel_id}/messages/{message_id}',
                      json={'content': text, 'allowed_mentions': NO_PING}, headers=_headers(bot_token), timeout=timeout)
    resp.raise_for_status()
    return resp.json()
