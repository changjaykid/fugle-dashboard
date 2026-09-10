# Stock Radar — OpenClaw Integration Notes (WIP)

Status: scaffolding phase. No cron jobs enabled yet, no writes to production
docs/radar.json or dashboard.json from this branch.

## Host facts verified 2026-09-10
- OpenClaw CLI/Gateway: 2026.6.33, gateway running under LaunchAgent, loopback only.
- Repo path: /Users/kid/.openclaw/workspace/fugle-dashboard (main worktree, untouched).
- This branch is developed in a separate git worktree:
  /Users/kid/.openclaw/workspace/fugle-dashboard-radar (branch codex/stock-radar-v1)
  so existing cron jobs pointing at the main worktree keep running unaffected.
- Fugle marketdata API key present in fugle-dashboard/engine/config.json but
  returns 401 Unauthorized on intraday quote/ticker endpoints (verified via
  fugle_marketdata python client, 2026-09-10). Do NOT treat as usable until
  a human confirms plan/subscription status with Fugle. No purchases made.
- Discord channel 1493898877970153532 confirmed read+write reachable with the
  existing bot token (GET/POST verified 2026-09-10). This is the only channel
  stock_radar Discord features may use, per spec.
- Existing production stock cron jobs (system crontab, NOT OpenClaw cron):
  run_and_push.sh (hourly 09-14 Mon-Fri), stock_morning/evening/night/weekly
  report scripts. None of these are modified by this branch.

## Still open / unverified
- Fugle free-tier trial-match (試撮) and 五檔 (order book) entitlement: unknown,
  blocked by 401. Spec explicitly forbids treating Fugle 401 as "working" and
  forbids clock-based faking of trial price. Until resolved, live "today
  ask price" (今日掛價) features cannot go live — decide() correctly returns
  stale/blocked without a working quote feed. As of 2026-09-10 17:xx, Kid is
  applying an updated key on his side per Codex's 401-vs-403 clarification;
  re-verified still 401 with the config.json key present at that time.
- 0050 (and ETF NAV in general): no free, machine-readable NAV/IOPV feed
  found yet. Checked: TWSE OpenAPI (only static fund metadata via
  t187ap47_L, no daily NAV), TWSE www.twse.com.tw/fund/T51 (returns HTML,
  not JSON, despite response=json param), Yuanta's own NAV history page
  (https://www.yuantaetfs.com/tradeInfo/comparison/0050/NAVhistory) is a
  JS SPA backed by a bundled API client -- did not reverse-engineer the
  bundle to find the real endpoint (open task, not attempted further to
  avoid scraping something not intended for machine consumption without
  checking terms first). etf_nav valuations are NOT proposed until this is
  resolved; 0050 stays 'pending' in radar.json rather than guessing.
- OTC (上櫃) halted/disposition data: no TWSE feed covers OTC symbols (TWTAWU
  and announcement/punish are TSE-only, verified against live responses).
  stock_radar/risk.py explicitly marks OTC instruments as not-cleared
  (cleared=False) rather than fabricating a clean check.
- 全額交割 (full-cash-delivery) status: no free feed found; not checked by
  risk.py in V1 (separate gap from halted/disposition, which ARE covered).
- No purchase of any paid plan without Kid's explicit approval.

## Resolved 2026-09-10 (was open, now implemented)
- **Trading-day calendar**: replaced the manual `--assume-market-open` flag
  with a real data source. `stock_radar/calendar.py` pulls TWSE's own
  OpenAPI `holidaySchedule` feed (free, no key) and correctly distinguishes
  actual closures (放假/補假/市場無交易) from rows that merely describe an
  adjacent trading day (最後交易日/開始交易日). `cli.py sync-calendar` caches
  it in the Store; `cli.py export` reads that cache by default and fails
  CLOSED (health=blocked, not silently "open") if the cache has no data for
  the current ROC year. Known limit: the feed only serves the current ROC
  year server-side (a `?year=` param was tested live and ignored), so this
  needs re-running once a year; `--assume-market-open` is kept only as an
  explicit manual override for testing.
- **ETF misclassification**: CFI-code-only classification wrongly tagged
  leveraged/inverse (00631L/00632R) and bond (00679B/00795B) ETFs as
  'etf_equity'. Fixed in `stock_radar/universe.py` via
  `classify_etf_kinds()`, cross-checked against TWSE's own fund-type
  disclosure (t187ap47_L). ETN is now its own `kind='etn'`, excluded from
  ETF coverage counts. See git log for full detail.
- **financials source_url + period vs publish-date**: every
  `stock_radar/financials.py` fetcher now returns `source_url`; docstrings
  and comments now make explicit that 出表日期 (report_date) is only a
  publish/refresh timestamp, never the actual reporting period
  (year_roc/quarter, or period_roc_ym for monthly revenue).
