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
from datetime import datetime, timedelta
from typing import Iterable

from .domain import TW, stamp

DISCORD_CHANNEL = '1493898877970153532'
API_BASE = 'https://discord.com/api/v10'
MESSAGE_CHAR_LIMIT = 1900  # leave headroom under Discord's 2000 hard cap
# Never let a research-text symbol/name (or an accidental '@everyone'/'@here'
# substring) actually ping anyone. Every outbound message uses this.
NO_PING = {'parse': []}


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
    truncation. allowed_mentions is always locked to no-ping (NO_PING) --
    a research thesis or symbol name must never be able to @mention
    anyone, and callers cannot opt out of this."""
    import requests
    http = session or requests
    resp = http.post(f'{API_BASE}/channels/{channel_id}/messages',
                     json={'content': text, 'allowed_mentions': NO_PING},
                     headers=_headers(bot_token), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _hard_split(line: str, limit: int) -> list[str]:
    """Split a single line that is itself longer than `limit` into
    limit-sized pieces. Needed because the newline-based chunker in
    chunk_text() cannot shrink one already-too-long line -- without this,
    a single overlong line (e.g. an unbroken URL or a long thesis string)
    would be emitted as-is and Discord would reject the whole message with
    a 400, or the caller's naive char-count math would undercount."""
    return [line[i:i + limit] for i in range(0, len(line), limit)] or ['']


def chunk_text(text: str, limit: int = MESSAGE_CHAR_LIMIT) -> list[str]:
    """Split on newline boundaries where possible so a table/list row is
    never cut mid-line; any single line still longer than `limit` after
    that (rare, but must not silently produce an over-limit chunk or crash
    the send) gets hard-split by character count."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = []
    current_len = 0
    for line in text.split('\n'):
        if len(line) > limit:
            if current:
                chunks.append('\n'.join(current))
                current, current_len = [], 0
            chunks.extend(_hard_split(line, limit))
            continue
        add_len = len(line) + 1
        if current_len + add_len > limit and current:
            chunks.append('\n'.join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += add_len
    if current:
        chunks.append('\n'.join(current))
    return chunks


def _revalidate_signal(item: dict, now: datetime) -> dict:
    """Re-check a signal's own expiry/date fields against the actual send
    time before formatting -- radar.json may have been generated some time
    before this function runs (export -> notify-summary can be two
    separate cron steps), and per RADAR_DATA_CONTRACT.md any positive
    signal past valid_until or off today's date must have its price
    hidden, not just at frontend render time. Returns a possibly-downgraded
    copy of the signal dict; never upgrades a non-actionable status."""
    sig = dict(item.get('signal') or {})
    if sig.get('status') in ('sweet', 'add', 'buy'):
        expires = stamp(sig.get('valid_until'))
        calc = stamp(sig.get('calculated_at'))
        if not expires or now >= expires or not calc or calc.date() != now.date():
            sig.update(status='stale', status_label='資料過期', suggested=None,
                      conservative=None, extreme=None,
                      reason='掛價已過效期或跨日，發送前重新檢查已隱藏', action='請重新查詢最新雷達資料')
    return sig


def format_status_line(item: dict, *, now: datetime | None = None) -> str:
    """One line per instrument for the daily summary, per spec's main-table
    fields: 名稱代號、昨收、試撮、今日掛價、甜甜/加碼/買進、動作與狀態.
    Always re-validates the signal's own expiry against `now` (see
    _revalidate_signal) before rendering, rather than trusting whatever
    status was baked into the radar.json payload passed in."""
    now = (now or datetime.now(TW))
    q = item.get('quote') or {}
    sig = _revalidate_signal(item, now)
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


def format_daily_summary(radar_json: dict, *, test_marker: bool = False, now: datetime | None = None) -> str:
    """Build the main Discord summary text per spec: coverage line +
    符合且資料有效的標的 list. Symbols still pending/blocked/stale are
    counted but not spelled out one-by-one (spec: 只列出「符合且資料有效」的
    標的，另外只顯示統計數字給 待估值/暫停/資料不足）.

    Two things this function decides for itself rather than trusting the
    caller:
    - test_marker is forced True whenever radar_json['mode'] == 'simulation',
      regardless of what the caller passed. A caller cannot accidentally
      (or by a future bug) send simulation data as a live message just by
      omitting a flag -- the mode field in the data itself is authoritative.
    - Each actionable item's signal is re-validated against `now` (real
      send time) via format_status_line/_revalidate_signal, not against
      whatever time the radar.json was generated at.
    """
    now = now or datetime.now(TW)
    cov = radar_json['coverage']
    generated_at = radar_json['generated_at']
    is_test = bool(test_marker) or radar_json.get('mode') == 'simulation'
    header_prefix = '🧪【測試訊息，非即時交易建議】\n' if is_test else ''
    lines = [
        f"{header_prefix}台股雷達 {radar_json.get('market_date')} {generated_at[11:16]}",
        f"涵蓋率：全市場{cov['universe']}檔（股票{cov['stocks']}/ETF{cov['etfs']}），"
        f"已取得報價{cov['quotes']}檔，已完成估值{cov['valued']}檔",
    ]
    revalidated = [(i, _revalidate_signal(i, now)) for i in radar_json['items']]
    actionable = [i for i, sig in revalidated if sig['status'] in ('sweet', 'add', 'buy')]
    other_counts = {}
    for i, sig in revalidated:
        s = sig['status']
        if s not in ('sweet', 'add', 'buy'):
            other_counts[s] = other_counts.get(s, 0) + 1
    if actionable:
        lines.append('')
        lines.append('可行動標的：')
        for item in actionable:
            lines.append(format_status_line(item, now=now))
    else:
        lines.append('目前無符合且資料有效的可行動標的。')
    if other_counts:
        label_map = {'pending': '待估值', 'blocked': '暫停', 'stale': '資料過期/不足', 'avoid': '不追'}
        summary = '、'.join(f'{label_map.get(k, k)}{v}' for k, v in other_counts.items())
        lines.append(f'其餘：{summary}')
    for h in radar_json.get('health', []):
        if h.get('status') == 'blocked':
            lines.append(f"⚠️ {h['name']}：{h['detail']}")
    return '\n'.join(lines)


def lookup_reply(matches: list[dict], *, now: datetime | None = None) -> str:
    """Reply text for a symbol/name query. Ambiguous name -> ask user to
    disambiguate by symbol, never guess."""
    if not matches:
        return '查無此標的。'
    if len(matches) > 1:
        options = '\n'.join(f"- {m['symbol']} {m.get('name')} [{m['kind']}]" for m in matches)
        return f'找到多筆符合，請用代號指定：\n{options}'
    return format_status_line(matches[0], now=now)


def is_test_mode(radar_json: dict | None, *, explicit_test: bool = False) -> bool:
    """Single source of truth for whether a reply must carry the 🧪 test
    marker. A caller passing --test always forces it on; but critically,
    radar_json['mode'] == 'simulation' ALSO forces it on regardless of
    whether the caller remembered to pass --test. Mirrors
    format_daily_summary's own is_test logic so a single-symbol lookup
    reply cannot leak un-marked simulation data just because the caller
    (a human typing a query in Discord, or a script) forgot a flag -- the
    data's own mode field is authoritative, matching the daily-summary
    contract exactly (RADAR_DATA_CONTRACT.md: mode=simulation 必須醒目標示).
    """
    return bool(explicit_test) or bool(radar_json and radar_json.get('mode') == 'simulation')


TEST_MARKER_PREFIX = '🧪【測試查詢，非即時交易建議】'
