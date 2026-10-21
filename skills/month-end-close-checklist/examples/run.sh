#!/usr/bin/env bash
# Runnable example. Working directory is the skill directory.
# Every invocation prints its own exit status so the failure paths are visible.
set -u
cd "$(dirname "$0")/.."

echo "### 1. Close status on business day 5, tie-outs clean"
python3 scripts/close_status.py \
  --tasks examples/sample_tasks.csv \
  --tieouts examples/sample_tieouts.csv \
  --period-end 2026-08-31 \
  --as-of 2026-09-08 \
  --target-close-days 8 \
  --holidays 2026-09-07
echo "exit status: $?"

echo
echo "### 2. Same close with an unresolved difference and an unevidenced balance"
python3 scripts/close_status.py \
  --tasks examples/sample_tasks.csv \
  --tieouts examples/sample_tieouts_open.csv \
  --period-end 2026-08-31 \
  --as-of 2026-09-08 \
  --target-close-days 8 \
  --holidays 2026-09-07 \
  | sed -n '/^TIE-OUT LOG/,$p'
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 3. Bad input is rejected loudly"
python3 scripts/close_status.py \
  --tasks examples/broken_tasks.csv \
  --period-end 2026-08-31 \
  --as-of 2026-09-08 2>&1
echo "exit status: $?"

exit 0
