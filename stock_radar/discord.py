"""Discord read/write for Stock Radar.

Per STOCK_RADAR_SPEC.md: the ONLY authorized channel is 1493898877970153532.
Reads are allowed for anyone in that channel; writes (apply valuation) only
for a Discord-verified sender in ALLOWED_ACTORS (see cli.py) AND only when
the message's real channel id equals the authorized channel -- never trust
a channel/user claimed inside the message text itself.

Bot token is NOT hardcoded here and NOT read from OpenClaw's config format
directly (this package stays independently portable/testable); callers
(the operational wrapper script, run under OpenClaw) pass it in.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Iterable

from .domain import TW

DISCORD_CHANNEL = '1493898877970153532'
API_BASE = 'https://discord.com/api/v10'
MESSAGE_CHAR_LIMIT = 1900  # leave headroom under Discord's 2000 hard cap


def _headers(bot_token: str) -> dict:
    return {'Authorization': f'Bot {bot_token}', 'User-Agent': 'DiscordBot (StockRadar, 1.0)'}


def fetch_recent_messages(bot_token: str, *, channel_id: str = DISCORD_CHANNEL,
                          limit: int = 20, session=None, timeout=15) -> list[dict]:
    import requests
    http = session or requests
    resp = http.get(f'{API_BASE}/channels/{channel_id}/messages',
                     params={'limit': limit}, headers=_headers(bot_token), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def send_message(bot_token: str, text: str, *, channel_id: str = DISCORD_CHANNEL,
                 session=None, timeout=15) -> dict:
    """Sends one message. Caller must pre-chunk text <= MESSAGE_CHAR_LIMIT
    using chunk_text(); this function does not chunk itself, so a caller
    forgetting to chunk gets a loud Discord 400 rather than silent
    truncation."""
    import requests
    http = session or requests
    resp = http.post(f'{API_BASE}/channels/{channel_id}/messages',
                     json={'content': text}, headers=_headers(bot_token), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def chunk_text(text: str, limit: int = MESSAGE_CHAR_LIMIT) -> list[str]:
    """Split on newline boundaries where possible so a table/list row is
    never cut mid-line."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for line in text.split('\n'):
        add_len = len(line) + 1
        if current_len + add_len > limit and current:
            chunks.append('\n'.join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += add_len
    if current:
        chunks.append('\n'.join(current))
    return chunks


def format_status_line(item: dict) -> str:
    """One line per instrument for the daily summary, per spec's main-table
    fields: 名稱代號、昨收、試撮、今日掛價、甜甜/加碼/買進、動作與狀態."""
    q = item.get('quote') or {}
    sig = item.get('signal') or {}
    v = item.get('valuation')

    def fmt(n):
        return f'{n:g}' if isinstance(n, (int, float)) else '—'

    prev = fmt(q.get('previous_close'))
    trial = fmt(q.get('trial_price'))
    ask = fmt(sig.get('suggested'))
    if v:
        sab = f"甜{fmt(v.get('sweet'))}/加{fmt(v.get('add'))}/買{fmt(v.get('buy'))}"
    else:
        sab = '尚無估值'
    status_label = sig.get('status_label', '待估值')
    action = sig.get('action', '')
    return f"{item.get('name')}({item.get('symbol')}) 昨收{prev} 試撮{trial} 掛價{ask} {sab} [{status_label}] {action}"


def format_daily_summary(radar_json: dict, *, test_marker: bool = False) -> str:
    """Build the main Discord summary text per spec: coverage line +
    符合且資料有效的標的 list. Symbols still pending/blocked/stale are
    counted but not spelled out one-by-one (spec: 只列出「符合且資料有效」的
    標的，另外只顯示統計數字給 待估值/暫停/資料不足)."""
    cov = radar_json['coverage']
    now = radar_json['generated_at']
    header_prefix = '🧪【測試訊息，非即時交易建議】\n' if test_marker else ''
    lines = [
        f"{header_prefix}台股雷達 {radar_json.get('market_date')} {now[11:16]}",
        f"涵蓋率：全市場{cov['universe']}檔（股票{cov['stocks']}/ETF{cov['etfs']}），"
        f"已取得報價{cov['quotes']}檔，已完成估值{cov['valued']}檔",
    ]
    actionable = [i for i in radar_json['items'] if i['signal']['status'] in ('sweet', 'add', 'buy')]
    other_counts = {}
    for i in radar_json['items']:
        s = i['signal']['status']
        if s not in ('sweet', 'add', 'buy'):
            other_counts[s] = other_counts.get(s, 0) + 1
    if actionable:
        lines.append('')
        lines.append('可行動標的：')
        for item in actionable:
            lines.append(format_status_line(item))
    else:
        lines.append('目前無符合且資料有效的可行動標的。')
    if other_counts:
        label_map = {'pending': '待估值', 'blocked': '暫停', 'stale': '資料不足', 'avoid': '不追'}
        summary = '、'.join(f'{label_map.get(k, k)}{v}' for k, v in other_counts.items())
        lines.append(f'其餘：{summary}')
    for h in radar_json.get('health', []):
        if h.get('status') == 'blocked':
            lines.append(f"⚠️ {h['name']}：{h['detail']}")
    return '\n'.join(lines)


def lookup_reply(matches: list[dict]) -> str:
    """Reply text for a symbol/name query. Ambiguous name -> ask user to
    disambiguate by symbol, never guess."""
    if not matches:
        return '查無此標的。'
    if len(matches) > 1:
        options = '\n'.join(f"- {m['symbol']} {m.get('name')} [{m['kind']}]" for m in matches)
        return f'找到多筆符合，請用代號指定：\n{options}'
    return format_status_line(matches[0])
