#!/bin/bash
# Fugle 行情引擎 — 開盤時每小時更新，收盤後執行最後一次
set -e

REPO="/Users/kid/.openclaw/workspace/fugle-dashboard"
LOG="$REPO/engine/run.log"
ENGINE="$REPO/engine/engine.py"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 開始掃描..." >> "$LOG"

# 假日跳過
DOW=$(date '+%u')  # 1=Mon ... 7=Sun
if [ "$DOW" -ge 6 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 假日，跳過" >> "$LOG"
  exit 0
fi

# 時間範圍：09:00~14:00 都跑
# 09:00~13:30 = 盤中即時更新
# 13:31~14:00 = 收盤後最終數據
HOUR=$(date '+%H')
HOUR_INT=$((10#$HOUR))

if [ "$HOUR_INT" -lt 9 ] || [ "$HOUR_INT" -gt 14 ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 非盤中/盤後時間（${HOUR}:xx），跳過" >> "$LOG"
  exit 0
fi

# 跑引擎
python3 "$ENGINE" scan >> "$LOG" 2>&1

# 推 GitHub
cd "$REPO"
git add docs/dashboard.json
if git diff --cached --quiet; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 無變動，不推送" >> "$LOG"
else
  git commit -m "data: 行情更新 $(date '+%H:%M')"
  git push >> "$LOG" 2>&1
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 推送完成" >> "$LOG"
fi
