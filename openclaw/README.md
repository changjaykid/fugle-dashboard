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
  stale/blocked without a working quote feed.
- No purchase of any paid plan without Kid's explicit approval.
