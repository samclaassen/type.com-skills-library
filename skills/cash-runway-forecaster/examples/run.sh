#!/usr/bin/env bash
# Runnable example. Working directory is the skill directory.
# Every invocation prints its own exit status so the failure paths are visible.
set -u
cd "$(dirname "$0")/.."

echo "### 1. Base case: 13 weeks from 2026-09-07, 97% collection rate, 7-day lag"
python3 scripts/cash_forecast.py \
  --opening-balance 1284905.22 \
  --as-of 2026-09-07 \
  --ar examples/ar_ageing.csv \
  --ap examples/ap_ageing.csv \
  --payroll examples/payroll_schedule.csv \
  --recurring examples/recurring_commitments.csv \
  --minimum-cash 750000 \
  --collection-lag-days 7 \
  --collection-rate 97
echo "exit status: $?"

echo
echo "### 2. Downside: collections slip to 21 days, 90% collected, AP stretched 14 days, spend cut 10%"
python3 scripts/cash_forecast.py \
  --opening-balance 1284905.22 \
  --as-of 2026-09-07 \
  --ar examples/ar_ageing.csv \
  --ap examples/ap_ageing.csv \
  --payroll examples/payroll_schedule.csv \
  --recurring examples/recurring_commitments.csv \
  --minimum-cash 750000 \
  --collection-lag-days 21 \
  --collection-rate 90 \
  --payment-lag-days 14 \
  --spend-change-pct -10 \
  | sed -n '/^WEEKLY CASH FLOW/,/^COMPLETENESS/p'
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 3. First three weeks, item by item"
python3 scripts/cash_forecast.py \
  --opening-balance 1284905.22 \
  --as-of 2026-09-07 \
  --ar examples/ar_ageing.csv \
  --ap examples/ap_ageing.csv \
  --payroll examples/payroll_schedule.csv \
  --recurring examples/recurring_commitments.csv \
  --weeks 3 \
  --minimum-cash 750000 \
  --collection-lag-days 7 \
  --collection-rate 97 \
  --detail \
  | sed -n '/^SCHEDULED ITEMS BY WEEK/,/^RUNWAY AND COVENANT/p'
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 4. Bad input is rejected loudly"
python3 scripts/cash_forecast.py \
  --opening-balance 1284905.22 \
  --as-of 2026-09-07 \
  --recurring examples/broken_recurring.csv 2>&1
echo "exit status: $?"

exit 0
