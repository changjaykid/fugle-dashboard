"""TPEx (OTC exchange) open data access via subprocess curl.

Verified 2026-09-10: Python's `requests`/`ssl` module fails to verify
www.tpex.org.tw's certificate chain (CERTIFICATE_VERIFY_FAILED: self-signed
certificate in certificate chain / missing Subject Key Identifier, confirmed
via `openssl s_client` on the live cert). System `curl` (SecureTransport
backend on this macOS host) verifies and accepts the same certificate
(`SSL_verify_result:0`, HTTP 200), 2/2 repeat attempts succeeded. This is a
gap between Python's bundled trust/verification path and the OS trust
store, not a genuinely invalid certificate -- so per instruction we do NOT
disable certificate verification in Python. Instead we shell out to the
system `curl` binary, which already does full verification, and treat any
non-zero exit code as a hard failure (never silently returns stale/empty
data).

If a future Python/OpenSSL update on this host resolves the verification
gap, this module can be simplified to a direct `requests.get`; until then,
this is the one exception to "always use the requests library directly"
documented here explicitly rather than silently applied.
"""
from __future__ import annotations

import json
import subprocess

TPEX_BASE = 'https://www.tpex.org.tw/openapi/v1'
CURL_TIMEOUT_S = 30


def _curl_json(path: str) -> object:
    url = f'{TPEX_BASE}/{path}'
    try:
        proc = subprocess.run(
            ['curl', '-sS', '--fail', '--max-time', str(CURL_TIMEOUT_S), url],
            capture_output=True, text=True, timeout=CURL_TIMEOUT_S + 5,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'tpex curl timed out for {url}') from exc
    if proc.returncode != 0:
        raise RuntimeError(f'tpex curl failed (exit {proc.returncode}) for {url}: {proc.stderr.strip()[:300]}')
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'tpex curl returned non-JSON for {url}: {proc.stdout[:200]!r}') from exc


def fetch_otc_daily_close() -> dict[str, dict]:
    """All-OTC daily close quotes in one call. Returns {symbol: row_dict}."""
    data = _curl_json('tpex_mainboard_daily_close_quotes')
    out = {}
    for row in data:
        code = row.get('SecuritiesCompanyCode') or row.get('Code')
        if not code:
            continue
        out[code] = row
    return out
